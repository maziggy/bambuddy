#!/usr/bin/env python3
"""PN5180 NFC tag reader — ported from working Pico firmware (pico-nfc-bridge.ino).

Key learnings from pico-nfc-bridge.ino:
- Must call setTransceiveMode() before every SEND_DATA
- waitBusy() must wait for HIGH then LOW (not just LOW)
- Bambu tags are MIFARE Classic 1K (ISO 14443A), not ISO 15693
- SPI at 500kHz, 5µs CS setup, 100µs post-CS delay
- MFC_AUTHENTICATE (0x0C) is a PN5180 host command — Crypto1 handled in hardware
- HKDF-SHA256 derives per-sector keys from master key + UID
"""

import hashlib
import hmac
import sys
import time

import gpiod
import spidev

BUSY_PIN = 25
RST_PIN = 24
NSS_PIN = 23  # Manual CS (moved from GPIO8)

# Bambu Lab MIFARE Classic key derivation constants (from pico-nfc-bridge.ino)
BAMBU_MASTER_KEY = bytes(
    [
        0x9A,
        0x75,
        0x9C,
        0xF2,
        0xC4,
        0xF7,
        0xCA,
        0xFF,
        0x22,
        0x2C,
        0xB9,
        0x76,
        0x9B,
        0x41,
        0xBC,
        0x96,
    ]
)
BAMBU_CONTEXT = b"RFID-A\x00"  # 7 bytes including null terminator

# Blocks to read for Bambu tag data
BAMBU_BLOCKS = [1, 2, 4, 5]


def hkdf_derive_keys(uid: bytes) -> bytes:
    """Derive 96 bytes of MIFARE key material (16 sectors * 6 bytes each).

    Uses HKDF-SHA256 with the Bambu master key as salt and the tag UID as IKM.
    """
    # HKDF-Extract: PRK = HMAC-SHA256(salt=master_key, IKM=uid)
    prk = hmac.new(BAMBU_MASTER_KEY, uid, hashlib.sha256).digest()

    # HKDF-Expand: generate 96 bytes using context "RFID-A\0"
    okm = b""
    t = b""
    counter = 1
    while len(okm) < 96:
        t = hmac.new(prk, t + BAMBU_CONTEXT + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:96]


def get_sector_key(keys: bytes, block: int) -> bytes:
    """Get the 6-byte key for the sector containing the given block."""
    sector = block // 4
    return keys[sector * 6 : sector * 6 + 6]


def _find_gpio_chip():
    for path in ["/dev/gpiochip4", "/dev/gpiochip0"]:
        try:
            chip = gpiod.Chip(path)
            if "pinctrl" in chip.get_info().label:
                return chip
            chip.close()
        except (FileNotFoundError, PermissionError, OSError):
            continue
    raise RuntimeError("No GPIO chip")


class PN5180:
    def __init__(self):
        self._chip = _find_gpio_chip()
        self._lines = self._chip.request_lines(
            consumer="pn5180",
            config={
                BUSY_PIN: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT),
                RST_PIN: gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT, output_value=gpiod.line.Value.ACTIVE
                ),
                NSS_PIN: gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT, output_value=gpiod.line.Value.ACTIVE
                ),
            },
        )
        self._spi = spidev.SpiDev()
        self._spi.open(0, 0)
        self._spi.max_speed_hz = 500_000  # 500kHz like Pico firmware
        self._spi.mode = 0b00
        self._spi.no_cs = True

    def close(self):
        self._spi.close()
        self._lines.release()
        self._chip.close()

    def _cs_low(self):
        self._lines.set_value(NSS_PIN, gpiod.line.Value.INACTIVE)
        time.sleep(0.000005)  # 5µs setup

    def _cs_high(self):
        self._lines.set_value(NSS_PIN, gpiod.line.Value.ACTIVE)
        time.sleep(0.000100)  # 100µs post-CS delay

    def _wait_busy(self, timeout_s=1.0):
        """Wait for BUSY to go HIGH (processing) then LOW (done) — matches Pico firmware."""
        deadline = time.monotonic() + min(timeout_s, 0.010)
        # Wait for BUSY HIGH (PN5180 started processing)
        while self._lines.get_value(BUSY_PIN) != gpiod.line.Value.ACTIVE:
            if time.monotonic() > deadline:
                break  # Timeout waiting for HIGH — command may have processed already
            time.sleep(0.00001)
        # Wait for BUSY LOW (PN5180 done)
        deadline = time.monotonic() + timeout_s
        while self._lines.get_value(BUSY_PIN) == gpiod.line.Value.ACTIVE:
            if time.monotonic() > deadline:
                raise TimeoutError("BUSY timeout")
            time.sleep(0.0001)

    def _cmd(self, data):
        self._cs_low()
        self._spi.xfer2(list(data))
        self._cs_high()
        self._wait_busy()

    def _read_response(self, n):
        self._cs_low()
        result = self._spi.xfer2([0xFF] * n)
        self._cs_high()
        return result

    # -- Register ops --

    def write_reg(self, reg, val):
        self._cmd([0x00, reg, val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF, (val >> 24) & 0xFF])

    def write_reg_or(self, reg, mask):
        self._cmd([0x01, reg, mask & 0xFF, (mask >> 8) & 0xFF, (mask >> 16) & 0xFF, (mask >> 24) & 0xFF])

    def write_reg_and(self, reg, mask):
        self._cmd([0x02, reg, mask & 0xFF, (mask >> 8) & 0xFF, (mask >> 16) & 0xFF, (mask >> 24) & 0xFF])

    def read_reg(self, reg):
        self._cmd([0x04, reg])
        time.sleep(0.000100)  # Extra 100µs before read
        return int.from_bytes(self._read_response(4), "little")

    def read_eeprom(self, addr, length):
        self._cmd([0x07, addr, length])
        time.sleep(0.000100)
        return bytes(self._read_response(length))

    # -- Commands --

    def reset(self):
        self._lines.set_value(RST_PIN, gpiod.line.Value.INACTIVE)
        time.sleep(0.050)
        self._lines.set_value(RST_PIN, gpiod.line.Value.ACTIVE)
        time.sleep(0.100)
        self._wait_busy(2.0)
        time.sleep(0.050)

    def load_rf_config(self, tx, rx):
        self.write_reg(0x03, 0xFFFFFFFF)  # Clear IRQs first
        time.sleep(0.000100)
        self._cmd([0x11, tx, rx])
        time.sleep(0.010)

    def rf_on(self):
        self._cmd([0x16, 0x00])
        time.sleep(0.010)

    def rf_off(self):
        self._cmd([0x17, 0x00])
        time.sleep(0.005)

    def set_transceive_mode(self):
        """Set SYSTEM_CONFIG command bits to TRANSCEIVE (0x03) — CRITICAL!"""
        sys_cfg = self.read_reg(0x00)
        sys_cfg = (sys_cfg & 0xFFFFFFF8) | 0x03
        self.write_reg(0x00, sys_cfg)

    def send_data(self, data, valid_bits=0x00):
        self._cs_low()
        self._spi.xfer2([0x09, valid_bits] + list(data))
        self._cs_high()
        time.sleep(0.000100)
        self._wait_busy()

    def read_data(self, length):
        self._cmd([0x0A, 0x00])
        return bytes(self._read_response(length))

    # -- ISO 14443A --

    def activate_type_a(self):
        """Full Type A activation: WUPA -> Anticollision -> SELECT. Returns (uid, sak) or None."""
        # Crypto off, CRC off
        self.write_reg_and(0x00, 0xFFFFFFBF)
        self.write_reg_and(0x12, 0xFFFFFFFE)
        self.write_reg_and(0x19, 0xFFFFFFFE)
        self.write_reg(0x03, 0xFFFFFFFF)

        # Reset to IDLE then TRANSCEIVE
        sys_cfg = self.read_reg(0x00)
        self.write_reg(0x00, sys_cfg & 0xFFFFFFF8)  # IDLE
        time.sleep(0.001)
        self.write_reg(0x00, (sys_cfg & 0xFFFFFFF8) | 0x03)  # TRANSCEIVE
        time.sleep(0.002)

        # WUPA (7-bit)
        self.send_data([0x52], valid_bits=0x07)
        time.sleep(0.005)

        rx_status = self.read_reg(0x13)
        rx_len = rx_status & 0x1FF
        if rx_len < 2 or rx_len == 511:
            # Try REQA
            self.write_reg(0x03, 0xFFFFFFFF)
            time.sleep(0.002)
            self.set_transceive_mode()
            time.sleep(0.002)
            self.send_data([0x26], valid_bits=0x07)
            time.sleep(0.005)
            rx_status = self.read_reg(0x13)
            rx_len = rx_status & 0x1FF
            if rx_len < 2 or rx_len == 511:
                return None

        atqa = self.read_data(2)
        if atqa[0] == 0xFF or atqa[0] == 0x00:
            return None

        # Anti-collision Level 1
        self.write_reg(0x03, 0xFFFFFFFF)
        self.set_transceive_mode()
        time.sleep(0.002)

        self.send_data([0x93, 0x20])
        time.sleep(0.010)

        rx_status = self.read_reg(0x13)
        rx_len = rx_status & 0x1FF
        if rx_len < 5 or rx_len > 64:
            return None

        uid_buf = self.read_data(5)
        uid = uid_buf[:4]
        bcc = uid[0] ^ uid[1] ^ uid[2] ^ uid[3]
        if bcc != uid_buf[4]:
            return None

        # SELECT
        self.write_reg(0x03, 0xFFFFFFFF)
        self.set_transceive_mode()
        time.sleep(0.002)

        # Enable CRC for SELECT
        self.write_reg_or(0x19, 0x01)
        self.write_reg_or(0x12, 0x01)

        self.send_data([0x93, 0x70, uid[0], uid[1], uid[2], uid[3], bcc])
        time.sleep(0.010)

        rx_status = self.read_reg(0x13)
        rx_len = rx_status & 0x1FF
        if rx_len < 1:
            return None

        sak_buf = self.read_data(min(rx_len, 3))
        sak = sak_buf[0]

        return bytes(uid), sak

    # -- MIFARE Classic --

    def mfc_authenticate(self, block: int, key: bytes, uid: bytes) -> bool:
        """MIFARE Classic authentication via PN5180 MFC_AUTHENTICATE (0x0C).

        The PN5180 handles Crypto1 internally. After success, bit 6 of
        SYSTEM_CONFIG is set (MFC_CRYPTO1_ON) and all subsequent RF
        communication is encrypted/decrypted by the hardware.

        Args:
            block: Block number to authenticate
            key: 6-byte MIFARE Key A
            uid: 4-byte tag UID
        Returns:
            True if authentication succeeded
        """
        # Wait for BUSY LOW before starting
        deadline = time.monotonic() + 0.100
        while self._lines.get_value(BUSY_PIN) == gpiod.line.Value.ACTIVE:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.001)

        # MFC_AUTHENTICATE: [0x0C][key 6B][keyType][blockNo][uid 4B] = 13 bytes
        cmd = [0x0C] + list(key) + [0x60, block] + list(uid[:4])
        self._cs_low()
        self._spi.xfer2(cmd)
        self._cs_high()

        # Wait for BUSY HIGH then LOW (auth can take up to 1s)
        self._wait_busy(timeout_s=1.0)

        # Read 1-byte response: 0x00 = success
        self._cs_low()
        response = self._spi.xfer2([0xFF])
        self._cs_high()

        return response[0] == 0x00

    def mfc_read_block(self, block: int) -> bytes | None:
        """Read a 16-byte MIFARE Classic block (must be authenticated first).

        Returns 16 bytes of block data, or None on failure.
        """
        # Clear IRQs
        self.write_reg(0x03, 0xFFFFFFFF)

        # Set transceive mode (Crypto1 stays active from MFC_AUTHENTICATE)
        self.set_transceive_mode()
        time.sleep(0.001)

        # Enable TX and RX CRC for encrypted read
        self.write_reg_or(0x19, 0x01)
        self.write_reg_or(0x12, 0x01)

        # Send MIFARE READ command: 0x30 + block number
        self.send_data([0x30, block])
        time.sleep(0.010)

        # Check RX status
        rx_status = self.read_reg(0x13)
        rx_len = rx_status & 0x1FF
        if rx_len != 16:
            return None

        return self.read_data(16)

    def ntag_read_pages(self, start_page: int, num_pages: int) -> bytes | None:
        """Read NTAG pages (4 bytes each). No authentication required.

        Uses NTAG READ command (0x30) which returns 4 pages (16 bytes) at a time.
        CRC must be disabled for NTAG reads.
        """
        # Disable CRC for NTAG
        self.write_reg_and(0x19, 0xFFFFFFFE)  # TX CRC off
        self.write_reg_and(0x12, 0xFFFFFFFE)  # RX CRC off

        result = bytearray()
        pages_read = 0
        while pages_read < num_pages:
            self.write_reg(0x03, 0xFFFFFFFF)  # Clear IRQs
            self.set_transceive_mode()
            time.sleep(0.001)

            # READ command: 0x30 + page number → returns 16 bytes (4 pages)
            self.send_data([0x30, start_page + pages_read])
            time.sleep(0.005)

            rx_status = self.read_reg(0x13)
            rx_len = rx_status & 0x1FF
            if rx_len < 16:
                return None

            data = self.read_data(16)
            # Copy only the pages we need
            pages_to_copy = min(4, num_pages - pages_read)
            result.extend(data[: pages_to_copy * 4])
            pages_read += 4  # Always advances by 4 (READ returns 4 pages)

        return bytes(result)

    def reactivate_card(self) -> tuple[bytes, int] | None:
        """RF cycle and full re-select of the card. Returns (uid, sak) or None."""
        self.rf_off()
        time.sleep(0.010)

        self.write_reg(0x03, 0xFFFFFFFF)  # Clear IRQs
        self.load_rf_config(0x00, 0x80)  # ISO 14443A
        time.sleep(0.005)

        self.rf_on()
        time.sleep(0.020)

        return self.activate_type_a()

    def read_bambu_tag(self, uid: bytes) -> dict[int, bytes] | None:
        """Read Bambu tag data blocks using HKDF-derived keys.

        Args:
            uid: 4-byte tag UID (from activate_type_a)
        Returns:
            Dict mapping block number -> 16 bytes of data, or None on failure
        """
        # Derive per-sector keys from UID
        keys = hkdf_derive_keys(uid)

        # Clear Crypto1 state and IRQs
        self.write_reg_and(0x00, 0xFFFFFFBF)  # Clear MFC_CRYPTO1_ON (bit 6)
        self.write_reg(0x03, 0xFFFFFFFF)

        # Reactivate card (may have timed out)
        result = self.reactivate_card()
        if result is None:
            print("    Failed to reactivate card")
            return None

        uid_check, _ = result
        if uid_check != uid:
            print(f"    UID mismatch after reactivation: {uid_check.hex()} != {uid.hex()}")
            return None

        # Read blocks with per-sector authentication
        blocks = {}
        current_sector = -1

        for block in BAMBU_BLOCKS:
            sector = block // 4

            # Authenticate when entering a new sector
            if sector != current_sector:
                key = get_sector_key(keys, block)
                if not self.mfc_authenticate(block, key, uid):
                    print(f"    Auth failed for block {block} (sector {sector})")
                    return None
                current_sector = sector

            # Read the block
            data = self.mfc_read_block(block)
            if data is None:
                print(f"    Read failed for block {block}")
                return None
            blocks[block] = data

        return blocks

    def read_ntag(self, uid: bytes) -> bytes | None:
        """Read NTAG pages 4-20 (NDEF data area, 68 bytes). No auth needed.

        Used for SpoolEase / OpenPrintTag community tags.
        """
        # Reactivate card
        result = self.reactivate_card()
        if result is None:
            print("    Failed to reactivate card")
            return None

        return self.ntag_read_pages(start_page=4, num_pages=17)


def _print_hex_dump(data: bytes, label: str, bytes_per_line: int = 16):
    """Print a hex dump with ASCII sidebar."""
    for i in range(0, len(data), bytes_per_line):
        chunk = data[i : i + bytes_per_line]
        hex_str = " ".join(f"{b:02X}" for b in chunk)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"    {label}{i:3d}: {hex_str:<{bytes_per_line * 3}}|{ascii_str}|")


def main():
    print("=" * 60)
    print("PN5180 NFC Tag Reader")
    print("  Supports: Bambu (MIFARE Classic) + NTAG (SpoolEase/OpenPrintTag)")
    print("=" * 60)

    nfc = PN5180()
    try:
        nfc.reset()
        ver = nfc.read_eeprom(0x10, 2)
        print(f"[1] Reset OK — product v{ver[1]}.{ver[0]}")

        nfc.load_rf_config(0x00, 0x80)  # ISO 14443A
        time.sleep(0.010)
        nfc.rf_on()
        time.sleep(0.030)
        nfc.set_transceive_mode()

        rf = nfc.read_reg(0x1D)
        print(f"[2] RF ON  (RF_STATUS=0x{rf:08X}, TX_RF={'ON' if rf & 1 else 'OFF'})")

        print("[3] Scanning for tag...")
        result = nfc.activate_type_a()

        if result is None:
            print("    No tag found.")
            sys.exit(1)

        uid, sak = result
        tag_types = {0x00: "NTAG", 0x08: "MIFARE Classic 1K", 0x18: "MIFARE Classic 4K"}
        print(f"    UID : {uid.hex().upper()}")
        print(f"    SAK : 0x{sak:02X} ({tag_types.get(sak, 'Unknown')})")

        if sak in (0x08, 0x18):
            # MIFARE Classic 1K or 4K — Bambu Lab tag
            print("[4] Reading Bambu tag data (MIFARE Classic)...")
            blocks = nfc.read_bambu_tag(uid)

            if blocks is None:
                print("    Failed to read tag data.")
                nfc.rf_off()
                sys.exit(1)

            print("[5] Tag data:")
            for block_num in BAMBU_BLOCKS:
                data = blocks[block_num]
                hex_str = " ".join(f"{b:02X}" for b in data)
                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                print(f"    Block {block_num:2d}: {hex_str}  |{ascii_str}|")

            raw = b""
            for block_num in BAMBU_BLOCKS:
                raw += blocks[block_num]
            print(f"\n    Raw payload ({len(raw)} bytes): {raw.hex().upper()}")

        elif sak == 0x00:
            # NTAG — SpoolEase / OpenPrintTag
            print("[4] Reading NTAG data (pages 4-20)...")
            ntag_data = nfc.read_ntag(uid)

            if ntag_data is None:
                print("    Failed to read NTAG data.")
                nfc.rf_off()
                sys.exit(1)

            print(f"[5] NTAG data ({len(ntag_data)} bytes):")
            _print_hex_dump(ntag_data, "page ")

        else:
            print(f"    Unsupported tag type (SAK=0x{sak:02X})")
            nfc.rf_off()
            sys.exit(1)

        nfc.rf_off()
        print("\n" + "=" * 60)
        print("Tag read complete!")
        print("=" * 60)

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        nfc.close()


if __name__ == "__main__":
    main()
