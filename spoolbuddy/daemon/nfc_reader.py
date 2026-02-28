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
            from read_tag import PN5180

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
                tag_type = "mifare_classic" if sak in (0x08, 0x18) else "ntag" if sak == 0x00 else "unknown"

                if sak in (0x08, 0x18):
                    blocks = self._nfc.read_bambu_tag(uid_bytes)
                    if blocks:
                        tray_uuid = _extract_tray_uuid(blocks)

                logger.info("Tag detected: %s (SAK=0x%02X, type=%s)", uid_hex, sak, tag_type)
                return "tag_detected", {
                    "tag_uid": uid_hex,
                    "sak": sak,
                    "tag_type": tag_type,
                    "tray_uuid": tray_uuid,
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
    """Extract tray_uuid from Bambu MIFARE Classic data blocks."""
    # Block 4-5 contain the 32-char tray UUID (first 16 bytes from block 4 + 5)
    if 4 in blocks and 5 in blocks:
        raw = blocks[4] + blocks[5]
        # UUID is stored as ASCII hex in the first 16 bytes of blocks 4-5
        uuid_bytes = raw[:16]
        try:
            uuid_str = uuid_bytes.hex().upper()
            if uuid_str and uuid_str != "0" * 32:
                return uuid_str
        except Exception:
            pass
    return None
