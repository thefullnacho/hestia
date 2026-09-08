"""Start, stop and watch one B-hyve zone over BLE. Actuation requires --confirm-water.

Manual-mode commands are built from a zone and a duration, so durations above 127
seconds encode as multi-byte varints instead of the fixed frames used during early
testing. This file contains no clock, program, provisioning or arming command, and
transmits nothing but a session handshake, get-status, and manual-mode run or stop.
Protocol reference: https://github.com/evanscastonguay/bhyve-xd-ble (MIT).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bhyve_status as protocol

ZONE_COUNT = 4
# A hard ceiling on any single run. The device also expires on its own.
MAX_SECONDS = 1800
# Allowed slack between a requested duration and the idle reading that should follow.
EXPIRY_GRACE_SECONDS = 8


def encode_varint(value):
    if value < 0:
        raise ValueError("Negative varint")
    output = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        output.append(byte | 128 if value else byte)
        if not value:
            return bytes(output)


def tag(number, wire):
    return encode_varint(number << 3 | wire)


def submessage(number, body):
    return tag(number, 2) + encode_varint(len(body)) + body


def manual_command(zone, seconds):
    """A manual-mode run for one zone. A duration of zero is the stop command."""
    if zone not in range(1, ZONE_COUNT + 1):
        raise ValueError("Zone out of range")
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise ValueError("Duration must be an integer")
    if not 0 <= seconds <= MAX_SECONDS:
        raise ValueError("Duration out of range")
    run = tag(1, 0) + encode_varint(zone - 1) + tag(2, 0) + encode_varint(seconds)
    body = tag(1, 0) + encode_varint(2) + submessage(2, submessage(3, run))
    return protocol.envelope(submessage(14, body))


class Session:
    """One authenticated connection. Only whitelisted message shapes can be sent."""

    def __init__(self, client, key, address):
        self.client = client
        self.key = key
        self.address = address
        self.frames = []

    async def open(self):
        available = {c.uuid.lower() for s in self.client.services for c in s.characteristics}
        if not {protocol.AES_CHAR, protocol.WRITE_CHAR, protocol.NOTIFY_CHAR} <= available:
            raise RuntimeError("Missing characteristics")
        await self.client.start_notify(protocol.NOTIFY_CHAR, self._collect)
        handshake = bytearray(os.urandom(20))
        handshake[11] = 0
        await self.client.write_gatt_char(protocol.AES_CHAR, bytes(handshake), response=True)
        response = bytes(await self.client.read_gatt_char(protocol.AES_CHAR))
        if len(response) != 20:
            raise RuntimeError("Invalid handshake")
        self.iv = response[:4] + bytes(handshake[4:12])
        self.tx = struct.unpack("<I", handshake[12:16])[0]
        self.rx = struct.unpack("<I", handshake[16:20])[0]

    def _collect(self, _sender, data):
        if len(self.frames) < 64 and len(data) <= 259:
            self.frames.append(bytes(data))

    async def send(self, message):
        for offset in range(0, len(message), 16):
            chunk = message[offset:offset + 16]
            encrypted = protocol.crypt(self.key, self.iv, self.tx, chunk)
            self.tx = (self.tx + 1) & 0xffffffff
            frame = bytes((0x11, len(chunk))) + encrypted + struct.pack(
                "<H", (sum(chunk) + 0x11 + len(chunk)) & 0xffff)
            await asyncio.wait_for(
                self.client.write_gatt_char(protocol.WRITE_CHAR, frame, response=False), timeout=3)
        await asyncio.sleep(0.12)

    async def query(self, wait_seconds=1.0):
        self.frames.clear()
        await self.send(protocol.STATUS_REQUEST)
        await asyncio.sleep(wait_seconds)
        for frame in reversed(self.frames):
            status = protocol.decode_response(frame, self.key, self.iv, self.rx, self.address)
            if status is not None:
                return status
        return None


def is_idle(status):
    return bool(status and status["state"] == "idle" and status["active_zone"] is None)


async def water(scanner, client_class, address, key, *, zone, seconds, report,
                stop_after=None, poll_seconds=10, scan_seconds=30, wait_seconds=1.0):
    """Run one zone, watching it throughout, and leave the manifold idle."""
    device = await scanner.find_device_by_address(address, timeout=scan_seconds)
    if device is None:
        report("not_observed")
        return {"result": "not_observed"}
    async with client_class(device, timeout=12, pair=False) as client:
        session = Session(client, key, address)
        await session.open()
        baseline = await session.query(wait_seconds)
        report("before", status=baseline)
        if not is_idle(baseline):
            report("aborted_not_idle")
            return {"result": "aborted_not_idle"}
        started = False
        confirmed_idle = False
        try:
            await session.send(manual_command(zone, seconds))
            started = True
            begin = time.monotonic()
            report("start_sent", zone=zone, duration_seconds=seconds, stop_after=stop_after)
            during = await session.query(wait_seconds)
            report("during", status=during)
            if during and during["active_zone"] not in (None, zone):
                raise RuntimeError("Unexpected active zone")
            confirmed_start = bool(during and during["state"] == "watering"
                                   and during["active_zone"] == zone)
            # Poll until the explicit stop, or until the run should have expired.
            limit = stop_after if stop_after is not None else seconds + EXPIRY_GRACE_SECONDS
            while True:
                elapsed = time.monotonic() - begin
                if elapsed >= limit:
                    break
                await asyncio.sleep(min(poll_seconds, limit - elapsed))
                observed = await session.query(wait_seconds)
                report("poll", elapsed=round(time.monotonic() - begin, 1), status=observed)
            if stop_after is not None:
                await session.send(manual_command(zone, 0))
                report("stop_sent", elapsed=round(time.monotonic() - begin, 1))
            final = await session.query(wait_seconds)
            confirmed_idle = is_idle(final)
            report("final", status=final, elapsed=round(time.monotonic() - begin, 1))
            return {"result": "completed", "start_confirmed": confirmed_start,
                    "idle_confirmed": confirmed_idle,
                    "stopped_explicitly": stop_after is not None}
        finally:
            if started and not confirmed_idle:
                report("stop_fallback")
                try:
                    await session.send(manual_command(zone, 0))
                    report("after_fallback", status=await session.query(wait_seconds))
                except Exception as exc:
                    report("stop_fallback_failed", error_type=type(exc).__name__)


async def stop(scanner, client_class, address, key, *, zone, report,
               scan_seconds=30, wait_seconds=1.0):
    device = await scanner.find_device_by_address(address, timeout=scan_seconds)
    if device is None:
        report("not_observed")
        return {"result": "not_observed"}
    async with client_class(device, timeout=12, pair=False) as client:
        session = Session(client, key, address)
        await session.open()
        report("before", status=await session.query(wait_seconds))
        await session.send(manual_command(zone, 0))
        report("stop_sent", zone=zone)
        final = await session.query(wait_seconds)
        report("final", status=final)
        return {"result": "completed", "idle_confirmed": is_idle(final)}


def reporter(log_path):
    def report(event, **data):
        row = {"event": event, "observed_at": time.time(), **data}
        line = json.dumps(row)
        print(line, flush=True)
        if log_path is not None:
            handle = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(handle, "a") as log:
                log.write(line + "\n")
    return report


def load_credentials(key_file):
    if key_file.stat().st_mode & 0o077:
        raise ValueError("Key file must have owner-only permissions")
    config = json.loads(key_file.read_text())
    key = bytes.fromhex(config["network_key"])
    address = config["address"]
    if len(key) != 16 or len(bytes.fromhex(address.replace(":", ""))) != 6:
        raise ValueError("Invalid credentials")
    return address, key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path,
                        help="Owner-only JSON containing address and network_key")
    parser.add_argument("--log", type=Path, help="Append JSON lines to this private file")
    parser.add_argument("--confirm-water", action="store_true",
                        help="Required acknowledgement that a valve will actually open")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("water", help="Run one zone and watch it to idle")
    run.add_argument("--zone", required=True, type=int)
    run.add_argument("--seconds", required=True, type=int)
    run.add_argument("--stop-after", type=int,
                     help="Send an explicit stop this many seconds in, instead of waiting")
    run.add_argument("--poll-seconds", type=int, default=10)
    halt = sub.add_parser("stop", help="Stop one zone now")
    halt.add_argument("--zone", required=True, type=int)
    args = parser.parse_args()
    report = reporter(args.log)
    if not args.confirm_water:
        print(json.dumps({"result": "refused", "reason": "confirm_water_required"}))
        return 1
    try:
        address, key = load_credentials(args.key_file)
        from bleak import BleakClient, BleakScanner
        if args.command == "water":
            if args.stop_after is not None and not 0 < args.stop_after < args.seconds:
                raise ValueError("Stop must fall inside the run")
            budget = (args.stop_after or args.seconds) + 120
            result = asyncio.run(asyncio.wait_for(water(
                BleakScanner, BleakClient, address, key, zone=args.zone, seconds=args.seconds,
                stop_after=args.stop_after, poll_seconds=args.poll_seconds, report=report),
                timeout=budget))
        else:
            result = asyncio.run(asyncio.wait_for(stop(
                BleakScanner, BleakClient, address, key, zone=args.zone, report=report),
                timeout=120))
    except Exception as exc:
        # Never print exception text, credentials, or raw Bluetooth traffic.
        print(json.dumps({"result": "failed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("idle_confirmed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
