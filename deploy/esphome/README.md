# ESPHome devices

Small boards that feed Home Assistant over its native API. Each one reads something local and
does its own decoding, so a board keeps working on its own screen when HA is down.

## kennel-box

A Heltec WiFi LoRa 32 V3 placed next to the whelping box. It listens for the Govee H5075 in
the box over Bluetooth and publishes box temperature, humidity and the sensor's battery to
HA. Its OLED shows the box in °F with humidity and battery underneath.

The H5075 broadcasts its readings unencrypted, so nothing here touches the Govee app or cloud.
The decode is the one HA's own `govee-ble` parser uses, checked against a live packet.

When the sensor goes quiet for 10 minutes, the HA entities go to `unknown` and the screen
switches to "No reading for N min". A dead coin cell cannot leave a stale number looking
current.

There are no temperature thresholds on the board. Safe box temperatures change week by week
after whelping, so they live in `brain/box_watch.py`, which reads the litter's age from
records and alerts from there. Changing a band never needs a reflash.

### Flashing

```bash
cp secrets.yaml.example secrets.yaml      # then fill it in (2.4 GHz Wi-Fi only)
cd deploy/esphome
uvx --from esphome==2025.12.7 esphome run kennel-box.yaml --device /dev/ttyUSB0   # first flash, over USB
uvx --from esphome==2025.12.7 esphome run kennel-box.yaml                         # later flashes, over Wi-Fi
```

Find the sensor's MAC with `bluetoothctl --timeout 20 scan on | grep GVH5075`.

ESPHome is pinned to 2025.12.7 to match Home Assistant 2025.12. ESPHome 2026.x stopped sending
the `object_id` that HA 2025.12 builds entity unique IDs from, so every sensor arrives with the
same ID and HA keeps only the first. Move the pin when HA is upgraded.

### Adding to HA

Settings, Devices and services, Add integration, ESPHome. Enter the board's IP and the
`kennel_api_key` from `secrets.yaml`. Set a DHCP reservation for the board so the address holds.

## greenhouse-door

An Elegoo ESP32 dev board on the greenhouse door frame. A reed switch reports the door and a
DS18B20 reports the air, on one board so both readings share a clock. It is test 1 of the
Homesteader Labs build loop: the number for the post is how many seconds the switch takes to
notice an open door, against how many minutes the temperature takes to show it, plus °F lost per
minute with the door open 2 inches. Wiring is in the header of `greenhouse-door.yaml`.

The board carries no alert logic, same as kennel-box. The alert is an HA automation, because it
has to fire within seconds and the brain watchers run on a schedule.

### Flashing

The keys are already in `secrets.yaml`.

```bash
cd deploy/esphome
uvx --from esphome==2025.12.7 esphome run greenhouse-door.yaml --device /dev/ttyUSB0   # first flash, over USB
```

Then add it in HA (Settings, Devices and services, Add integration, ESPHome) with the board's IP
and `greenhouse_api_key`, and set a DHCP reservation.

### The alert

Paste into HA's automations (YAML mode). The 5 seconds stops a quick in-and-out from paging.

```yaml
alias: Greenhouse door left open
triggers:
  - trigger: state
    entity_id: binary_sensor.greenhouse_door
    to: "on"
    for: "00:00:05"
actions:
  - action: notify.mobile_app_alexs_iphone
    data:
      title: Greenhouse door is open
      message: "Open since {{ as_local(trigger.to_state.last_changed).strftime('%H:%M:%S') }}. The heater is losing."
mode: single
```

### Reading the number

HA's recorder purges raw states after 10 days, so export the door and temperature history within
10 days of the test night.
