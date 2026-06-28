# Garden bed knowledge

## Beds and their sensors
Six live soil-moisture channels feed from the Ecowitt gateway. Each bed reads as a
percentage via the `home` tool (`get_state` on the entity, or read it off the soil
catalog in the system prompt). The friendly names already match the bed names.

| Bed                      | Entity                                      |
|--------------------------|---------------------------------------------|
| Beets                    | `sensor.unknown_device_soilmoisture1`       |
| Carrots                  | `sensor.unknown_device_soilmoisture2`       |
| Potatoes & Snow Peas     | `sensor.unknown_device_soilmoisture3`       |
| Tomatoes                 | `sensor.unknown_device_soilmoisture6`       |
| Hot Peppers              | `sensor.unknown_device_soilmoisture7`       |
| Artichoke & Sweet Pepper | `sensor.unknown_device_soilmoisture8`       |

## Thresholds (percent moisture)
- **Dry — needs water:** at or below **40%**.
- **Comfortable:** roughly 45–90%.
- **Saturated/waterlogged:** at or above **95%**.

## Sensor quirks — don't be fooled
- **There is no channel 5.** The gateway remembers a ghost channel 5 with no physical
  sensor. If anything reports channel 5, ignore it — it is not a real bed.
- A genuinely **dry or unplugged** WH51 sensor sits at **0%**, not a low-but-real
  number. A flat 0% can mean the bed is bone dry, but it can also mean a dead battery
  or a sensor that's been pulled. If a bed reads exactly 0%, treat it as suspect: say
  the reading looks wrong / the sensor may be down, rather than declaring a drought.
- Readings refresh every ~60s; a timestamp that looks stale is normal (the value only
  changes when the moisture changes).

## The core combine rule
Soil moisture answers "is the bed dry right now." The rain forecast answers "is water
coming anyway." A watering call needs **both**: a bed is only worth watering when it
reads dry **and** no meaningful rain is on the way. This mirrors the daily
`garden_watch` job, so the live answer and the morning alert agree.
