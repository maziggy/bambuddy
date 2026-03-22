#!/usr/bin/env python3
"""NAU7802 Scale Diagnostic - ported from SpoolBuddy Rust firmware.

I2C address: 0x2A
Bus: /dev/i2c-1 (GPIO2/GPIO3 on RPi)
"""

import os
import struct
import sys
import time

import smbus2


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


class NAU7802:
    def __init__(self, bus=I2C_BUS, addr=NAU7802_ADDR):
        self._bus_num = bus
        self._bus = smbus2.SMBus(bus)
        self._addr = addr

    def close(self):
        self._bus.close()

    def read_reg(self, reg: int) -> int:
        return self._bus.read_byte_data(self._addr, reg)

    def write_reg(self, reg: int, val: int):
        self._bus.write_byte_data(self._addr, reg, val & 0xFF)

    def _update_bits(self, reg: int, mask: int, value: int):
        cur = self.read_reg(reg)
        self.write_reg(reg, (cur & ~mask) | (value & mask))

    def _set_bit(self, reg: int, bit: int, enabled: bool):
        mask = 1 << bit
        self._update_bits(reg, mask, mask if enabled else 0)

    def _set_field(self, reg: int, shift: int, width: int, value: int):
        mask = ((1 << width) - 1) << shift
        self._update_bits(reg, mask, value << shift)

    def init(self):
        """Initialize NAU7802 using the Adafruit library startup sequence."""

        # Reset
        self._set_bit(REG_PU_CTRL, 0, True)  # RR=1
        time.sleep(0.010)
        self._set_bit(REG_PU_CTRL, 0, False)  # RR=0
        self._set_bit(REG_PU_CTRL, 1, True)  # PUD=1
        time.sleep(0.001)

        # Enable digital + analog and allow analog section to settle.
        self._set_bit(REG_PU_CTRL, 1, True)  # PUD=1
        self._set_bit(REG_PU_CTRL, 2, True)  # PUA=1
        time.sleep(0.600)

        # Start conversion cycle (PU_CS bit 4) after power-up.
        self._set_bit(REG_PU_CTRL, 4, True)

        # Wait for power-up ready (PU_PUR bit 3)
        for _ in range(100):
            status = self.read_reg(REG_PU_CTRL)
            if status & PU_PUR:
                print("  Power-up ready")
                break
            time.sleep(0.001)
        else:
            raise TimeoutError("NAU7802 power-up timeout")

        # Check revision register low nibble (Adafruit expects 0xF).
        revision = self.read_reg(REG_REVISION)
        print(f"  Revision: 0x{revision:02X}")
        if (revision & 0x0F) != 0x0F:
            raise RuntimeError(f"Unexpected NAU7802 revision register: 0x{revision:02X}")

        # Internal LDO enable is PU_CTRL.AVDDS (bit 7); set LDO voltage to 3.0V.
        self._set_bit(REG_PU_CTRL, 7, True)  # AVDDS=1 (internal LDO)
        self._set_field(REG_CTRL1, shift=3, width=3, value=0b101)  # VLDO=3.0V
        print("  LDO: 3.0V (internal)")

        # Gain: 128x (bits 2:0 of CTRL1 = 0b111)
        self._set_field(REG_CTRL1, shift=0, width=3, value=0b111)
        print("  Gain: 128x")

        # Sample rate: 10 SPS (CTRL2 bits 6:4 = 0b000)
        self._set_field(REG_CTRL2, shift=4, width=3, value=0b000)
        print("  Sample rate: 10 SPS")

        # Adafruit tuning: disable ADC chopper clock (ADC bits 5:4 = 0b11)
        self._set_field(REG_ADC, shift=4, width=2, value=0b11)

        # Adafruit tuning: use low ESR caps (PGA bit 6 = 0)
        self._set_bit(REG_PGA, 6, False)

        # Start conversion cycle
        self._set_bit(REG_PU_CTRL, 4, True)
        print("  Conversion started")

    def data_ready(self) -> bool:
        return bool(self.read_reg(REG_PU_CTRL) & PU_CR)

    def read_raw(self) -> int:
        """Read 24-bit signed ADC value."""
        b2 = self.read_reg(REG_ADCO_B2)
        b1 = self.read_reg(REG_ADCO_B1)
        b0 = self.read_reg(REG_ADCO_B0)
        raw = (b2 << 16) | (b1 << 8) | b0
        # Sign extend 24-bit to 32-bit
        if raw & 0x800000:
            raw |= 0xFF000000
            raw = struct.unpack("i", struct.pack("I", raw))[0]
        return raw


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
        if isinstance(e, OSError):
            if e.errno == 16:  # Device or resource busy
                print("\nI2C DEVICE BUSY (Errno 16): Another process is using the I2C bus.")
                print("This typically means the SpoolBuddy daemon is already reading the scale.")
                print("\nTo run this diagnostic, stop the daemon first:")
                print("  sudo systemctl stop bambuddy")
                print("  # Run diagnostic")
                print("  .../scale_diag.py")
                print("  # Restart daemon when done:")
                print("  sudo systemctl start bambuddy")
            elif e.errno == 121:
                print("\nI2C NACK (Errno 121): the device did not acknowledge reads at 0x2A.")
                print("Check:")
                print("  - NAU7802 SDA/SCL are on the configured bus pins")
                print("  - 3.3V and GND are correct and stable")
                print("  - Sensor address is really 0x2A")
                print("  - No loose wire or swapped SDA/SCL")
            else:
                print(f"\nI2C Error (Errno {e.errno}): {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        scale.close()


if __name__ == "__main__":
    main()
