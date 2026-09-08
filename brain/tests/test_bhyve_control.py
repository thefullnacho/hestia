"""A strict fake manifold checks that only status and manual-mode frames are sent."""
import asyncio
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace as NS

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
spec = importlib.util.spec_from_file_location("bhyve_control", DEPLOY / "bhyve_control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
protocol = control.protocol

KEY = bytes(range(16))
ADDRESS = "AA:BB:CC:DD:EE:FF"
IDENTITY = bytes.fromhex(ADDRESS.replace(":", ""))


def status_reply(run_state, zone=None, remaining=None):
    detail = b""
    if zone is not None:
        detail += bytes((0x20, zone - 1))
    if remaining is not None:
        detail += b"\x38" + control.encode_varint(remaining)
    inner = bytes((0x08, run_state))
    if detail:
        inner += bytes((0x32, len(detail))) + detail
    return protocol.envelope(b"\x0a\x06" + IDENTITY + b"\x82\x01" + bytes((len(inner),)) + inner)


IDLE = status_reply(1)


def parse_manual(message):
    """Recover mode, zone and duration from a built command."""
    for number, wire, value in protocol.fields(message[6:-2]):
        assert (number, wire) == (14, 2)
        mode = zone = seconds = None
        for inner_number, _, inner in protocol.fields(value):
            if inner_number == 1:
                mode = inner
            else:
                assert inner_number == 2
                for run_number, _, run in protocol.fields(inner):
                    assert run_number == 3
                    for field, _, item in protocol.fields(run):
                        if field == 1:
                            zone = item + 1
                        elif field == 2:
                            seconds = item
        return mode, zone, seconds
    raise AssertionError("Empty command")


def test_built_frames_match_the_ones_verified_on_hardware():
    assert control.manual_command(4, 60)[6:-2].hex() == "720a080212061a040803103c"
    assert control.manual_command(4, 0)[6:-2].hex() == "720a080212061a0408031000"


@pytest.mark.parametrize("seconds", [0, 1, 60, 127, 128, 300, 600, control.MAX_SECONDS])
@pytest.mark.parametrize("zone", range(1, control.ZONE_COUNT + 1))
def test_durations_past_one_byte_round_trip(zone, seconds):
    assert parse_manual(control.manual_command(zone, seconds)) == (2, zone, seconds)


def test_long_duration_lengths_are_recomputed_not_fixed():
    short, long = control.manual_command(3, 60), control.manual_command(3, 600)
    assert b"\x10\xd8\x04" in long and len(long) == len(short) + 1


@pytest.mark.parametrize("zone,seconds", [
    (0, 60), (5, 60), (-1, 60), (1, -1), (1, control.MAX_SECONDS + 1), (1, 86400)])
def test_impossible_requests_are_refused(zone, seconds):
    with pytest.raises(ValueError):
        control.manual_command(zone, seconds)


@pytest.mark.parametrize("seconds", [60.0, True, "60"])
def test_non_integer_durations_are_refused(seconds):
    with pytest.raises(ValueError):
        control.manual_command(1, seconds)


def manifold(replies, sent, zone_allowed):
    """A device that decrypts every write and rejects anything unexpected."""
    class Scanner:
        @staticmethod
        async def find_device_by_address(address, timeout):
            return NS(address=address) if address == ADDRESS else None

    class Client:
        def __init__(self, device, *, timeout, pair):
            assert pair is False
            self.services = [NS(characteristics=[NS(uuid=u) for u in (
                protocol.AES_CHAR, protocol.WRITE_CHAR, protocol.NOTIFY_CHAR)])]
            self.buffer = bytearray()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            sent.append("disconnect")

        async def start_notify(self, characteristic, callback):
            assert characteristic == protocol.NOTIFY_CHAR
            self.callback = callback

        async def read_gatt_char(self, characteristic):
            assert characteristic == protocol.AES_CHAR
            return bytes(20)

        async def write_gatt_char(self, characteristic, data, *, response):
            if characteristic == protocol.AES_CHAR:
                assert response is True and len(data) == 20 and data[11] == 0
                self.iv = bytes(4) + data[4:12]
                self.tx = struct.unpack("<I", data[12:16])[0]
                self.rx = struct.unpack("<I", data[16:20])[0]
                return
            assert characteristic == protocol.WRITE_CHAR and response is False
            size = data[1]
            chunk = protocol.crypt(KEY, self.iv, self.tx, data[2:-2])
            assert struct.unpack("<H", data[-2:])[0] == (sum(chunk) + 0x11 + size) & 0xffff
            self.tx = (self.tx + 1) & 0xffffffff
            self.buffer.extend(chunk)
            if len(self.buffer) < 6 or len(self.buffer) != self.buffer[4] + 6:
                return
            message, self.buffer = bytes(self.buffer), bytearray()
            if message == protocol.STATUS_REQUEST:
                sent.append("status")
                if replies:
                    reply = replies.pop(0)
                    self.callback(None, bytes((0x11, len(reply))) + protocol.crypt(
                        KEY, self.iv, self.rx, reply) + struct.pack(
                        "<H", (sum(reply) + 0x11 + len(reply)) & 0xffff))
                    self.rx = (self.rx + 1) & 0xffffffff
                return
            mode, zone, seconds = parse_manual(message)
            assert mode == 2 and zone == zone_allowed, "Unexpected command"
            sent.append(f"run{seconds}")
    return Scanner, Client


def drive(replies, *, zone=3, allowed=None, **kwargs):
    sent, events = [], []
    scanner, client = manifold(list(replies), sent, allowed or zone)
    control.EXPIRY_GRACE_SECONDS = 0
    result = asyncio.run(control.water(
        scanner, client, ADDRESS, KEY, zone=zone, report=lambda e, **d: events.append(e),
        wait_seconds=0, poll_seconds=1, **kwargs))
    return result, sent, events


def test_a_run_that_expires_sends_no_stop():
    watering = status_reply(4, zone=3, remaining=1)
    result, sent, events = drive([IDLE, watering, watering, IDLE], seconds=1)
    assert result == {"result": "completed", "start_confirmed": True,
                      "idle_confirmed": True, "stopped_explicitly": False}
    assert sent == ["status", "run1", "status", "status", "status", "disconnect"]
    assert "stop_fallback" not in events


def test_explicit_stop_lands_inside_the_run():
    watering = status_reply(4, zone=3, remaining=300)
    result, sent, _ = drive([IDLE, watering, watering, IDLE], seconds=300, stop_after=1)
    assert result["stopped_explicitly"] and result["idle_confirmed"]
    assert sent == ["status", "run300", "status", "status", "run0", "status", "disconnect"]


def test_a_manifold_that_is_already_busy_is_left_alone():
    result, sent, events = drive([status_reply(4, zone=2, remaining=60)], seconds=60)
    assert result == {"result": "aborted_not_idle"}
    assert sent == ["status", "disconnect"] and "start_sent" not in events


def test_unknown_or_silent_replies_never_read_as_idle():
    result, sent, _ = drive([], seconds=60)
    assert result == {"result": "aborted_not_idle"} and sent == ["status", "disconnect"]
    result, sent, _ = drive([status_reply(99)], seconds=60)
    assert result == {"result": "aborted_not_idle"}


def test_a_run_that_will_not_confirm_idle_is_stopped():
    watering = status_reply(4, zone=3, remaining=1)
    result, sent, events = drive([IDLE, watering, watering, watering, watering], seconds=1)
    assert result["idle_confirmed"] is False
    assert sent == ["status", "run1", "status", "status", "status",
                    "run0", "status", "disconnect"]
    assert events[-2:] == ["stop_fallback", "after_fallback"]


def test_the_wrong_zone_answering_aborts_the_run():
    with pytest.raises(RuntimeError):
        drive([IDLE, status_reply(4, zone=2, remaining=60)], seconds=1, zone=3, allowed=3)


def test_an_unseen_manifold_is_not_actuated():
    sent, events = [], []
    scanner, client = manifold([], sent, 3)
    result = asyncio.run(control.water(
        scanner, client, "11:22:33:44:55:66", KEY, zone=3, seconds=60,
        report=lambda e, **d: events.append(e), wait_seconds=0))
    assert result == {"result": "not_observed"} and sent == [] and events == ["not_observed"]
