"""USB barcode scanner reader — keyboard-wedge (HID) capture via evdev.

The scanner (e.g. DE2120 breakout) presents as a USB keyboard and "types"
the barcode followed by Enter. Reading it through evdev with an exclusive
grab (EVIOCGRAB) keeps those keystrokes out of the kiosk browser entirely,
so a scan can never spill digits into whatever field happens to be focused.

Detection requires a device-name match — a capability heuristic alone can't
tell a scanner from a real keyboard, and grabbing the user's keyboard would
silently eat their input. (Like the NFC and scale readers, there's no device
override knob; the scanner is auto-detected by name.)
"""

import logging
import re
import select
import time

try:
    import evdev
    from evdev import ecodes
except ImportError:  # pragma: no cover - exercised via tests with fakes
    evdev = None
    ecodes = None

import asyncio

logger = logging.getLogger(__name__)

# Case-insensitive substrings of device names we trust as barcode scanners.
# The DE2120 enumerates as "SM SM-2D PRODUCT HID KBW".
BARCODE_NAME_PATTERNS = (
    "barcode",
    "de2120",
    "scanner",
    "kbw",
    "sm-2d",
    "sparkfun",
    "newland",
    "honeywell",
    "symbol",
    "datalogic",
    "zebra",
)

MIN_SCAN_LENGTH = 4
MAX_SCAN_LENGTH = 64  # matches Spool.barcode VARCHAR(64)
SCAN_DEBOUNCE_S = 2.0  # IR auto-trigger can re-fire on the same box
CHAR_TIMEOUT_S = 0.15  # flush terminator for scanners configured without a suffix
RESCAN_INTERVAL_S = 5.0  # how often to look for a (re)plugged scanner
IDLE_SELECT_S = 0.5  # blocking-select window while no scan is in progress


# AIM symbology identifiers (ISO/IEC 15424): "]" + a code character + a
# modifier digit, prepended to the data only when the scanner is configured
# to transmit them (a config barcode in the DE2120 manual enables it). With
# the prefix present the backend KNOWS the symbology — e.g. a true EAN/UPC
# read versus a Code 128 wrapping an arbitrary numeric string — instead of
# inferring GTIN-ness from the digits alone.
_AIM_FAMILIES = {
    "E": "ean-upc",  # EAN-13 / EAN-8 / UPC-A / UPC-E
    "I": "itf",  # Interleaved 2 of 5 (ITF-14 case codes carry GTINs)
    "C": "code128",
    "A": "code39",
    "Q": "qr",
    "d": "datamatrix",
}


# 2D symbologies never carry product codes on filament boxes — the Bambu
# spool QR is a URL — so scans of them are dropped outright (with AIM IDs
# enabled the scanner tells us; without them the URL shape below catches the
# common case).
IGNORED_SYMBOLOGIES = ("qr", "datamatrix")

# URL-shaped payloads. The optional colon matters: HID keymaps that can't
# type ":" decode the Bambu QR as "HTTPS//E.BAMBULAB.COM/…".
_URL_PAYLOAD_RE = re.compile(r"(?i)^\s*(https?:?//|www\.)")


def looks_like_url_payload(code: str) -> bool:
    """True when a decoded scan is a URL (a QR payload), not a product code."""
    return bool(_URL_PAYLOAD_RE.match(code)) or "://" in code


def split_aim_prefix(code: str) -> tuple[str | None, str]:
    """Split a leading AIM symbology identifier off a decoded scan.

    Returns (symbology_family, remaining_code). Scanners not configured to
    send AIM IDs are unaffected: anything that doesn't match the strict
    ``]Xn`` + data shape passes through untouched as (None, code).
    """
    if len(code) >= 4 and code[0] == "]" and code[2].isdigit():
        return _AIM_FAMILIES.get(code[1], code[1]), code[3:]
    return None, code


def _build_keycode_map(codes) -> dict[int, tuple[str, str]]:
    """Map evdev keycodes to (plain, shifted) characters.

    Covers GTIN digits plus letters and common symbols so alphanumeric
    SKUs (e.g. Amazon "X0043ABC12") decode too.
    """
    keymap: dict[int, tuple[str, str]] = {}
    for digit, shifted in zip("1234567890", "!@#$%^&*()", strict=True):
        keymap[getattr(codes, f"KEY_{digit}")] = (digit, shifted)
        kp = getattr(codes, f"KEY_KP{digit}", None)
        if kp is not None:
            keymap[kp] = (digit, digit)
    for ch in "abcdefghijklmnopqrstuvwxyz":
        keymap[getattr(codes, f"KEY_{ch.upper()}")] = (ch, ch.upper())
    keymap[codes.KEY_MINUS] = ("-", "_")
    # "]" opens the AIM symbology identifier (e.g. "]E0") — without this the
    # prefix would decode as a bare "E0" glued onto the barcode digits.
    keymap[codes.KEY_RIGHTBRACE] = ("]", "}")
    # ":" so URL payloads (QR codes) decode faithfully for the ignore filter.
    keymap[codes.KEY_SEMICOLON] = (";", ":")
    keymap[codes.KEY_DOT] = (".", ">")
    keymap[codes.KEY_SLASH] = ("/", "?")
    keymap[codes.KEY_EQUAL] = ("=", "+")
    keymap[codes.KEY_SPACE] = (" ", " ")
    return keymap


class BarcodeReader:
    """Detects, exclusively grabs, and decodes a USB HID barcode scanner."""

    def __init__(self):
        self._device = None
        self._keymap: dict[int, tuple[str, str]] = {}
        self._chars: list[str] = []
        self._shift = False
        self._last_key = 0.0
        self._last_code = ""
        self._last_code_ts = 0.0
        self.ok = False
        self.device_name: str | None = None

    # -- detection / lifecycle -------------------------------------------------

    def _matches(self, device) -> bool:
        name = (device.name or "").lower()
        if not any(pattern in name for pattern in BARCODE_NAME_PATTERNS):
            return False
        # Sanity: must look like a keyboard (keys, no pointer/touch axes) so a
        # coincidentally-named touchscreen or mouse can never be grabbed.
        caps = device.capabilities()
        if ecodes.EV_ABS in caps or ecodes.EV_REL in caps:
            return False
        return ecodes.EV_KEY in caps

    def _find_device(self):
        try:
            paths = evdev.list_devices()
        except Exception as e:
            logger.debug("Could not list input devices: %s", e)
            return None
        for path in paths:
            try:
                device = evdev.InputDevice(path)
            except OSError:
                continue
            try:
                if self._matches(device):
                    return device
            except Exception:
                pass
            try:
                device.close()
            except Exception:
                pass
        return None

    def open(self) -> bool:
        """Find and exclusively grab the scanner. Safe to call repeatedly."""
        if self.ok:
            return True
        if evdev is None:
            return False
        device = self._find_device()
        if device is None:
            return False
        try:
            device.grab()
        except OSError as e:
            # EBUSY: something else grabbed it — retry on the next rescan.
            logger.warning("Could not grab barcode scanner %s: %s", device.name, e)
            try:
                device.close()
            except Exception:
                pass
            return False
        self._device = device
        self._keymap = _build_keycode_map(ecodes)
        self._reset_scan()
        self.device_name = device.name
        self.ok = True
        logger.info("Barcode scanner detected: %s (%s)", device.name, device.path)
        return True

    def close(self):
        device = self._device
        self._device = None
        self.ok = False
        if device is not None:
            try:
                device.ungrab()
            except Exception:
                pass
            try:
                device.close()
            except Exception:
                pass
        self._reset_scan()

    # -- decoding --------------------------------------------------------------

    def _reset_scan(self):
        self._chars = []
        self._shift = False

    def _feed(self, event) -> str | None:
        """Process one input event; returns the completed code on Enter."""
        if event.type != ecodes.EV_KEY:
            return None
        if event.code in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            self._shift = event.value != 0
            return None
        if event.value != 1:  # only key-down; ignore key-up and autorepeat
            return None
        if event.code in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
            code = "".join(self._chars)
            self._reset_scan()
            return code
        pair = self._keymap.get(event.code)
        if pair is None:
            return None
        self._chars.append(pair[1] if self._shift else pair[0])
        self._last_key = time.monotonic()
        if len(self._chars) > MAX_SCAN_LENGTH:
            # Runaway input — flush so the sanity filter can reject it.
            code = "".join(self._chars)
            self._reset_scan()
            return code
        return None

    def _drain(self) -> str | None:
        """Consume all pending events; returns a completed scan, if any."""
        while True:
            event = self._device.read_one()
            if event is None:
                break
            code = self._feed(event)
            if code is not None:
                return code
        if self._chars and (time.monotonic() - self._last_key) >= CHAR_TIMEOUT_S:
            code = "".join(self._chars)
            self._reset_scan()
            return code
        return None

    def _accept(self, code: str) -> bool:
        if not (MIN_SCAN_LENGTH <= len(code) <= MAX_SCAN_LENGTH):
            logger.debug("Ignoring scan with implausible length %d", len(code))
            return False
        now = time.monotonic()
        if code == self._last_code and (now - self._last_code_ts) < SCAN_DEBOUNCE_S:
            logger.debug("Ignoring duplicate scan within debounce window")
            return False
        self._last_code = code
        self._last_code_ts = now
        return True

    def _wait_readable(self, timeout: float) -> bool:
        r, _, _ = select.select([self._device.fd], [], [], timeout)
        return bool(r)

    # -- main loop -------------------------------------------------------------

    async def run(self, on_scan, is_enabled=None):
        """Forever-loop: detect scanner, decode scans, invoke ``on_scan(code)``.

        ``is_enabled`` (optional callable) gates capture — while it returns
        False the device is released entirely (grab dropped) so disabling the
        scanner in Settings truly stops it from swallowing input.
        """
        while True:
            if is_enabled is not None and not is_enabled():
                if self.ok:
                    logger.info("Barcode scanner disabled — releasing device")
                    self.close()
                await asyncio.sleep(RESCAN_INTERVAL_S)
                continue

            if not self.ok:
                opened = await asyncio.to_thread(self.open)
                if not opened:
                    await asyncio.sleep(RESCAN_INTERVAL_S)
                    continue

            try:
                timeout = CHAR_TIMEOUT_S if self._chars else IDLE_SELECT_S
                await asyncio.to_thread(self._wait_readable, timeout)
                code = self._drain()
            except OSError as e:
                logger.warning("Barcode scanner read failed (disconnected?): %s", e)
                self.close()
                await asyncio.sleep(1.0)
                continue

            if code:
                symbology, code = split_aim_prefix(code)
                if symbology in IGNORED_SYMBOLOGIES or looks_like_url_payload(code):
                    logger.debug("Ignoring 2D/URL scan: %s", code)
                elif self._accept(code):
                    logger.info("Barcode scanned: %s", code)
                    try:
                        await on_scan(code, symbology)
                    except Exception:
                        logger.exception("Barcode scan handler failed")
