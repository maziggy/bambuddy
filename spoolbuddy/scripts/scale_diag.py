#!/usr/bin/env python3
"""NAU7802 Scale Driver - ported from SpoolBuddy Rust firmware.

I2C address: 0x2A
Bus: /dev/i2c-1 (GPIO2/GPIO3 on RPi)
"""

import os
import sys
import time

import smbus2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "daemon")))

from nau7802 import NAU7802


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


I2C_BUS = _env_int("SPOOLBUDDY_I2C_BUS", 1)
NAU7802_ADDR = 0x2A

# Register addresses
REG_PU_CTRL = 0x00
REG_CTRL1 = 0x01
REG_CTRL2 = 0x02
REG_ADCO_B2 = 0x12  # ADC output MSB
REG_ADCO_B1 = 0x13
REG_ADCO_B0 = 0x14  # ADC output LSB
REG_ADC = 0x15
REG_PGA = 0x1B
REG_PWR_CTRL = 0x1C
REG_REVISION = 0x1F

# PU_CTRL bits
PU_RR = 0x01  # Register reset
PU_PUD = 0x02  # Power up digital
PU_PUA = 0x04  # Power up analog
PU_PUR = 0x08  # Power up ready (read-only)
PU_CS = 0x10  # Cycle start
PU_CR = 0x20  # Cycle ready (read-only)
PU_OSCS = 0x40  # Oscillator select
PU_AVDDS = 0x80  # AVDD source select


def main():
    print("=" * 60)
    print("NAU7802 Scale Diagnostic")
    print("=" * 60)

    print(f"Configured bus: {I2C_BUS}, address: 0x{NAU7802_ADDR:02X}")

    # Probe both common I2C buses and show where devices are actually visible.
    found_by_bus: dict[int, list[int]] = {}
    for bus_num in (0, 1):
        found_by_bus[bus_num] = []
        try:
            with smbus2.SMBus(bus_num) as probe_bus:
                for addr in range(0x03, 0x78):
                    try:
                        probe_bus.read_byte(addr)
                        found_by_bus[bus_num].append(addr)
                    except OSError:
                        continue
        except FileNotFoundError:
            continue
        except PermissionError:
            continue

    for bus_num, addrs in found_by_bus.items():
        if addrs:
            pretty = " ".join(f"0x{a:02X}" for a in addrs)
            print(f"Bus {bus_num} devices: {pretty}")
        else:
            print(f"Bus {bus_num} devices: (none)")

    if NAU7802_ADDR not in found_by_bus.get(I2C_BUS, []):
        for alt in (1, 0):
            if alt != I2C_BUS and NAU7802_ADDR in found_by_bus.get(alt, []):
                print(f"\nHint: NAU7802 (0x{NAU7802_ADDR:02X}) appears on bus {alt}, not configured bus {I2C_BUS}.")
                print(f"Try: SPOOLBUDDY_I2C_BUS={alt} .../scale_diag.py")
                break

    scale = NAU7802()
    try:
        print("[1] Initializing...")

        scale.init()

        # Print key interpreted config values
        revision = scale.read_reg(REG_REVISION)
        revision_id = revision & 0x0F
        revision_id_str = f"0b{revision_id:04b}"
        print(f"    Revision ID: {revision_id_str} (bits 3:0, should be 0b1111 per datasheet)")
        ctrl1 = scale.read_reg(REG_CTRL1)
        vldo = (ctrl1 >> 3) & 0b111
        vldo_map = {
            0b111: "2.4V",
            0b110: "2.7V",
            0b101: "3.0V",
            0b100: "3.3V",
            0b011: "3.6V",
            0b010: "3.9V",
            0b001: "4.2V",
            0b000: "4.5V",
        }
        vldo_str = vldo_map.get(vldo, f"Unknown ({vldo})")
        gain = ctrl1 & 0b111
        gain_map = {
            0b000: "1x",
            0b001: "2x",
            0b010: "4x",
            0b011: "8x",
            0b100: "16x",
            0b101: "32x",
            0b110: "64x",
            0b111: "128x",
        }
        gain_str = gain_map.get(gain, f"Unknown ({gain})")
        print(f"    LDO setting (VLDO): {vldo_str}")
        print(f"    Gain setting: {gain_str}")
        ctrl2 = scale.read_reg(REG_CTRL2)
        sps = (ctrl2 >> 4) & 0b111
        sps_map = {
            0b000: "10 SPS",
            0b001: "20 SPS",
            0b010: "40 SPS",
            0b011: "80 SPS",
            0b100: "320 SPS",
        }
        sps_str = sps_map.get(sps, f"Unknown ({sps})")
        print(f"    Sample rate: {sps_str}")
        adc = scale.read_reg(REG_ADC)
        chopper = (adc >> 4) & 0b11
        chopper_str = {0b00: "Enabled", 0b01: "Enabled", 0b10: "Enabled", 0b11: "Disabled"}.get(
            chopper, f"Unknown ({chopper})"
        )
        print(f"    ADC chopper: {chopper_str}")
        pga = scale.read_reg(REG_PGA)
        low_esr = (pga >> 6) & 0x1
        low_esr_str = "Enabled" if low_esr == 0 else "Disabled"
        print(f"    PGA low-ESR caps: {low_esr_str}")
        # PU_CTRL bit 7: AVDD source select
        pu_ctrl = scale.read_reg(REG_PU_CTRL)
        avdds = (pu_ctrl >> 7) & 0x1
        avdds_str = "Internal LDO" if avdds == 1 else "AVDD pin input"
        print(f"    AVDD source select: {avdds_str} (PU_CTRL bit 7)")

        scale.flush_readings(count=4, timeout_s=1.5)
        scale.calibrate_afe(timeout_ms=1000, mode=0)
        scale.flush_readings(count=2, timeout_s=1.0)

        print("[2] Waiting for first reading...")
        for _ in range(200):
            if scale.data_ready():
                break
            time.sleep(0.010)
        else:
            print("    Timeout waiting for data ready")
            sys.exit(1)

        print("[3] Reading 10 samples (10 SPS = ~1 second)...")
        readings = []
        for i in range(10):
            # Wait for data ready
            for _ in range(200):
                if scale.data_ready():
                    break
                time.sleep(0.010)
            raw = scale.read_raw()
            readings.append(raw)
            print(f"    Sample {i + 1:2d}: {raw:>10d}")

        avg = sum(readings) / len(readings)
        spread = max(readings) - min(readings)
        print(f"\n    Average: {avg:>10.0f}")
        print(f"    Min:     {min(readings):>10d}")
        print(f"    Max:     {max(readings):>10d}")
        print(f"    Spread:  {spread:>10d}")

        print("\n" + "=" * 60)
        print("Diagnostic complete!")
        print("=" * 60)

    except Exception as e:
        print(f"\nERROR: {e}")
        is_known_error = False

        if isinstance(e, OSError):
            if e.errno == 16:  # Device or resource busy
                is_known_error = True
                print("\nI2C DEVICE BUSY (Errno 16): Another process is using the I2C bus.")
                print("This typically means the SpoolBuddy daemon is already reading the scale.")
                print("\nTo run this diagnostic, stop the daemon first:")
                print("  sudo systemctl stop bambuddy")
                print("  # Run diagnostic")
                print("  .../scale_diag.py")
                print("  # Restart daemon when done:")
                print("  sudo systemctl start bambuddy")
            elif e.errno == 121:
                is_known_error = True
                print("\nI2C NACK (Errno 121): the device did not acknowledge reads at 0x2A.")
                print("Check:")
                print("  - NAU7802 SDA/SCL are on the configured bus pins")
                print("  - 3.3V and GND are correct and stable")
                print("  - Sensor address is really 0x2A")
                print("  - No loose wire or swapped SDA/SCL")
            else:
                print(f"\nI2C Error (Errno {e.errno}): {e}")

        # Only print full traceback for unexpected errors
        if not is_known_error:
            import traceback

            traceback.print_exc()

        sys.exit(1)
    finally:
        scale.close()


if __name__ == "__main__":
    main()
