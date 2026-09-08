"""Discover B-hyve BLE services without application commands or pairing.

Run with an operator-installed bleak environment. No Orbit credentials are needed.
This only establishes radio visibility and service compatibility, not valve control.
"""
from __future__ import annotations

import argparse
import asyncio
import json

BHYVE_SERVICE = "0000fe32-0000-1000-8000-00805f9b34fb"
EXPECTED_CHARS = {
    "00006c71-fe32-4f58-8b78-98e42b2c047f": "handshake",
    "00006c72-fe32-4f58-8b78-98e42b2c047f": "command",
    "00006c73-fe32-4f58-8b78-98e42b2c047f": "notification",
}


async def probe(scanner, client_class, *, address=None, seconds=12):
    advertisements = await scanner.discover(timeout=seconds, return_adv=True)
    candidates = []
    target = None
    for device, advertisement in advertisements.values():
        services = [s.lower() for s in advertisement.service_uuids or []]
        match = address is not None and device.address.casefold() == address.casefold()
        if BHYVE_SERVICE in services or match:
            candidates.append({"address": device.address,
                               "name": advertisement.local_name or device.name,
                               "rssi": advertisement.rssi,
                               "advertised_services": services})
        if match:
            target = device
    result = {"candidates": candidates, "connected": False,
              "protocol_compatibility": "untested"}
    if address is None:
        return result
    if target is None:
        raise RuntimeError("Selected address was not observed during this scan.")
    # Use the freshly discovered device, not a second address-resolution scan.
    # Deliberately no characteristic reads, writes, notifications, or pairing.
    async with client_class(target, timeout=10, pair=False) as client:
        characteristics = {c.uuid.lower() for s in client.services for c in s.characteristics}
        result.update(connected=True,
                      expected_characteristics={label: uuid in characteristics
                                                for uuid, label in EXPECTED_CHARS.items()})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="Explicit device address to inspect after discovery")
    parser.add_argument("--seconds", type=int, default=12, choices=range(1, 31), metavar="1..30")
    args = parser.parse_args()
    from bleak import BleakClient, BleakScanner
    try:
        result = asyncio.run(asyncio.wait_for(
            probe(BleakScanner, BleakClient, address=args.address, seconds=args.seconds),
            timeout=args.seconds + 25))
    except (TimeoutError, RuntimeError, OSError) as exc:
        parser.exit(1, f"BLE discovery failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
