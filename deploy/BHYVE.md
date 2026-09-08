# B-hyve local discovery

Status: exploratory tooling only. No integration or valve control is deployed.

`bhyve_probe.py` scans advertisements for the B-hyve service. With an explicit
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
