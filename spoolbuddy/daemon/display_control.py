"""Display brightness and screen blanking control for SpoolBuddy kiosk.

Brightness: DSI backlights are controlled via sysfs /sys/class/backlight/*/brightness.
            HDMI brightness is handled by the frontend via CSS filter.
Blanking:   swayidle is the sole authority on screen blanking (idle timeout →
            wlopm --off, touch → wlopm --on).  The daemon wakes the display by
            writing to a FIFO that the idle watchdog monitors — the watchdog
            runs inside the Wayland session and calls wlopm --on on behalf of
            the daemon.  The same FIFO carries "reload-timeout N" lines: when
            the user changes the blank-timeout setting in the UI, the daemon
            picks up the new value over the heartbeat and signals the watchdog
            to kill+restart swayidle with the new timeout, so changes take
            effect live without a kiosk restart.
"""

import logging
import os
import stat
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BACKLIGHT_BASE = Path("/sys/class/backlight")
WAKE_FIFO = Path("/tmp/spoolbuddy-wake")


class DisplayControl:
    def __init__(self):
        self._backlight_path = self._find_backlight()
        self._max_brightness = self._read_max_brightness()
        self._blank_timeout = 0  # seconds, 0 = disabled
        self._timeout_initialized = False
        self._last_activity = time.monotonic()
        self._blanked = False

        if self._backlight_path:
            logger.info("Backlight found: %s (max=%d)", self._backlight_path, self._max_brightness)
        else:
            logger.info("No DSI backlight found, brightness control via frontend CSS")

    def _find_backlight(self) -> Path | None:
        if not BACKLIGHT_BASE.exists():
            return None
        for entry in BACKLIGHT_BASE.iterdir():
            brightness_file = entry / "brightness"
            if brightness_file.exists():
                return entry
        return None

    def _read_max_brightness(self) -> int:
        if not self._backlight_path:
            return 100
        try:
            return int((self._backlight_path / "max_brightness").read_text().strip())
        except Exception:
            return 255

    @property
    def has_backlight(self) -> bool:
        return self._backlight_path is not None

    def set_brightness(self, pct: int):
        """Set backlight brightness (0-100%). No-op if no backlight."""
        if not self._backlight_path:
            return
        pct = max(0, min(100, pct))
        value = round(self._max_brightness * pct / 100)
        try:
            (self._backlight_path / "brightness").write_text(str(value))
            logger.debug("Brightness set to %d%% (%d/%d)", pct, value, self._max_brightness)
        except PermissionError:
            logger.warning(
                "Permission denied writing to %s/brightness. Ensure spoolbuddy user is in the 'video' group.",
                self._backlight_path,
            )
        except Exception as e:
            logger.warning("Failed to set brightness: %s", e)

    def set_blank_timeout(self, seconds: int):
        """Set screen blank timeout in seconds. 0 = disabled.

        On every change after the first call, signals the idle watchdog
        (spoolbuddy-idle.sh) to restart swayidle with the new value.
        Without this, swayidle keeps running with whatever timeout it
        was started with at autostart and UI changes only take effect
        after a kiosk restart.

        The first call (during daemon startup) is suppressed because the
        watchdog already fetched the same value from the backend at its
        own startup; signalling here would just thrash swayidle.
        """
        new_timeout = max(0, seconds)
        changed = new_timeout != self._blank_timeout
        self._blank_timeout = new_timeout
        if changed and self._timeout_initialized:
            self._signal_reload_timeout(new_timeout)
        self._timeout_initialized = True

    def wake(self):
        """Wake screen on activity (NFC tag, scale weight change).

        Writes to /tmp/spoolbuddy-wake FIFO which the idle watchdog
        (spoolbuddy-idle.sh) monitors inside the Wayland session.  The
        watchdog calls wlopm --on on our behalf.  No-op if the FIFO
        doesn't exist (kiosk not running or blanking disabled without FIFO).
        """
        self._last_activity = time.monotonic()
        self._blanked = False
        self._signal_wake()

    def tick(self):
        """Called periodically from heartbeat loop. Tracks idle state internally."""
        if self._blank_timeout <= 0:
            self._blanked = False
            return
        idle = time.monotonic() - self._last_activity
        if not self._blanked and idle >= self._blank_timeout:
            self._blanked = True
            logger.debug("Screen idle timeout reached (swayidle manages blanking)")

    def _signal_wake(self) -> None:
        """Write to the wake FIFO to request display power-on."""
        if self._write_fifo(b"wake\n"):
            logger.info("Wake signal sent via FIFO")

    def _signal_reload_timeout(self, seconds: int) -> None:
        """Tell the idle watchdog to apply a new timeout to swayidle."""
        if self._write_fifo(f"reload-timeout {seconds}\n".encode()):
            logger.info("Reload-timeout signal sent (timeout=%ds)", seconds)

    def _write_fifo(self, payload: bytes) -> bool:
        """Best-effort write to the wake FIFO. Returns True on success."""
        if not WAKE_FIFO.exists():
            return False
        try:
            # Verify it's actually a FIFO, not a regular file
            if not stat.S_ISFIFO(WAKE_FIFO.stat().st_mode):
                return False
            # Open non-blocking so we don't hang if no reader is attached
            fd = os.open(str(WAKE_FIFO), os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, payload)
                return True
            finally:
                os.close(fd)
        except OSError as e:
            # ENXIO = no reader on the FIFO (idle script not running) — expected
            if e.errno != 6:  # ENXIO
                logger.debug("FIFO write failed: %s", e)
            return False
