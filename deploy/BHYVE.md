# B-hyve local discovery

Status: exploratory discovery and read-only status tooling. No integration or valve
control is deployed.

`bhyve_probe.py` scans advertisements for the B-hyve service or a B-hyve name,
including devices that advertise no service UUIDs. A name match is only a candidate;
it does not establish whether the device is a timer or hub. With an explicit
address, it connects to that observed device and lists whether the expected GATT
characteristics exist. It does not pair, authenticate, read characteristics,
subscribe to notifications, or send application commands. Service discovery alone
does not prove that encryption, status decoding, or valve control works.

On a Bluetooth-capable host, with `bleak` installed in an isolated environment:

```sh
python deploy/bhyve_probe.py
python deploy/bhyve_probe.py --address '<address from discovery>'
```

Discovery has a bounded timeout. Keep output private: it contains device addresses.
No credentials are required. Do not commit discovery output or account keys.

## Upstream review

The upstream [bhyve-xd-ble](https://github.com/evanscastonguay/bhyve-xd-ble)
implementation documents testing on HT34A hardware with firmware 0107.
Compatibility with other firmware must be established on hardware.

Source reviewed on 2026-09-08: `BHyveXD.status()` calls `arm()`. That sequence
sets the clock and sends `SETUP_FIELD20`, documented in the code as disabling all
programs. Programs are restored only when a nonzero `device_active_mask` is
configured. The default mask is zero. Its high-level status command is therefore
not a read-only diagnostic. Do not run it against an existing irrigation setup
as a discovery check. Its self-test also changes the clock and actuates a valve.

Next stages, after discovery: review a minimal authenticated status query without
the arming sequence, establish a private credential path, then validate timed
actuation and expiry separately. An unanswered status request is inconclusive;
do not silently fall back to setup or provisioning commands.

The brain's tool set and existing irrigation configuration remain unchanged.

## Minimal local status query

`bhyve_status.py` implements only the session handshake and a fixed get-status
request. It does not import upstream executable code or contain clock, program,
provisioning, or valve commands. It requires `bleak` and `cryptography` in an
isolated environment. Pass an owner-only JSON file containing `address` and a
32-character hexadecimal `network_key`:

```sh
python deploy/bhyve_status.py --key-file '<private credential file>'
```

Credential retrieval is separate from this script. It makes no cloud requests.
Keep credentials and raw device records outside version control. Never print keys
or place them in command arguments. The script reports only status or an error type.

Responses must pass the outer checksum, envelope-length check, inner CRC, and
status-field parsing. A supplied device identity must match the selected address.
Silence, unknown framing, or malformed replies remain inconclusive. Unknown run-state
values are reported as unknown. Device time is reported as supplied, not corrected.

The code bounds the scan, connection, total session duration, and notification
buffer. The connection context closes on failure as well as success. Focused tests
cover bad keys, corrupt replies, identity mismatches, unknown states, silence,
multi-block watering responses, and the exact handshake/status-only write sequence.

## Timed actuation validation

One operator-authorized 10-second test on 2026-09-08 confirmed a transition from
idle to the requested watering zone, followed by idle after expiry. The minimal
manual-mode command worked without the upstream arming sequence. The fallback stop
was not needed and was not sent.

This establishes one start-and-expire cycle, not unattended-control reliability.
Explicit stop, interrupted connections, and repeated runs still need validation.
No general control command or automatic watering integration is deployed; the
committed status tool remains read-only.

A subsequent authorized 60-second test confirmed watering with 60 seconds reported
remaining, but the midpoint query reported idle after approximately 30 seconds.
Physical actuation was confirmed by the operator. The BLE session disconnected
before the final query; the attempted fallback stop could not be sent. A fresh
read-only connection confirmed idle. Full-minute runtime is therefore unverified.
Investigate the early idle report and connection lifetime before further actuation.
