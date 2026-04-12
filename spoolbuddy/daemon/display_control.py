"""Display brightness and screen blanking control for SpoolBuddy kiosk.

Brightness: DSI backlights are controlled via sysfs /sys/class/backlight/*/brightness.
            HDMI brightness is handled by the frontend via CSS filter.
Blanking:   swayidle is the sole authority on screen blanking (idle timeout →
            wlopm --off, touch → wlopm --on).  The daemon only *wakes* the
            display via wlopm --on when NFC/scale activity is detected — it
            never blanks.  wlopm --on is idempotent so both paths coexist
            safely.
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BACKLIGHT_BASE = Path("/sys/class/backlight")


class DisplayControl:
    def __init__(self):
        self._backlight_path = self._find_backlight()
        self._max_brightness = self._read_max_brightness()
        self._blank_timeout = 0  # seconds, 0 = disabled
        self._last_activity = time.monotonic()
        self._blanked = False
        self._wlopm_path = shutil.which("wlopm")
        self._wayland_env: dict[str, str] | None = None
        self._output = os.environ.get("SPOOLBUDDY_DISPLAY_OUTPUT", "HDMI-A-1")

        if self._backlight_path:
            logger.info("Backlight found: %s (max=%d)", self._backlight_path, self._max_brightness)
        else:
            logger.info("No DSI backlight found, brightness control via frontend CSS")

        if self._wlopm_path:
            logger.info("wlopm found at %s, HDMI wake/blank enabled", self._wlopm_path)
        else:
            logger.info("wlopm not found, HDMI wake/blank disabled")

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
        """Set screen blank timeout in seconds. 0 = disabled."""
        self._blank_timeout = max(0, seconds)

    def wake(self):
        """Wake screen on activity (NFC tag, scale weight change).

        Always calls wlopm --on regardless of the daemon's internal blanked
        state, because swayidle may have blanked the screen independently and
        the daemon has no way to know.  wlopm --on is idempotent so calling it
        while the screen is already on is harmless.
        """
        self._last_activity = time.monotonic()
        self._blanked = False
        self._wlopm(on=True)

    def tick(self):
        """Called periodically from heartbeat loop. Tracks idle state internally."""
        if self._blank_timeout <= 0:
            self._blanked = False
            return
        idle = time.monotonic() - self._last_activity
        if not self._blanked and idle >= self._blank_timeout:
            self._blanked = True
            logger.debug("Screen idle timeout reached (swayidle manages blanking)")

    def _discover_wayland_env(self) -> dict[str, str] | None:
        """Discover WAYLAND_DISPLAY and XDG_RUNTIME_DIR for the kiosk session.

        The daemon runs as a systemd service outside the Wayland session, so
        these variables aren't inherited.  We probe the same runtime dir that
        labwc uses (the daemon and kiosk run as the same user).
        """
        xdg = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        runtime = Path(xdg)
        if not runtime.is_dir():
            return None
        for entry in sorted(runtime.iterdir()):
            if entry.name.startswith("wayland-") and not entry.name.endswith(".lock"):
                return {"WAYLAND_DISPLAY": entry.name, "XDG_RUNTIME_DIR": xdg}
        return None

    def _wlopm(self, on: bool) -> None:
        """Toggle HDMI output via wlopm.  No-op if wlopm is unavailable."""
        if not self._wlopm_path:
            return
        # Retry discovery each call until the Wayland socket appears — labwc
        # may start after the daemon on boot.
        if self._wayland_env is None:
            self._wayland_env = self._discover_wayland_env()
            if self._wayland_env is None:
                logger.debug("No Wayland socket found, cannot control HDMI")
                return
            logger.info("Wayland session discovered: %s", self._wayland_env.get("WAYLAND_DISPLAY"))
        flag = "--on" if on else "--off"
        try:
            env = {**os.environ, **self._wayland_env}
            subprocess.run(
                [self._wlopm_path, flag, self._output],
                env=env,
                timeout=5,
                capture_output=True,
            )
        except Exception as e:
            logger.debug("wlopm %s %s failed: %s", flag, self._output, e)
