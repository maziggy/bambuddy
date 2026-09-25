"""Tests for daemon.barcode_reader — USB HID barcode scanner capture.

evdev is Linux-only and not installed on dev/CI machines, so these tests inject
a fake `evdev`/`ecodes` into the module and drive the decode/detection/loop
logic directly.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import daemon.barcode_reader as br
import pytest
from daemon.barcode_reader import BarcodeReader

# --- Fake evdev ------------------------------------------------------------

# Standard Linux input-event keycodes (linux/input-event-codes.h).
_KEYS = {
    "KEY_1": 2,
    "KEY_2": 3,
    "KEY_3": 4,
    "KEY_4": 5,
    "KEY_5": 6,
    "KEY_6": 7,
    "KEY_7": 8,
    "KEY_8": 9,
    "KEY_9": 10,
    "KEY_0": 11,
    "KEY_MINUS": 12,
    "KEY_EQUAL": 13,
    "KEY_ENTER": 28,
    "KEY_KPENTER": 96,
    "KEY_LEFTSHIFT": 42,
    "KEY_RIGHTSHIFT": 54,
    "KEY_DOT": 52,
    "KEY_SLASH": 53,
    "KEY_SPACE": 57,
    "KEY_RIGHTBRACE": 27,
    "KEY_SEMICOLON": 39,
}
# Letters KEY_A..KEY_Z on a US layout are not contiguous; explicit map.
_LETTER_CODES = {
    "A": 30,
    "B": 48,
    "C": 46,
    "D": 32,
    "E": 18,
    "F": 33,
    "G": 34,
    "H": 35,
    "I": 23,
    "J": 36,
    "K": 37,
    "L": 38,
    "M": 50,
    "N": 49,
    "O": 24,
    "P": 25,
    "Q": 16,
    "R": 19,
    "S": 31,
    "T": 20,
    "U": 22,
    "V": 47,
    "W": 17,
    "X": 45,
    "Y": 21,
    "Z": 44,
}


def _make_ecodes():
    ns = SimpleNamespace(EV_KEY=1, EV_REL=2, EV_ABS=3, EV_SYN=0)
    for name, code in _KEYS.items():
        setattr(ns, name, code)
    for letter, code in _LETTER_CODES.items():
        setattr(ns, f"KEY_{letter}", code)
    for d in "1234567890":
        setattr(ns, f"KEY_KP{d}", 200 + int(d))  # arbitrary, distinct
    return ns


ECODES = _make_ecodes()
# Reverse maps for building key events by character.
_CHAR_TO_CODE = {}
for d in "1234567890":
    _CHAR_TO_CODE[d] = getattr(ECODES, f"KEY_{d}")
for letter, code in _LETTER_CODES.items():
    _CHAR_TO_CODE[letter.lower()] = code
_CHAR_TO_CODE["]"] = ECODES.KEY_RIGHTBRACE
_CHAR_TO_CODE["/"] = ECODES.KEY_SLASH
_CHAR_TO_CODE["."] = ECODES.KEY_DOT


class FakeEvent:
    def __init__(self, type_, code, value):
        self.type = type_
        self.code = code
        self.value = value


class FakeInputDevice:
    # Real evdev.InputDevice takes the device path as the first positional arg.
    def __init__(self, path="/dev/input/event5", name="Barcode Scanner", caps=None):
        self.name = name
        self.path = path
        self.fd = 42
        self._caps = caps if caps is not None else {ECODES.EV_KEY: [2, 28]}
        self.grabbed = False
        self.closed = False
        self._queue = []

    def capabilities(self):
        return self._caps

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        self.closed = True

    def read_one(self):
        return self._queue.pop(0) if self._queue else None

    # test helper: enqueue a full scan of `text` ending in Enter
    def enqueue_scan(self, text, terminator=True):
        for ch in text:
            shifted = ch.isupper()
            if shifted:
                self._queue.append(FakeEvent(ECODES.EV_KEY, ECODES.KEY_LEFTSHIFT, 1))
            code = _CHAR_TO_CODE[ch.lower()]
            self._queue.append(FakeEvent(ECODES.EV_KEY, code, 1))
            self._queue.append(FakeEvent(ECODES.EV_KEY, code, 0))
            if shifted:
                self._queue.append(FakeEvent(ECODES.EV_KEY, ECODES.KEY_LEFTSHIFT, 0))
        if terminator:
            self._queue.append(FakeEvent(ECODES.EV_KEY, ECODES.KEY_ENTER, 1))


@pytest.fixture
def patched_evdev(monkeypatch):
    """Install fake evdev/ecodes into the barcode_reader module."""
    fake_evdev = SimpleNamespace(
        InputDevice=FakeInputDevice,
        list_devices=lambda: ["/dev/input/event5"],
        ecodes=ECODES,
    )
    monkeypatch.setattr(br, "evdev", fake_evdev)
    monkeypatch.setattr(br, "ecodes", ECODES)
    return fake_evdev


def _reader_with_device(device):
    r = BarcodeReader()
    r._device = device
    r._keymap = br._build_keycode_map(ECODES)
    r.ok = True
    return r


# --- Decoding --------------------------------------------------------------


class TestDecode:
    def test_digits_with_enter_terminator(self, patched_evdev):
        dev = FakeInputDevice()
        dev.enqueue_scan("6975337031234")
        r = _reader_with_device(dev)
        assert r._drain() == "6975337031234"

    def test_shift_produces_uppercase_sku(self, patched_evdev):
        dev = FakeInputDevice()
        dev.enqueue_scan("X0043ABC12")
        r = _reader_with_device(dev)
        assert r._drain() == "X0043ABC12"

    def test_timeout_flush_without_enter(self, patched_evdev, monkeypatch):
        dev = FakeInputDevice()
        dev.enqueue_scan("12345678", terminator=False)
        r = _reader_with_device(dev)
        # First drain consumes the events but has no terminator → returns None
        assert r._drain() is None
        # Simulate the inter-char timeout elapsing, then drain again → flush.
        monkeypatch.setattr(br.time, "monotonic", lambda: r._last_key + br.CHAR_TIMEOUT_S + 1)
        assert r._drain() == "12345678"

    def test_ignores_key_up_and_autorepeat(self, patched_evdev):
        dev = FakeInputDevice()
        # value=2 is autorepeat, value=0 is key-up — neither should register.
        dev._queue = [
            FakeEvent(ECODES.EV_KEY, ECODES.KEY_1, 2),
            FakeEvent(ECODES.EV_KEY, ECODES.KEY_1, 0),
            FakeEvent(ECODES.EV_KEY, ECODES.KEY_2, 1),
            FakeEvent(ECODES.EV_KEY, ECODES.KEY_ENTER, 1),
        ]
        r = _reader_with_device(dev)
        assert r._drain() == "2"


# --- AIM symbology prefix ---------------------------------------------------


class TestSplitAimPrefix:
    def test_ean_upc_prefix(self):
        assert br.split_aim_prefix("]E06975337031234") == ("ean-upc", "6975337031234")

    def test_itf_prefix(self):
        assert br.split_aim_prefix("]I016975337031231") == ("itf", "16975337031231")

    def test_code128_prefix(self):
        assert br.split_aim_prefix("]C06975337031234") == ("code128", "6975337031234")

    def test_unknown_code_char_passes_through_raw(self):
        # Unmapped AIM letter: still stripped, family reported verbatim.
        assert br.split_aim_prefix("]X0ABC123") == ("X", "ABC123")

    def test_no_prefix_is_untouched(self):
        assert br.split_aim_prefix("6975337031234") == (None, "6975337031234")

    def test_non_digit_modifier_is_not_a_prefix(self):
        # AIM modifier is a digit; "]EX..." is just data.
        assert br.split_aim_prefix("]EXTRA") == (None, "]EXTRA")

    def test_bare_prefix_without_data_is_untouched(self):
        assert br.split_aim_prefix("]E0") == (None, "]E0")

    def test_decodes_from_hid_events(self, patched_evdev):
        # The scanner "types" the prefix — "]" must survive the keymap.
        dev = FakeInputDevice()
        dev.enqueue_scan("]E06975337031234")
        r = _reader_with_device(dev)
        assert r._drain() == "]E06975337031234"


# --- QR / URL payload ignore -------------------------------------------------


class TestUrlPayloadIgnore:
    def test_bambu_qr_without_colon_is_url(self):
        # Real-world shape: HID keymaps without ":" decode the Bambu spool QR
        # colon-less.
        assert br.looks_like_url_payload("HTTPS//E.BAMBULAB.COM/T?C=SMY5WWK01KBZL4RN") is True

    def test_full_url_is_url(self):
        assert br.looks_like_url_payload("https://example.com/x") is True

    def test_www_is_url(self):
        assert br.looks_like_url_payload("www.example.com") is True

    def test_gtin_is_not_url(self):
        assert br.looks_like_url_payload("6975337031234") is False

    def test_user_code_with_slash_free_shape_is_not_url(self):
        assert br.looks_like_url_payload("MyShelf-a42") is False


# --- Accept / debounce / length -------------------------------------------


class TestAccept:
    def test_rejects_too_short(self, patched_evdev):
        r = _reader_with_device(FakeInputDevice())
        assert r._accept("12") is False

    def test_accepts_normal(self, patched_evdev):
        r = _reader_with_device(FakeInputDevice())
        assert r._accept("6975337031234") is True

    def test_debounces_duplicate_within_window(self, patched_evdev, monkeypatch):
        r = _reader_with_device(FakeInputDevice())
        t = [1000.0]
        monkeypatch.setattr(br.time, "monotonic", lambda: t[0])
        assert r._accept("6975337031234") is True
        t[0] += 0.5  # within SCAN_DEBOUNCE_S
        assert r._accept("6975337031234") is False
        t[0] += br.SCAN_DEBOUNCE_S + 1  # past the window
        assert r._accept("6975337031234") is True


# --- Detection -------------------------------------------------------------


class TestDetection:
    def test_accepts_name_match(self, patched_evdev):
        r = BarcodeReader()
        assert r._matches(FakeInputDevice(name="SM SM-2D PRODUCT HID KBW")) is True

    def test_rejects_touchscreen_even_if_named(self, patched_evdev):
        # A device with absolute axes (touchscreen) is never grabbed.
        dev = FakeInputDevice(name="Goodix Barcode Touch", caps={ECODES.EV_KEY: [2], ECODES.EV_ABS: [0]})
        r = BarcodeReader()
        assert r._matches(dev) is False

    def test_rejects_unmatched_keyboard(self, patched_evdev):
        r = BarcodeReader()
        assert r._matches(FakeInputDevice(name="Dell USB Keyboard")) is False

    def test_open_grabs_device(self, patched_evdev):
        r = BarcodeReader()
        assert r.open() is True
        assert r.ok is True
        assert r._device.grabbed is True
        assert r.device_name == "Barcode Scanner"

    def test_close_ungrabs_and_closes(self, patched_evdev):
        r = BarcodeReader()
        r.open()
        dev = r._device
        r.close()
        assert dev.grabbed is False
        assert dev.closed is True
        assert r.ok is False


# --- Run loop --------------------------------------------------------------


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_scan_invokes_callback(self, patched_evdev):
        dev = FakeInputDevice()
        dev.enqueue_scan("6975337031234")
        r = BarcodeReader()
        r._device = dev
        r._keymap = br._build_keycode_map(ECODES)
        r.ok = True
        r._wait_readable = lambda timeout: True  # fd 42 isn't a real socket

        scanned = []
        on_scan = AsyncMock(side_effect=lambda code, symbology: scanned.append((code, symbology)))

        task = asyncio.create_task(r.run(on_scan))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert scanned == [("6975337031234", None)]

    @pytest.mark.asyncio
    async def test_aim_prefix_is_stripped_and_reported(self, patched_evdev):
        dev = FakeInputDevice()
        dev.enqueue_scan("]E06975337031234")
        r = BarcodeReader()
        r._device = dev
        r._keymap = br._build_keycode_map(ECODES)
        r.ok = True
        r._wait_readable = lambda timeout: True

        scanned = []
        on_scan = AsyncMock(side_effect=lambda code, symbology: scanned.append((code, symbology)))

        task = asyncio.create_task(r.run(on_scan))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert scanned == [("6975337031234", "ean-upc")]

    @pytest.mark.asyncio
    async def test_qr_and_url_scans_are_dropped_but_loop_continues(self, patched_evdev):
        dev = FakeInputDevice()
        dev.enqueue_scan("]Q1HTTPS//E.BAMBULAB.COM/TCX")  # AIM says QR
        dev.enqueue_scan("HTTPS//E.BAMBULAB.COM/TCY")  # no AIM, URL shape
        dev.enqueue_scan("6975337031234")  # real barcode still gets through
        r = BarcodeReader()
        r._device = dev
        r._keymap = br._build_keycode_map(ECODES)
        r.ok = True
        r._wait_readable = lambda timeout: True

        scanned = []
        on_scan = AsyncMock(side_effect=lambda code, symbology: scanned.append((code, symbology)))

        task = asyncio.create_task(r.run(on_scan))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert scanned == [("6975337031234", None)]

    @pytest.mark.asyncio
    async def test_disabled_releases_grab(self, patched_evdev):
        r = BarcodeReader()
        r.open()
        assert r.ok is True

        on_scan = AsyncMock()
        task = asyncio.create_task(r.run(on_scan, is_enabled=lambda: False))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Grab released while disabled.
        assert r.ok is False
