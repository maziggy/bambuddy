#!/usr/bin/env python3
"""NAU7802 Scale Diagnostic — ported from SpoolBuddy Rust firmware.

I2C address: 0x2A
Bus: /dev/i2c-0 (GPIO0/GPIO1 on RPi)
"""

import struct
import sys
import time

import smbus2

I2C_BUS = 0
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
        self._bus = smbus2.SMBus(bus)
        self._addr = addr

    def close(self):
        self._bus.close()

    def read_reg(self, reg: int) -> int:
        return self._bus.read_byte_data(self._addr, reg)

    def write_reg(self, reg: int, val: int):
        self._bus.write_byte_data(self._addr, reg, val & 0xFF)

    def init(self):
        """Initialize NAU7802 — matches Rust firmware init sequence."""
        revision = self.read_reg(REG_REVISION)
        print(f"  Revision: 0x{revision:02X}")

        # Reset
        self.write_reg(REG_PU_CTRL, PU_RR)
        time.sleep(0.010)
        self.write_reg(REG_PU_CTRL, 0x00)

        # Power up digital + analog
        self.write_reg(REG_PU_CTRL, PU_PUD | PU_PUA)

        # Wait for power-up ready
        for _ in range(100):
            status = self.read_reg(REG_PU_CTRL)
            if status & PU_PUR:
                print("  Power-up ready")
                break
            time.sleep(0.001)
        else:
            raise TimeoutError("NAU7802 power-up timeout")

        # Sample rate: 10 SPS (bits 6:4 of CTRL2 = 0b000)
        ctrl2 = self.read_reg(REG_CTRL2)
        self.write_reg(REG_CTRL2, (ctrl2 & 0x8F) | (0 << 4))
        print("  Sample rate: 10 SPS")

        # Gain: 128x (bits 2:0 of CTRL1 = 0b111)
        ctrl1 = self.read_reg(REG_CTRL1)
        self.write_reg(REG_CTRL1, (ctrl1 & 0xF8) | 7)
        print("  Gain: 128x")

        # LDO: 3.3V (bits 5:3 of CTRL1 = 0b100)
        ctrl1 = self.read_reg(REG_CTRL1)
        self.write_reg(REG_CTRL1, (ctrl1 & 0xC7) | (0b100 << 3))

        # Enable internal LDO (bit 7 of CTRL1)
        ctrl1 = self.read_reg(REG_CTRL1)
        self.write_reg(REG_CTRL1, ctrl1 | 0x80)
        print("  LDO: 3.3V (internal)")

        # Start conversion cycle
        pu_ctrl = self.read_reg(REG_PU_CTRL)
        self.write_reg(REG_PU_CTRL, pu_ctrl | PU_CS)
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
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        scale.close()


if __name__ == "__main__":
    main()
