#!/usr/bin/env python3
"""PN5180 NFC reader diagnostic script.

Connects to a PN5180 over SPI on a Raspberry Pi and reads
hardware status, version info, and register state.

Wiring (from spoolbuddy/README.md):
    PN5180 VCC  -> Pi Pin 1  (3.3V)
    PN5180 GND  -> Pi Pin 20 (GND)
    PN5180 SCK  -> Pi Pin 23 (GPIO11)
    PN5180 MISO -> Pi Pin 21 (GPIO9)
    PN5180 MOSI -> Pi Pin 19 (GPIO10)
    PN5180 NSS  -> Pi Pin 16 (GPIO23, manual CS)
    PN5180 BUSY -> Pi Pin 22 (GPIO25)
    PN5180 RST  -> Pi Pin 18 (GPIO24)
"""

import os
import sys
import time

import gpiod
from pn5180 import (
    NSS_PIN as DRIVER_NSS_PIN,  # noqa: E402
    PN5180,  # noqa: E402
    RST_PIN as DRIVER_RST_PIN,  # noqa: E402
    SPI_BUS as DRIVER_SPI_BUS,  # noqa: E402
    SPI_DEVICE as DRIVER_SPI_DEVICE,  # noqa: E402
)

# Ensure daemon directory is in sys.path regardless of invocation location
_daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "daemon"))
if _daemon_dir not in sys.path:
    sys.path.insert(0, _daemon_dir)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Pin assignments (BCM numbering)
# ---------------------------------------------------------------------------
BUSY_PIN = _env_int("SPOOLBUDDY_NFC_BUSY_PIN", 25)
RST_PIN = _env_int("SPOOLBUDDY_NFC_RST_PIN", 24)
NSS_PIN = _env_int("SPOOLBUDDY_NFC_NSS_PIN", 23)

# ---------------------------------------------------------------------------
# SPI command instruction codes (NXP PN5180 datasheet Table 5)
# ---------------------------------------------------------------------------
CMD_WRITE_REGISTER = 0x00
CMD_WRITE_REGISTER_OR_MASK = 0x01
CMD_WRITE_REGISTER_AND_MASK = 0x02
CMD_READ_REGISTER = 0x04
CMD_READ_REGISTER_MULTIPLE = 0x05
CMD_WRITE_EEPROM = 0x06
CMD_READ_EEPROM = 0x07
CMD_SEND_DATA = 0x09
CMD_READ_DATA = 0x0A
CMD_LOAD_RF_CONFIG = 0x11
CMD_RF_ON = 0x16
CMD_RF_OFF = 0x17

# ---------------------------------------------------------------------------
# Register addresses (32-bit each)
# ---------------------------------------------------------------------------
REG_SYSTEM_CONFIG = 0x00
REG_IRQ_ENABLE = 0x01
REG_IRQ_STATUS = 0x02
REG_IRQ_CLEAR = 0x03
REG_TRANSCEIVE_CONTROL = 0x04
REG_TIMER1_RELOAD = 0x0C
REG_TIMER1_CONFIG = 0x0F
REG_RX_WAIT_CONFIG = 0x11
REG_CRC_RX_CONFIG = 0x12
REG_RX_STATUS = 0x13
REG_CRC_TX_CONFIG = 0x19
REG_RF_STATUS = 0x1D
REG_SYSTEM_STATUS = 0x24
REG_TEMP_CONTROL = 0x25

REGISTER_NAMES = {
    REG_SYSTEM_CONFIG: "SYSTEM_CONFIG",
    REG_IRQ_ENABLE: "IRQ_ENABLE",
    REG_IRQ_STATUS: "IRQ_STATUS",
    REG_IRQ_CLEAR: "IRQ_CLEAR",
    REG_TRANSCEIVE_CONTROL: "TRANSCEIVE_CONTROL",
    REG_TIMER1_RELOAD: "TIMER1_RELOAD",
    REG_TIMER1_CONFIG: "TIMER1_CONFIG",
    REG_RX_WAIT_CONFIG: "RX_WAIT_CONFIG",
    REG_CRC_RX_CONFIG: "CRC_RX_CONFIG",
    REG_RX_STATUS: "RX_STATUS",
    REG_CRC_TX_CONFIG: "CRC_TX_CONFIG",
    REG_RF_STATUS: "RF_STATUS",
    REG_SYSTEM_STATUS: "SYSTEM_STATUS",
    REG_TEMP_CONTROL: "TEMP_CONTROL",
}

# ---------------------------------------------------------------------------
# EEPROM addresses
# ---------------------------------------------------------------------------
EEPROM_DIE_IDENTIFIER = 0x00  # 16 bytes
EEPROM_PRODUCT_VERSION = 0x10  # 2 bytes
EEPROM_FIRMWARE_VERSION = 0x12  # 2 bytes
EEPROM_EEPROM_VERSION = 0x14  # 2 bytes
EEPROM_IRQ_PIN_CONFIG = 0x1A  # 1 byte


def _check_spi_device_access() -> str:
    """Check that the configured spidev exists and can be opened."""
    spi_path = f"/dev/spidev{DRIVER_SPI_BUS}.{DRIVER_SPI_DEVICE}"
    if not os.path.exists(spi_path):
        raise FileNotFoundError(f"SPI device not found: {spi_path}")

    fd = os.open(spi_path, os.O_RDWR)
    os.close(fd)
    return spi_path


def _pin_state_name(value: gpiod.line.Value) -> str:
    return "ACTIVE" if value == gpiod.line.Value.ACTIVE else "INACTIVE"


def _self_test_control_pins(nfc: PN5180):
    """Toggle NSS and RST pins and print observed line state."""
    for pin_name, pin_num in (("NSS", DRIVER_NSS_PIN), ("RST", DRIVER_RST_PIN)):
        nfc._lines.set_value(pin_num, gpiod.line.Value.ACTIVE)
        time.sleep(0.005)
        active_state = nfc._lines.get_value(pin_num)

        nfc._lines.set_value(pin_num, gpiod.line.Value.INACTIVE)
        time.sleep(0.005)
        inactive_state = nfc._lines.get_value(pin_num)

        # Restore idle-high level used by this driver.
        nfc._lines.set_value(pin_num, gpiod.line.Value.ACTIVE)

        print(
            f"    {pin_name} pin {pin_num}: "
            f"ACTIVE->{_pin_state_name(active_state)}, "
            f"INACTIVE->{_pin_state_name(inactive_state)}"
        )


def run_diagnostics():
    print("=" * 60)
    print("PN5180 NFC Reader Diagnostics")
    print("=" * 60)

    nfc = None
    try:
        print("\n[1] SPI device check...")
        spi_path = _check_spi_device_access()
        print(f"    SPI device OK: {spi_path}")

        nfc = PN5180()

        print("\n[2] Control pin self-test (NSS/RST)...")
        _self_test_control_pins(nfc)

        # Reset
        print("\n[3] Hardware reset...")
        nfc.reset()
        print("    Reset OK")

        # Version info
        print("\n[4] Version info (EEPROM)")
        product = nfc.read_eeprom(EEPROM_PRODUCT_VERSION, 2)
        firmware = nfc.read_eeprom(EEPROM_FIRMWARE_VERSION, 2)
        eeprom = nfc.read_eeprom(EEPROM_EEPROM_VERSION, 2)
        die_id = nfc.read_eeprom(EEPROM_DIE_IDENTIFIER, 16)

        print(f"    Product version  : {product[1]}.{product[0]}")
        print(f"    Firmware version : {firmware[1]}.{firmware[0]}")
        print(f"    EEPROM version   : {eeprom[1]}.{eeprom[0]}")
        print(f"    Die identifier   : {die_id.hex()}")

        # Register dump
        print("\n[5] Register dump")
        # Use register names from the script (not in pn5180.py)
        REGISTER_NAMES = {
            0x00: "SYSTEM_CONFIG",
            0x01: "IRQ_ENABLE",
            0x02: "IRQ_STATUS",
            0x03: "IRQ_CLEAR",
            0x04: "TRANSCEIVE_CONTROL",
            0x0C: "TIMER1_RELOAD",
            0x0F: "TIMER1_CONFIG",
            0x11: "RX_WAIT_CONFIG",
            0x12: "CRC_RX_CONFIG",
            0x13: "RX_STATUS",
            0x19: "CRC_TX_CONFIG",
            0x1D: "RF_STATUS",
            0x24: "SYSTEM_STATUS",
            0x25: "TEMP_CONTROL",
        }
        for addr, name in sorted(REGISTER_NAMES.items()):
            val = nfc.read_reg(addr)
            print(f"    0x{addr:02X} {name:<24s} = 0x{val:08X}")

        # IRQ status breakdown
        irq = nfc.read_reg(0x02)
        print(f"\n[6] IRQ status flags (0x{irq:08X})")
        irq_flags = [
            (0, "RX_IRQ"),
            (1, "TX_IRQ"),
            (2, "IDLE_IRQ"),
            (3, "MODE_DETECTED_IRQ"),
            (4, "CARD_ACTIVATED_IRQ"),
            (5, "STATE_CHANGE_IRQ"),
            (6, "RFOFF_DET_IRQ"),
            (7, "RFON_DET_IRQ"),
            (8, "TX_RFOFF_IRQ"),
            (9, "TX_RFON_IRQ"),
            (10, "RF_ACTIVE_ERROR_IRQ"),
            (14, "LPCD_IRQ"),
        ]
        for bit, name in irq_flags:
            state = "SET" if irq & (1 << bit) else "---"
            print(f"    bit {bit:2d}: {name:<28s} [{state}]")

        # RF status
        rf = nfc.read_reg(0x1D)
        print(f"\n[7] RF status (0x{rf:08X})")
        tx_rf_on = bool(rf & (1 << 0))
        rx_en = bool(rf & (1 << 1))
        print(f"    TX RF active : {tx_rf_on}")
        print(f"    RX enabled   : {rx_en}")

        # System status
        sys_stat = nfc.read_reg(0x24)
        print(f"\n[8] System status (0x{sys_stat:08X})")

        # Temperature
        temp_ctrl = nfc.read_reg(0x25)
        print(f"\n[9] Temp control register (0x{temp_ctrl:08X})")

        print("\n" + "=" * 60)
        print("Diagnostics complete - PN5180 is responding over SPI.")
        print("=" * 60)

    except TimeoutError as e:
        print(f"\nERROR: {e}")
        print("Check wiring and ensure SPI is enabled (dtparam=spi=on in /boot/firmware/config.txt)")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        if nfc is not None:
            nfc.close()


if __name__ == "__main__":
    run_diagnostics()
