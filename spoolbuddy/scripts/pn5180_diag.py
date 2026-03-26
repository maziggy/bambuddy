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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "daemon")))


from pn5180 import (  # noqa: E402
    NSS_PIN as DRIVER_NSS_PIN,
    PN5180,
    RST_PIN as DRIVER_RST_PIN,
    SPI_BUS as DRIVER_SPI_BUS,
    SPI_DEVICE as DRIVER_SPI_DEVICE,
)

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
REG_SIGPRO_CONFIG = 0x1A  # Signal Processing Configuration
REG_TEMP_CONTROL = 0x25

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
    """Toggle NSS and RST pins and print observed line state.
    Uses public set_pin/get_pin methods to avoid direct access to driver internals.
    """
    for pin_name, pin_num in (("NSS", DRIVER_NSS_PIN), ("RST", DRIVER_RST_PIN)):
        nfc.set_pin(pin_num, True)
        time.sleep(0.005)
        active_state = nfc.get_pin(pin_num)

        nfc.set_pin(pin_num, False)
        time.sleep(0.005)
        inactive_state = nfc.get_pin(pin_num)

        # Restore idle-high level used by this driver.
        nfc.set_pin(pin_num, True)

        print(
            f"    {pin_name} pin {pin_num}: "
            f"ACTIVE->{'ACTIVE' if active_state else 'INACTIVE'}, "
            f"INACTIVE->{'ACTIVE' if inactive_state else 'INACTIVE'}"
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
        REGISTER_NAMES_DUMP = {
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
            0x1A: "SIGPRO_CONFIG",
            0x1D: "RF_STATUS",
            0x24: "SYSTEM_STATUS",
            0x25: "TEMP_CONTROL",
        }
        for addr, name in sorted(REGISTER_NAMES_DUMP.items()):
            val = nfc.read_reg(addr)
            print(f"    0x{addr:02X} {name:<24s} = 0x{val:08X}")

        # SIGPRO_CONFIG ISO/IEC14443 mode check
        sigpro_val = nfc.read_reg(REG_SIGPRO_CONFIG)
        sigpro_mode = (sigpro_val >> 0) & 0b111
        baudrate_map = {
            0b100: "106 kBd (ISO/IEC14443 type A/B)",
            0b101: "212 kBd (FeliCa 212 kBd)",
            0b110: "424 kBd (FeliCa 424 kBd)",
            0b111: "848 kBd",
        }
        baudrate_str = baudrate_map.get(sigpro_mode, "Unknown or reserved")
        print(f"\n[5b] SIGPRO_CONFIG (0x1A) bits 2:0 = 0b{sigpro_mode:03b} ({baudrate_str})")

        # IRQ status breakdown
        irq = nfc.read_reg(REG_IRQ_STATUS)
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
        rf = nfc.read_reg(REG_RF_STATUS)
        print(f"\n[7] RF status (0x{rf:08X})")
        tx_rf_on = bool(rf & (1 << 0))
        rx_en = bool(rf & (1 << 1))
        print(f"    TX RF active : {tx_rf_on}")
        print(f"    RX enabled   : {rx_en}")

        # System status
        sys_stat = nfc.read_reg(REG_SYSTEM_STATUS)
        print(f"\n[8] System status (0x{sys_stat:08X})")

        # System status bit breakdown
        sys_stat_bits = [
            (9, "LDO_TVDD_OK"),
            (8, "PARAMETER_ERROR"),
            (7, "SYNTAX_ERROR"),
            (6, "SEMANTIC_ERROR"),
            (5, "STBY_PREVENT_RFLD"),
            (4, "BOOT_TEMP"),
            (3, "BOOT_SOFT_RESET"),
            (2, "BOOT_WUC"),
            (1, "BOOT_RFLD"),
            (0, "BOOT_POR"),
        ]
        for bit, symbol in sys_stat_bits:
            state = "SET" if sys_stat & (1 << bit) else "---"
            print(f"    bit {bit:2d}: {symbol:<18s} [{state}]")

        # Temperature
        temp_ctrl = nfc.read_reg(REG_TEMP_CONTROL)
        print(f"\n[9] Temp control register (0x{temp_ctrl:08X})")

        # TEMP_DELTA bits 1:0
        temp_delta = (temp_ctrl >> 0) & 0b11
        temp_delta_map = {
            0b00: "85°C",
            0b01: "115°C",
            0b10: "125°C",
            0b11: "135°C",
        }
        temp_delta_str = temp_delta_map.get(temp_delta, "Unknown")
        print(f"    bits 1:0 TEMP_DELTA = 0b{temp_delta:02b} ({temp_delta_str})")

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
