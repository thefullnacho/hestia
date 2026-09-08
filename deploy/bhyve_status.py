"""Read B-hyve status over BLE without changing clocks, programs, or valves.

Needs bleak and cryptography in an isolated environment. Protocol reference:
https://github.com/evanscastonguay/bhyve-xd-ble (MIT).
This implements only a session handshake and get-status, never its arming sequence.
"""
from __future__ import annotations

import argparse
import asyncio
import binascii
import json
import os
from pathlib import Path
import struct

AES_CHAR = "00006c71-fe32-4f58-8b78-98e42b2c047f"
WRITE_CHAR = "00006c72-fe32-4f58-8b78-98e42b2c047f"
NOTIFY_CHAR = "00006c73-fe32-4f58-8b78-98e42b2c047f"
HEADER = bytes.fromhex("aa775a0f")


def envelope(body):
    raw = HEADER + bytes((len(body) + 2, 0)) + body
    return raw + struct.pack("<H", binascii.crc_hqx(raw, 0))


STATUS_REQUEST = envelope(bytes.fromhex("7a00"))


def crypt(key, iv, counter, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    output = bytearray()
    for i in range(0, len(data), 16):
        block = iv + struct.pack("<I", counter & 0xffffffff)
        stream = Cipher(algorithms.AES(key), modes.ECB()).encryptor().update(block)
        output.extend(a ^ b for a, b in zip(data[i:i + 16], stream))
        counter += 1
    return bytes(output)


def varint(data, position):
    value = 0
    for shift in range(0, 70, 7):
        byte = data[position]
        position += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
    raise ValueError("Oversized varint")


def fields(data):
    position = 0
    while position < len(data):
        tag, position = varint(data, position)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid field")
        if wire == 0:
            value, position = varint(data, position)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, position = varint(data, position)
            else:
                size = 8 if wire == 1 else 4
            if position + size > len(data):
                raise ValueError("Truncated field")
            value = data[position:position + size]
            position += size
        else:
            raise ValueError("Unsupported wire type")
        yield number, wire, value


def parse_status(raw, address):
    result = {"state": "unknown", "run_state": None,
              "seconds_remaining": None, "active_zone": None, "device_time": None}
    for number, wire, value in fields(raw[6:-2]):
        if number == 1 and wire == 2:
            if len(value) != 6 or value.hex().casefold() != address.replace(":", "").casefold():
                raise ValueError("Device identity mismatch")
        elif number == 7 and wire == 0:
            result["device_time"] = value
        elif number == 16 and wire == 2:
            for sub, sub_wire, item in fields(value):
                if sub == 1 and sub_wire == 0:
                    result["run_state"] = item
                    result["state"] = {1: "idle", 4: "watering"}.get(item, "unknown")
                elif sub == 6 and sub_wire == 2:
                    for detail, detail_wire, detail_value in fields(item):
                        if detail_wire == 0 and detail == 4:
                            result["active_zone"] = detail_value + 1
                        elif detail_wire == 0 and detail == 7:
                            result["seconds_remaining"] = detail_value
    return result if result["run_state"] is not None else None


def decode_response(frame, key, iv, initial_rx, address):
    # Unknown or fragmented framing is inconclusive, never an idle reading.
    if len(frame) < 6 or frame[0] != 0x11 or len(frame) != frame[1] + 4:
        return None
    size = frame[1]
    for offset in range(-4, 128):
        raw = crypt(key, iv, initial_rx + offset, frame[2:-2])
        if len(raw) < 8 or not raw.startswith(HEADER):
            continue
        if struct.unpack("<H", frame[-2:])[0] != (sum(raw) + 0x11 + size) & 0xffff:
            continue
        if raw[5] != 0 or len(raw) != raw[4] + 6:
            continue
        if struct.unpack("<H", raw[-2:])[0] != binascii.crc_hqx(raw[:-2], 0):
            continue
        try:
            return parse_status(raw, address)
        except (ValueError, IndexError):
            return None
    return None


async def read_status(scanner, client_class, address, key, *, scan_seconds=30, wait_seconds=5):
    device = await scanner.find_device_by_address(address, timeout=scan_seconds)
    if device is None:
        return {"result": "not_observed"}
    notifications = []
    async with client_class(device, timeout=12, pair=False) as client:
        available = {c.uuid.lower() for s in client.services for c in s.characteristics}
        if not {AES_CHAR, WRITE_CHAR, NOTIFY_CHAR} <= available:
            return {"result": "missing_characteristics"}
        def collect(_sender, data):
            if len(notifications) < 64 and len(data) <= 259:
                notifications.append(bytes(data))
        await client.start_notify(NOTIFY_CHAR, collect)
        handshake = bytearray(os.urandom(20))
        handshake[11] = 0
        await client.write_gatt_char(AES_CHAR, bytes(handshake), response=True)
        response = bytes(await client.read_gatt_char(AES_CHAR))
        if len(response) != 20:
            return {"result": "invalid_handshake"}
        iv = response[:4] + bytes(handshake[4:12])
        tx = struct.unpack("<I", handshake[12:16])[0]
        rx = struct.unpack("<I", handshake[16:20])[0]
        notifications.clear()
        # Only this fixed get-status payload may be transmitted. It fits one chunk.
        encrypted = crypt(key, iv, tx, STATUS_REQUEST)
        frame = bytes((0x11, len(encrypted))) + encrypted + struct.pack(
            "<H", (sum(STATUS_REQUEST) + 0x11 + len(encrypted)) & 0xffff)
        await client.write_gatt_char(WRITE_CHAR, frame, response=False)
        await asyncio.sleep(wait_seconds)
        for notification in reversed(notifications):
            status = decode_response(notification, key, iv, rx, address)
            if status is not None:
                return {"result": "validated_status", **status}
        return {"result": "inconclusive", "notifications": len(notifications)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path,
                        help="Owner-only JSON containing address and network_key (32 hex characters)")
    args = parser.parse_args()
    try:
        if args.key_file.stat().st_mode & 0o077:
            raise ValueError("Key file must have owner-only permissions")
        config = json.loads(args.key_file.read_text())
        key = bytes.fromhex(config["network_key"])
        address = config["address"]
        if len(key) != 16 or len(bytes.fromhex(address.replace(":", ""))) != 6:
            raise ValueError("Invalid credentials")
        from bleak import BleakClient, BleakScanner
        result = asyncio.run(asyncio.wait_for(
            read_status(BleakScanner, BleakClient, address, key), timeout=55))
    except Exception as exc:
        # Never print exception text, config, keys, or raw Bluetooth traffic.
        print(json.dumps({"result": "failed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["result"] == "validated_status" else 1


if __name__ == "__main__":
    raise SystemExit(main())
