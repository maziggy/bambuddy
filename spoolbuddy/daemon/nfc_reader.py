"""NFC reader wrapper with state machine for tag presence detection."""

import logging
import time
from enum import Enum, auto

logger = logging.getLogger(__name__)

MISS_THRESHOLD = 3  # Consecutive misses before declaring tag removed
ERROR_RECOVERY_THRESHOLD = 10  # Consecutive errors before attempting RF reset


class NFCState(Enum):
    IDLE = auto()
    TAG_PRESENT = auto()


class NFCReader:
    def __init__(self):
        self._nfc = None
        self._state = NFCState.IDLE
        self._current_uid: str | None = None
        self._current_sak: int | None = None
        self._miss_count = 0
        self._ok = False
        self._error_count = 0
        self._poll_count = 0
        self._last_status_log = 0.0

        try:
            from .pn5180 import PN5180

            self._nfc = PN5180()
            self._init_rf()
            self._ok = True
            logger.info("NFC reader initialized")
        except Exception as e:
            logger.warning("NFC not available: %s", e)

    def _init_rf(self):
        """Full RF initialization sequence."""
        self._nfc.reset()
        self._nfc.load_rf_config(0x00, 0x80)
        time.sleep(0.010)
        self._nfc.rf_on()
        time.sleep(0.030)
        self._nfc.set_transceive_mode()

    def _full_reset(self):
        """Full hardware reset + RF init to recover from stuck state."""
        try:
            self._init_rf()
            self._error_count = 0
            logger.info("NFC reader recovered after full reset")
            return True
        except Exception as e:
            logger.warning("NFC full reset failed: %s", e)
            return False

    @property
    def reader_type(self) -> str:
        """Return NFC reader hardware type."""
        return "PN5180" if self._nfc is not None else "Unknown"

    @property
    def connection(self) -> str:
        """Return NFC reader connection type."""
        return "SPI" if self._nfc is not None else "None"

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def state(self) -> NFCState:
        return self._state

    @property
    def current_uid(self) -> str | None:
        return self._current_uid

    def close(self):
        try:
            self._nfc.rf_off()
            self._nfc.close()
        except Exception:
            pass

    @property
    def current_sak(self) -> int | None:
        return self._current_sak

    def write_ntag(self, data: bytes, data_v2: bytes | None = None) -> tuple[bool, str]:
        """Write raw NDEF bytes to currently present NTAG tag.

        *data* is OpenTag3D v1, which fits every NTAG. *data_v2* (OpenTag3D
        2.004, #3500) is written instead when the tag's Capability Container
        says it has room for it; v2 does not fit an NTAG213.

        The Capability Container is read first: a tag whose CC is not NDEF
        (a blank MIFARE Ultralight also answers with SAK 0x00) or whose data
        area is smaller than the payload is refused rather than written past
        its end (#3076). Afterwards the pages are read back, since the PN5180
        cannot see the tag's ACK: data that reads back different fails the
        write. A read-back that gets no answer leaves it "not verified".

        Requires tag in TAG_PRESENT state with SAK=0x00.
        Returns (success, message).
        """
        if self._state != NFCState.TAG_PRESENT:
            return False, "No tag present"
        if self._current_sak not in (0x00, 0x04):
            return False, f"Not an NTAG (SAK=0x{self._current_sak:02X})"
        if not self._nfc:
            return False, "NFC reader not available"

        try:
            # Reactivate card before writing
            result = self._nfc.reactivate_card()
            if result is None:
                return False, "Failed to reactivate card for write"

            uid_bytes, sak = result
            if uid_bytes.hex().upper() != self._current_uid:
                return False, "Tag UID changed during write"

            # Page 3 is the Capability Container; READ returns pages 3-6.
            try:
                cc_pages = self._nfc.ntag_read_pages(start_page=3, num_pages=1)
            except Exception as e:
                logger.warning("Could not read the tag's Capability Container: %s", e)
                cc_pages = None

            version = "v1"
            if data_v2 is not None:
                data, version = choose_ndef(cc_pages, data, data_v2)
            refusal = capacity_refusal(cc_pages, len(data))
            if refusal:
                logger.warning("NTAG write refused for tag %s: %s", self._current_uid, refusal)
                return False, refusal

            # A fresh select before writing, as the write path always had.
            if self._nfc.reactivate_card() is None:
                return False, "Failed to reactivate card for write"

            # Write starting at page 4
            if not self._nfc.ntag_write_pages(start_page=4, data=data):
                return False, "Write failed"

            verified, mismatch = self._verify_written(data)
            if verified is False:
                return False, mismatch
            note = "" if verified else ", not verified"
            logger.info(
                "NTAG write successful%s: %d bytes (OpenTag3D %s) to tag %s",
                note,
                len(data),
                version,
                self._current_uid,
            )
            return True, f"Write successful (OpenTag3D {version}{note})"
        except Exception as e:
            logger.error("NTAG write error: %s", e)
            return False, f"Write error: {e}"

    def _verify_written(self, data: bytes) -> tuple[bool | None, str]:
        """Read the written pages back and compare (#3076).

        (True, "") when they match, (False, where) when they differ, (None, "")
        when the tag could not be read back. The PN5180 used to
        get no answer to a READ right after a batch write, so the tag is
        selected afresh first; a read that still fails proves nothing either
        way, and the write keeps counting as done, as it did before.
        """
        padded = data + bytes(-len(data) % 4)
        try:
            if self._nfc.reactivate_card() is None:
                logger.warning("NTAG read-back skipped: the tag did not answer the reselect")
                return None, ""
            read = self._nfc.ntag_read_pages(start_page=4, num_pages=len(padded) // 4)
        except Exception as e:
            logger.warning("NTAG read-back failed: %s", e)
            return None, ""
        if read is None or len(read) < len(padded):
            logger.warning("NTAG read-back got no complete answer; the write is not verified")
            return None, ""
        for offset in range(0, len(padded), 4):
            if read[offset : offset + 4] != padded[offset : offset + 4]:
                page = 4 + offset // 4
                logger.warning(
                    "NTAG read-back mismatch at page %d: wrote %s, read %s",
                    page,
                    padded[offset : offset + 4].hex(),
                    read[offset : offset + 4].hex(),
                )
                return False, f"Verification failed at page {page}: the tag holds other data"
        return True, ""

    def poll(self) -> tuple[str, dict | None]:
        """Poll for tag. Returns (event_type, event_data).

        event_type: "none", "tag_detected", "tag_removed"
        """
        self._poll_count += 1

        # Periodic status log (every 60s)
        now = time.monotonic()
        if now - self._last_status_log >= 60.0:
            logger.info(
                "NFC status: state=%s, polls=%d, errors=%d, ok=%s",
                self._state.name,
                self._poll_count,
                self._error_count,
                self._ok,
            )
            self._last_status_log = now

        if self._state == NFCState.IDLE:
            # Full hardware reset before every idle poll. Each activate_type_a()
            # call that returns None corrupts the PN5180 state — subsequent calls
            # silently fail even when a tag is present. Only a full RST pin toggle
            # recovers the reader. ~240ms overhead per poll, giving ~1.8 Hz poll
            # rate which is fine for a spool tag reader.
            try:
                self._init_rf()
            except Exception as e:
                logger.warning("NFC pre-poll reset failed: %s", e)
        else:
            # Tag present: light RF cycle to reset card from ACTIVE back to IDLE
            # state after previous SELECT, so it responds to the next WUPA/REQA.
            try:
                self._nfc.rf_off()
                time.sleep(0.003)
                self._nfc.rf_on()
                time.sleep(0.010)
            except Exception:
                pass  # Will be caught by activate_type_a() error handling below

        try:
            result = self._nfc.activate_type_a()
        except Exception as e:
            self._error_count += 1
            self._ok = False

            if self._error_count == 1:
                logger.warning("NFC poll error: %s", e)
            elif self._error_count == ERROR_RECOVERY_THRESHOLD:
                logger.warning(
                    "NFC reader stuck (%d consecutive errors), attempting recovery...",
                    self._error_count,
                )
                if self._full_reset():
                    return "none", None
                # Reset failed — will keep trying on next threshold
                self._error_count = 0
            elif self._error_count % ERROR_RECOVERY_THRESHOLD == 0:
                logger.warning("NFC recovery attempt #%d", self._error_count // ERROR_RECOVERY_THRESHOLD)
                self._full_reset()

            return "none", None

        # Successful poll — clear error streak
        if self._error_count > 0:
            logger.info("NFC reader recovered after %d errors", self._error_count)
        self._error_count = 0
        self._ok = True

        if result is not None:
            uid_bytes, sak = result
            uid_hex = uid_bytes.hex().upper()
            self._miss_count = 0

            if self._state == NFCState.IDLE:
                self._state = NFCState.TAG_PRESENT
                self._current_uid = uid_hex
                self._current_sak = sak

                # Try reading Bambu tag data
                tray_uuid = None
                raw_blocks = None
                tag_type = "mifare_classic" if sak in (0x08, 0x18) else "ntag" if sak in (0x00, 0x04) else "unknown"

                if sak in (0x08, 0x18):
                    # A reader fault mid-read (a BUSY timeout, an SPI error) must
                    # not escape poll() and stop the loop: report the tag without
                    # its tray UUID, so matching falls back to the UID (#3377)
                    try:
                        blocks = self._nfc.read_bambu_tag(uid_bytes)
                    except Exception as e:
                        logger.warning("Bambu tag read failed for %s: %s", uid_hex, e)
                        blocks = None
                    if blocks:
                        tray_uuid = _extract_tray_uuid(blocks)
                        raw_blocks = _raw_blocks(blocks)

                logger.info("Tag detected: %s (SAK=0x%02X, type=%s)", uid_hex, sak, tag_type)
                return "tag_detected", {
                    "tag_uid": uid_hex,
                    "sak": sak,
                    "tag_type": tag_type,
                    "tray_uuid": tray_uuid,
                    "raw_blocks": raw_blocks,
                }

            # Tag still present — no event
            return "none", None

        # No tag found
        if self._state == NFCState.TAG_PRESENT:
            self._miss_count += 1
            if self._miss_count >= MISS_THRESHOLD:
                old_uid = self._current_uid
                self._state = NFCState.IDLE
                self._current_uid = None
                self._current_sak = None
                self._miss_count = 0
                logger.info("Tag removed: %s", old_uid)
                return "tag_removed", {"tag_uid": old_uid}

        return "none", None


def _extract_tray_uuid(blocks: dict[int, bytes]) -> str | None:
    """Extract tray_uuid from Bambu MIFARE Classic data blocks.

    The tray UUID is the 16 raw bytes of block 9, as 32 uppercase hex chars.
    Both tags of a spool carry the same value, and it is what the AMS reports
    as tray_uuid. Blocks 4-5 hold the filament type ("PLA Matte"), not the UUID:
    reading them made every spool of one type share an id (#984).
    """
    data = blocks.get(9)
    if not data or len(data) < 16:
        return None
    uuid_str = data[:16].hex().upper()
    if uuid_str == "0" * 32:
        return None
    return uuid_str


def capacity_refusal(cc_pages: bytes | None, size: int) -> str | None:
    """Why *size* bytes can't go onto this tag, or None (#3076).

    *cc_pages* starts with page 3, the Capability Container. NTAG21x tags come
    with an NDEF CC (E1, version, data area size / 8, access); a blank MIFARE
    Ultralight, which also reports SAK 0x00, has none and holds 48 bytes. An
    unreadable CC proves nothing, so the write goes ahead as it always did.
    """
    if not cc_pages or len(cc_pages) < 4:
        return None
    if cc_pages[0] != 0xE1:
        return "Not an NDEF-formatted NTAG tag. Use an NTAG213, 215 or 216."
    capacity = cc_pages[2] * 8
    if size > capacity:
        return f"Tag too small: {size} bytes to write, {capacity} bytes of space"
    return None


def choose_ndef(cc_pages: bytes | None, data_v1: bytes, data_v2: bytes) -> tuple[bytes, str]:
    """Pick the OpenTag3D encoding the tag has room for (#3500).

    *cc_pages* starts with page 3, the NDEF Capability Container
    (E1, version, data area size / 8, access). v2 is chosen only when the CC
    reads as NDEF and its data area holds the whole v2 message: an NTAG213
    (size byte 0x12, 144 bytes) gets v1, an NTAG215/216 (0x3E/0x6D) gets v2.
    Anything unreadable gets v1, which fits every NTAG.
    """
    if cc_pages and len(cc_pages) >= 4 and cc_pages[0] == 0xE1 and len(data_v2) <= cc_pages[2] * 8:
        return data_v2, "v2"
    return data_v1, "v1"


def _raw_blocks(blocks: dict[int, bytes]) -> dict[str, str]:
    """Bambu tag blocks as {"block number": "32 uppercase hex chars"}, for the backend to decode (#2685)."""
    return {str(block): data.hex().upper() for block, data in sorted(blocks.items())}
