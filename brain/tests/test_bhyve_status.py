"""Synthetic BLE replies and a strict fake device exercise the read-only path."""
import asyncio
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace as NS

import pytest

spec = importlib.util.spec_from_file_location(
    "bhyve_status", Path(__file__).resolve().parents[2] / "deploy/bhyve_status.py")
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

KEY = bytes(range(16))
IV = bytes(range(12))
ADDRESS = "AA:BB:CC:DD:EE:FF"
IDLE = status.envelope(bytes.fromhex("8201020801"))


def frame(raw, counter=9, key=KEY, iv=IV):
    return bytes((0x11, len(raw))) + status.crypt(key, iv, counter, raw) + struct.pack(
        "<H", (sum(raw) + 0x11 + len(raw)) & 0xffff)


def test_checksums_and_key_must_validate():
    good = frame(IDLE)
    assert status.decode_response(good, KEY, IV, 9, ADDRESS)["state"] == "idle"
    assert status.decode_response(good, bytes(16), IV, 9, ADDRESS) is None
    assert status.decode_response(good[:-1], KEY, IV, 9, ADDRESS) is None
    corrupt = bytearray(good)
    corrupt[-1] ^= 1
    assert status.decode_response(bytes(corrupt), KEY, IV, 9, ADDRESS) is None
    # Correct outer checksum must not hide a bad inner CRC.
    bad_crc = IDLE[:-1] + bytes((IDLE[-1] ^ 1,))
    assert status.decode_response(frame(bad_crc), KEY, IV, 9, ADDRESS) is None


def test_unknown_state_and_identity_mismatch():
    raw = status.envelope(bytes.fromhex("8201020863"))
    assert status.decode_response(frame(raw), KEY, IV, 9, ADDRESS)["state"] == "unknown"
    wrong_device = status.envelope(bytes.fromhex("0a060102030405068201020801"))
    assert status.decode_response(frame(wrong_device), KEY, IV, 9, ADDRESS) is None
    empty = status.envelope(b"")
    assert status.decode_response(frame(empty), KEY, IV, 9, ADDRESS) is None


def test_watering_fields_and_multiblock_response():
    raw = status.envelope(bytes.fromhex("0a06aabbccddeeff820109080432052003388f02"))
    decoded = status.decode_response(frame(raw), KEY, IV, 9, ADDRESS)
    assert decoded["state"] == "watering"
    assert decoded["active_zone"] == 4
    assert decoded["seconds_remaining"] == 271


@pytest.mark.parametrize("reply", [True, False])
def test_live_path_sends_only_handshake_and_status_and_disconnects(reply):
    calls = []
    class Scanner:
        @staticmethod
        async def find_device_by_address(address, timeout):
            assert address == ADDRESS
            return NS(address=address)
    class Client:
        def __init__(self, device, *, timeout, pair):
            assert pair is False
            self.services = [NS(characteristics=[NS(uuid=u) for u in (
                status.AES_CHAR, status.WRITE_CHAR, status.NOTIFY_CHAR)])]
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            calls.append("disconnect")
        async def start_notify(self, characteristic, callback):
            assert characteristic == status.NOTIFY_CHAR
            self.callback = callback
        async def write_gatt_char(self, characteristic, data, *, response):
            if characteristic == status.AES_CHAR:
                assert response is True and len(data) == 20 and data[11] == 0
                self.handshake = data
                calls.append("handshake")
            else:
                assert characteristic == status.WRITE_CHAR and response is False
                iv = bytes(4) + self.handshake[4:12]
                tx = struct.unpack("<I", self.handshake[12:16])[0]
                rx = struct.unpack("<I", self.handshake[16:20])[0]
                assert status.crypt(KEY, iv, tx, data[2:-2]) == status.STATUS_REQUEST
                calls.append("status")
                if reply:
                    self.callback(None, frame(IDLE, rx, iv=iv))
        async def read_gatt_char(self, characteristic):
            assert characteristic == status.AES_CHAR
            return bytes(20)
    result = asyncio.run(status.read_status(Scanner, Client, ADDRESS, KEY, wait_seconds=0))
    assert calls == ["handshake", "status", "disconnect"]
    assert result["result"] == ("validated_status" if reply else "inconclusive")
    if not reply:
        assert "state" not in result
