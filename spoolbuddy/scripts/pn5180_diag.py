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
    PN5180 NSS  -> Pi Pin 24 (GPIO8 / CE0)
    PN5180 BUSY -> Pi Pin 22 (GPIO25)
    PN5180 RST  -> Pi Pin 18 (GPIO24)
"""

import sys
import time

import gpiod
import spidev

# ---------------------------------------------------------------------------
# Pin assignments (BCM numbering)
# ---------------------------------------------------------------------------
BUSY_PIN = 25  # Pin 22
RST_PIN = 24  # Pin 18

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


def _find_gpio_chip():
    """Find the right gpiochip for Raspberry Pi GPIO pins.

    RPi 5 uses gpiochip4, RPi 4 uses gpiochip0.
    """
    for path in ["/dev/gpiochip4", "/dev/gpiochip0"]:
        try:
            chip = gpiod.Chip(path)
            info = chip.get_info()
            # RPi 4: pinctrl-bcm2711, RPi 5: pinctrl-rp1
            if "pinctrl" in info.label:
                return chip
            chip.close()
        except (FileNotFoundError, PermissionError, OSError):
            continue
    raise RuntimeError("Could not find Raspberry Pi GPIO chip")


class PN5180:
    """Low-level driver for the PN5180 NFC frontend over SPI."""

    def __init__(self, spi_bus=0, spi_device=0, spi_speed_hz=1_000_000, busy_pin=BUSY_PIN, rst_pin=RST_PIN):
        # GPIO setup via libgpiod
        self._chip = _find_gpio_chip()

        self._busy_line = self._chip.request_lines(
            consumer="pn5180-diag",
            config={busy_pin: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)},
        )
        self._rst_line = self._chip.request_lines(
            consumer="pn5180-diag",
            config={
                rst_pin: gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT,
                    output_value=gpiod.line.Value.ACTIVE,
                )
            },
        )
        self._busy_pin = busy_pin
        self._rst_pin = rst_pin

        # SPI setup – mode 0 (CPOL=0, CPHA=0), MSB first
        self._spi = spidev.SpiDev()
        self._spi.open(spi_bus, spi_device)
        self._spi.max_speed_hz = spi_speed_hz
        self._spi.mode = 0b00
        self._spi.bits_per_word = 8

    def close(self):
        self._spi.close()
        self._busy_line.release()
        self._rst_line.release()
        self._chip.close()

    # -- low-level helpers --------------------------------------------------

    def _busy_is_high(self):
        return self._busy_line.get_value(self._busy_pin) == gpiod.line.Value.ACTIVE

    def _wait_busy(self, timeout_s=1.0):
        """Block until BUSY goes LOW (PN5180 ready)."""
        deadline = time.monotonic() + timeout_s
        while self._busy_is_high():
            if time.monotonic() > deadline:
                raise TimeoutError("PN5180 BUSY line did not go low")
            time.sleep(0.001)

    def _send_command(self, tx_data, rx_len=0):
        """Send an SPI command frame and optionally read a response frame.

        The PN5180 SPI protocol is half-duplex:
          1. Send command frame (NSS held low for entire frame).
          2. Wait for BUSY high then low (command processed).
          3. If a response is expected, clock out rx_len bytes in a second frame.
        """
        self._wait_busy()

        # Transmit command
        self._spi.xfer2(list(tx_data))

        if rx_len == 0:
            # Write-only command – wait for processing
            time.sleep(0.001)
            self._wait_busy()
            return None

        # Wait for PN5180 to process command (BUSY goes high then low)
        time.sleep(0.001)
        self._wait_busy()

        # Read response
        rx = self._spi.xfer2([0xFF] * rx_len)
        time.sleep(0.001)
        self._wait_busy()
        return bytes(rx)

    # -- register operations ------------------------------------------------

    def read_register(self, addr):
        """Read a 32-bit register. Returns int."""
        resp = self._send_command([CMD_READ_REGISTER, addr], rx_len=4)
        return int.from_bytes(resp, "little")

    def write_register(self, addr, value):
        """Write a 32-bit value to a register."""
        self._send_command(
            [
                CMD_WRITE_REGISTER,
                addr,
                value & 0xFF,
                (value >> 8) & 0xFF,
                (value >> 16) & 0xFF,
                (value >> 24) & 0xFF,
            ]
        )

    def write_register_or_mask(self, addr, mask):
        self._send_command(
            [
                CMD_WRITE_REGISTER_OR_MASK,
                addr,
                mask & 0xFF,
                (mask >> 8) & 0xFF,
                (mask >> 16) & 0xFF,
                (mask >> 24) & 0xFF,
            ]
        )

    def write_register_and_mask(self, addr, mask):
        self._send_command(
            [
                CMD_WRITE_REGISTER_AND_MASK,
                addr,
                mask & 0xFF,
                (mask >> 8) & 0xFF,
                (mask >> 16) & 0xFF,
                (mask >> 24) & 0xFF,
            ]
        )

    # -- EEPROM operations --------------------------------------------------

    def read_eeprom(self, addr, length):
        """Read `length` bytes from EEPROM starting at `addr`."""
        return self._send_command([CMD_READ_EEPROM, addr, length], rx_len=length)

    # -- reset --------------------------------------------------------------

    def reset(self):
        """Hardware-reset the PN5180 via the RST pin."""
        self._rst_line.set_value(self._rst_pin, gpiod.line.Value.INACTIVE)
        time.sleep(0.01)
        self._rst_line.set_value(self._rst_pin, gpiod.line.Value.ACTIVE)
        time.sleep(0.05)
        self._wait_busy(timeout_s=2.0)
        # Clear all IRQ flags
        self.write_register(REG_IRQ_CLEAR, 0xFFFFFFFF)

    # -- version / identity -------------------------------------------------

    def get_product_version(self):
        data = self.read_eeprom(EEPROM_PRODUCT_VERSION, 2)
        return f"{data[1]}.{data[0]}"

    def get_firmware_version(self):
        data = self.read_eeprom(EEPROM_FIRMWARE_VERSION, 2)
        return f"{data[1]}.{data[0]}"

    def get_eeprom_version(self):
        data = self.read_eeprom(EEPROM_EEPROM_VERSION, 2)
        return f"{data[1]}.{data[0]}"

    def get_die_identifier(self):
        data = self.read_eeprom(EEPROM_DIE_IDENTIFIER, 16)
        return data.hex()


def run_diagnostics():
    print("=" * 60)
    print("PN5180 NFC Reader Diagnostics")
    print("=" * 60)

    nfc = PN5180()
    try:
        # Reset
        print("\n[1] Hardware reset...")
        nfc.reset()
        print("    Reset OK")

        # Version info
        print("\n[2] Version info (EEPROM)")
        print(f"    Product version  : {nfc.get_product_version()}")
        print(f"    Firmware version : {nfc.get_firmware_version()}")
        print(f"    EEPROM version   : {nfc.get_eeprom_version()}")
        print(f"    Die identifier   : {nfc.get_die_identifier()}")

        # Register dump
        print("\n[3] Register dump")
        for addr, name in sorted(REGISTER_NAMES.items()):
            val = nfc.read_register(addr)
            print(f"    0x{addr:02X} {name:<24s} = 0x{val:08X}")

        # IRQ status breakdown
        irq = nfc.read_register(REG_IRQ_STATUS)
        print(f"\n[4] IRQ status flags (0x{irq:08X})")
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
        rf = nfc.read_register(REG_RF_STATUS)
        print(f"\n[5] RF status (0x{rf:08X})")
        tx_rf_on = bool(rf & (1 << 0))
        rx_en = bool(rf & (1 << 1))
        print(f"    TX RF active : {tx_rf_on}")
        print(f"    RX enabled   : {rx_en}")

        # System status
        sys_stat = nfc.read_register(REG_SYSTEM_STATUS)
        print(f"\n[6] System status (0x{sys_stat:08X})")

        # Temperature
        temp_ctrl = nfc.read_register(REG_TEMP_CONTROL)
        print(f"\n[7] Temp control register (0x{temp_ctrl:08X})")

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
        nfc.close()


if __name__ == "__main__":
    run_diagnostics()
