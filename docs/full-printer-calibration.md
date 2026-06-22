# Native printer calibration

On a supported printer card, open **Controls** and select **Calibration**.
Choose one or more stages exposed for that model; Bambuddy does not offer raw
MQTT masks or unsupported options. The control is enabled while the printer is
connected, `IDLE` or `FINISH`, and not already running the native calibration
program. `FINISH` requires an additional checkbox confirming that the build
plate is clear. That confirmation permits calibration only; it does not clear
Bambuddy's separate print-queue plate-clear acknowledgment.

The toolhead and bed move, the routine can be loud, and it may take several
minutes. Keep the build plate installed correctly and clear the printer area
before starting.

The command requires LAN-mode MQTT connectivity. A successful request means
the QoS-1 MQTT publish was accepted by the client; it does not mean calibration
has completed. The printer card follows live MQTT status instead. Calibration is
shown as active only when the printer reports Bambu's `auto_cali_for_user`
program with a valid `stg_cur` value, and it clears again once the state returns
to idle.

## Protocol and model support

The payload is Bambu Studio's native `print.calibration` command, not arbitrary
G-code:

```json
{
  "print": {
    "command": "calibration",
    "sequence_id": "<incrementing sequence>",
    "option": "<model-specific bitmask>"
  }
}
```

Every publish uses MQTT QoS 1. Bambu Studio defines the mask as: Micro Lidar
`1`, bed leveling `2`, vibration compensation `4`, motor-noise cancellation
`8`, nozzle-offset `16`, high-temperature-bed `32`, and clump-position `64`.
The selected stages are ORed into the native option value. The command
construction is visible in [Bambu Studio DeviceManager.cpp](https://github.com/bambulab/BambuStudio/blob/5875ec284a397703edf38eb8ee9a3903ea99a09f/src/slic3r/GUI/DeviceManager.cpp#L1892-L1913); the model flags are the matching files in [Bambu Studio's printer profiles](https://github.com/bambulab/BambuStudio/tree/5875ec284a397703edf38eb8ee9a3903ea99a09f/resources/printers).

| Models | Native `option` | Included Studio-default stages |
| --- | ---: | --- |
| X1, X1 Carbon, X1E | 7 | Micro Lidar, bed leveling, vibration |
| P1P, P1S | 6 | Bed leveling, vibration |
| A1, A1 Mini | 14 | Bed leveling, vibration, motor noise |
| P2S | 102 | Bed leveling, vibration, high-temperature bed, clump position |
| H2D, H2D Pro | 54 | Bed leveling, vibration, nozzle offset, high-temperature bed |
| H2S | 102 | Bed leveling, vibration, high-temperature bed, clump position |

Model names and Bambu internal IDs are normalized in one backend capability
table. H2C is intentionally unavailable: Bambu Studio's profile does not yet
provide a verified full-calibration option set for its Vortek configuration in
the source revision above. Unknown or future models are also unavailable rather
than receiving an assumed H2-family payload.

## Manual hardware test

Do not use a production print for the first test. With a supported printer in
LAN mode, install the correct build plate and clear the bed and toolhead travel
area. Choose **Calibration**, select one or more supported stages, and confirm
the warning. If the printer reports `FINISH`, also check the explicit
build-plate-clear confirmation. Verify that Bambuddy sends only the selected
stages' option bits at QoS 1, the printer starts its native routine, and the
card changes to *Calibration in progress* from live MQTT data. Test an empty
selection, a busy/paused printer, a disconnected printer, and an unsupported
model separately; each must reject without publishing MQTT.
