# SpoolBuddy Hardware Setup

## PN5180 NFC Reader (SPI)

### Wiring

| PN5180 Pin | Raspberry Pi Pin | GPIO | Wire Color |
|------------|------------------|------|------------|
| 3V3        | Pin 1            | —    | Red        |
| 5V         | Pin 2            | —    | Red        |
| GND        | Pin 20           | —    | Black      |
| SCK        | Pin 23           | GPIO11 | Yellow   |
| MISO       | Pin 21           | GPIO9  | Blue     |
| MOSI       | Pin 19           | GPIO10 | Green    |
| NSS (CS)   | Pin 16           | GPIO23 | Orange   |
| BUSY       | Pin 22           | GPIO25 | White    |
| RST        | Pin 18           | GPIO24 | Brown    |

> **Power:** The PN5180 board has two power pins. 3V3 powers the IC itself,
> 5V powers the antenna booster and extends read range. Both should be connected.
> Do NOT connect 5V to the 3V3 pin — it will destroy the reader.

> **NSS:** We use GPIO23 for manual chip-select instead of the default SPI CE0
> (GPIO8) because the kernel SPI driver's automatic CS timing does not meet the
> PN5180's requirements (5µs setup, 100µs hold). Manual CS via GPIO23 with
> `spidev.no_cs = True` resolves this.

### Setup Steps

#### 1. Enable SPI and I2C

After a fresh Raspberry Pi OS install, SPI and I2C are disabled by default.

```bash
sudo raspi-config
# Navigate to: Interface Options -> SPI -> Enable
# Navigate to: Interface Options -> I2C -> Enable
sudo reboot
```

Verify after reboot:

```bash
ls /dev/spidev0.*
# Should show: /dev/spidev0.0  /dev/spidev0.1

ls /dev/i2c-*
# Should include: /dev/i2c-1
```

#### 2. Configure `/boot/firmware/config.txt`

Add the following lines under the `[all]` section:

```
# SpoolBuddy: I2C bus 1 for NAU7802 scale (GPIO2/GPIO3)
dtparam=i2c_arm=on

# SpoolBuddy: Disable SPI auto CS (manual CS on GPIO23 for PN5180)
dtoverlay=spi0-0cs
```

- `i2c_arm=on` enables I2C bus 1 (GPIO2/GPIO3). The NAU7802 is wired to bus 1.
  manual CS on GPIO23 because the driver's CS timing doesn't meet the PN5180's

Then reboot:

```bash
sudo reboot
```

Verify after reboot:

```bash
ls /dev/i2c-1
# Should exist

sudo i2cdetect -y 1
# Should show 0x2A (NAU7802)
```

#### 3. Install system packages

```bash
sudo apt install python3-spidev python3-libgpiod gpiod libgpiod3 i2c-tools
```

- `python3-spidev` / `libgpiod3` — system libraries for SPI and GPIO access
- `gpiod` — command-line GPIO tools (useful for debugging)

```bash
pip install spidev gpiod smbus2
```

- `spidev` — Python SPI bindings (PN5180 NFC reader)
- `gpiod` — Python GPIO bindings via libgpiod (works on both RPi 4 and RPi 5)

Wago connectors or breadboard jumpers are unreliable for SPI — the PN5180
is very sensitive to signal integrity issues (loose connections cause RF
field flickering, phantom errors, and intermittent communication failures).
**Solder all wires directly** for reliable operation.

#### 6. Verify hardware communication

Run the diagnostic script to confirm the PN5180 is responding:

```bash
sudo python3 spoolbuddy/pn5180_diag.py
```

Expected output includes product version (e.g. `v4.0`), firmware version,
register dump, and "Diagnostics complete" at the end.

#### 7. Test tag reading

```bash
sudo python3 spoolbuddy/read_tag.py
```

Place a tag on the reader. Supported tag types:

| Tag Type            | SAK    | Use Case                     |
|---------------------|--------|------------------------------|
| MIFARE Classic 1K   | `0x08` | Bambu Lab filament tags      |
| MIFARE Classic 4K   | `0x18` | Bambu Lab filament tags      |
| NTAG (213/215/216)  | `0x00` | SpoolEase / OpenPrintTag     |

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| All zeros from SPI reads | SPI not enabled | Run `raspi-config` and enable SPI, then reboot |
| `GENERAL_ERROR` on SEND_DATA | Automatic CS timing too fast | Use manual CS on GPIO23 with `spi0-0cs` overlay |
| `BUSY timeout` | Wiring issue or RST not connected | Check RST and BUSY pin connections |
| RF field flickering on/off | Loose power wires | Solder all connections |
| `No tag found` but tag is present | Wrong protocol or missing `setTransceiveMode()` | Ensure ISO 14443A config (`0x00, 0x80`) and `setTransceiveMode()` before every `SEND_DATA` |
| Auth failed for block N | Wrong key derivation | Verify HKDF uses context `"RFID-A\0"` (7 bytes including null terminator) |
| `EBUSY` when requesting GPIO8 | Kernel SPI driver owns CE0 | Use GPIO23 for NSS instead |

### Technical Notes

- SPI speed: **500 kHz** (higher speeds cause communication errors)
- SPI mode: **0** (CPOL=0, CPHA=0)


### Wiring

| NAU7802 Pin | Raspberry Pi Pin | GPIO   | Wire Color |
|-------------|------------------|--------|------------|
| VCC         | Pin 1            | —      | Red        |
| SDA         | Pin 3            | GPIO 2 | Yellow     |
| SCL         | Pin 5            | GPIO 3 | White      |
| GND         | Pin 30           | —      | Black      |

> **I2C Bus:** Uses I2C bus 1 (GPIO2/GPIO3), enabled via `dtparam=i2c_arm=on`
> in config.txt.

### Verify

```bash
sudo i2cdetect -y 1
# Should show 0x2A

sudo python3 spoolbuddy/scale_diag.py
```

The diagnostic reads 10 samples at 10 SPS and shows raw ADC values, average,
and spread. Typical idle readings are around ~500k with a spread under 20k.
