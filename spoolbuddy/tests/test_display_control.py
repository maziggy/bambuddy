"""Tests for daemon.display_control — DisplayControl brightness and blanking."""

import os
import time

import pytest


class TestDisplayControlNoBacklight:
    """DisplayControl behavior when no backlight is present."""

    def test_no_backlight_detected(self, monkeypatch, tmp_path):
        # Point BACKLIGHT_BASE to an empty directory (no backlight entries)
        import daemon.display_control as dc_mod

        empty_dir = tmp_path / "backlight"
        empty_dir.mkdir()
        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", empty_dir)

        dc = dc_mod.DisplayControl()

        assert dc.has_backlight is False

    def test_no_backlight_dir_missing(self, monkeypatch, tmp_path):
        import daemon.display_control as dc_mod

        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", tmp_path / "nonexistent")

        dc = dc_mod.DisplayControl()

        assert dc.has_backlight is False

    def test_set_brightness_noop_without_backlight(self, monkeypatch, tmp_path):
        import daemon.display_control as dc_mod

        empty_dir = tmp_path / "backlight"
        empty_dir.mkdir()
        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", empty_dir)

        dc = dc_mod.DisplayControl()

        # Should not raise
        dc.set_brightness(50)
        dc.set_brightness(0)
        dc.set_brightness(100)


class TestDisplayControlWithBacklight:
    """DisplayControl behavior with a mock sysfs backlight."""

    @pytest.fixture
    def display(self, monkeypatch, tmp_path):
        import daemon.display_control as dc_mod

        bl_dir = tmp_path / "backlight" / "rpi_backlight"
        bl_dir.mkdir(parents=True)
        (bl_dir / "brightness").write_text("200")
        (bl_dir / "max_brightness").write_text("255")

        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", tmp_path / "backlight")

        return dc_mod.DisplayControl(), bl_dir

    def test_has_backlight_true(self, display):
        dc, _ = display
        assert dc.has_backlight is True

    def test_set_brightness_100(self, display):
        dc, bl_dir = display
        dc.set_brightness(100)
        assert (bl_dir / "brightness").read_text() == "255"

    def test_set_brightness_0(self, display):
        dc, bl_dir = display
        dc.set_brightness(0)
        assert (bl_dir / "brightness").read_text() == "0"

    def test_set_brightness_50(self, display):
        dc, bl_dir = display
        dc.set_brightness(50)
        value = int((bl_dir / "brightness").read_text())
        # 50% of 255 = 127 or 128 depending on rounding
        assert value == round(255 * 50 / 100)

    def test_set_brightness_clamped_above_100(self, display):
        dc, bl_dir = display
        dc.set_brightness(200)
        assert (bl_dir / "brightness").read_text() == "255"

    def test_set_brightness_clamped_below_0(self, display):
        dc, bl_dir = display
        dc.set_brightness(-50)
        assert (bl_dir / "brightness").read_text() == "0"

    def test_max_brightness_fallback_on_missing_file(self, monkeypatch, tmp_path):
        """If max_brightness file doesn't exist, defaults to 255."""
        import daemon.display_control as dc_mod

        bl_dir = tmp_path / "backlight" / "rpi_backlight"
        bl_dir.mkdir(parents=True)
        (bl_dir / "brightness").write_text("100")
        # No max_brightness file

        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", tmp_path / "backlight")

        dc = dc_mod.DisplayControl()
        assert dc._max_brightness == 255


class TestDisplayControlBlanking:
    """Blanking logic: timeout, wake, tick."""

    @pytest.fixture
    def display(self, monkeypatch, tmp_path):
        import daemon.display_control as dc_mod

        empty_dir = tmp_path / "backlight"
        empty_dir.mkdir()
        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", empty_dir)

        return dc_mod.DisplayControl()

    def test_blank_timeout_default_disabled(self, display):
        assert display._blank_timeout == 0

    def test_set_blank_timeout(self, display):
        display.set_blank_timeout(30)
        assert display._blank_timeout == 30

    def test_set_blank_timeout_negative_clamped(self, display):
        display.set_blank_timeout(-10)
        assert display._blank_timeout == 0

    def test_tick_does_not_blank_when_disabled(self, display):
        display.set_blank_timeout(0)
        display.tick()
        assert display._blanked is False

    def test_tick_blanks_after_timeout(self, display, monkeypatch):
        display.set_blank_timeout(5)
        # Simulate idle for 10 seconds by backdating last_activity
        display._last_activity = time.monotonic() - 10
        display.tick()
        assert display._blanked is True

    def test_tick_does_not_blank_before_timeout(self, display):
        display.set_blank_timeout(60)
        display.wake()  # Reset activity
        display.tick()
        assert display._blanked is False

    def test_wake_unblanks(self, display):
        display.set_blank_timeout(5)
        display._last_activity = time.monotonic() - 10
        display.tick()
        assert display._blanked is True

        display.wake()
        assert display._blanked is False

    def test_tick_unblanks_when_timeout_disabled_while_blanked(self, display):
        """If timeout is disabled while screen is blanked, tick should unblank."""
        display.set_blank_timeout(5)
        display._last_activity = time.monotonic() - 10
        display.tick()
        assert display._blanked is True

        display.set_blank_timeout(0)
        display.tick()
        assert display._blanked is False

    def test_wake_resets_activity_timer(self, display):
        display.set_blank_timeout(5)
        old_time = display._last_activity
        time.sleep(0.01)
        display.wake()
        assert display._last_activity > old_time


class TestDisplayControlFifoMessages:
    """The wake FIFO carries two messages: `wake` and `reload-timeout N`.

    These tests pin both — they're the only way the daemon can talk to
    the idle watchdog (spoolbuddy-idle.sh) running in the Wayland session.
    Regression target: a one-shot swayidle started with a stale timeout
    value would never pick up UI changes without these signals.
    """

    @pytest.fixture
    def display_with_fifo(self, monkeypatch, tmp_path):
        import daemon.display_control as dc_mod

        empty_dir = tmp_path / "backlight"
        empty_dir.mkdir()
        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", empty_dir)

        fifo_path = tmp_path / "spoolbuddy-wake"
        os.mkfifo(str(fifo_path), 0o622)
        monkeypatch.setattr(dc_mod, "WAKE_FIFO", fifo_path)

        # Hold a non-blocking reader open so the daemon's writes don't hit ENXIO.
        reader_fd = os.open(str(fifo_path), os.O_RDONLY | os.O_NONBLOCK)
        try:
            yield dc_mod.DisplayControl(), reader_fd
        finally:
            os.close(reader_fd)

    @staticmethod
    def _drain(fd: int) -> bytes:
        """Read whatever is queued on the FIFO without blocking."""
        try:
            return os.read(fd, 4096)
        except BlockingIOError:
            return b""

    def test_wake_writes_wake_line(self, display_with_fifo):
        dc, reader_fd = display_with_fifo
        dc.wake()
        assert self._drain(reader_fd) == b"wake\n"

    def test_first_set_blank_timeout_does_not_signal(self, display_with_fifo):
        """The watchdog already fetched this value at its own startup —
        signalling here would just thrash swayidle for nothing."""
        dc, reader_fd = display_with_fifo
        dc.set_blank_timeout(300)
        assert self._drain(reader_fd) == b""
        assert dc._blank_timeout == 300

    def test_subsequent_change_signals_reload(self, display_with_fifo):
        dc, reader_fd = display_with_fifo
        dc.set_blank_timeout(300)  # init — no signal
        dc.set_blank_timeout(60)
        assert self._drain(reader_fd) == b"reload-timeout 60\n"

    def test_same_value_does_not_signal(self, display_with_fifo):
        dc, reader_fd = display_with_fifo
        dc.set_blank_timeout(300)
        dc.set_blank_timeout(300)
        assert self._drain(reader_fd) == b""

    def test_disable_after_enable_signals_zero(self, display_with_fifo):
        """Going from "blanking on" to "blanking off" must reach the watchdog
        so it can stop swayidle — otherwise the screen keeps blanking even
        after the user picks 'Off'."""
        dc, reader_fd = display_with_fifo
        dc.set_blank_timeout(300)  # init
        dc.set_blank_timeout(0)
        assert self._drain(reader_fd) == b"reload-timeout 0\n"

    def test_negative_clamped_to_zero_in_signal(self, display_with_fifo):
        dc, reader_fd = display_with_fifo
        dc.set_blank_timeout(300)  # init
        dc.set_blank_timeout(-5)
        assert self._drain(reader_fd) == b"reload-timeout 0\n"

    def test_signal_no_op_when_fifo_missing(self, monkeypatch, tmp_path):
        """No watchdog running = no FIFO. Writes must not raise."""
        import daemon.display_control as dc_mod

        empty_dir = tmp_path / "backlight"
        empty_dir.mkdir()
        monkeypatch.setattr(dc_mod, "BACKLIGHT_BASE", empty_dir)
        monkeypatch.setattr(dc_mod, "WAKE_FIFO", tmp_path / "no-such-fifo")

        dc = dc_mod.DisplayControl()
        dc.set_blank_timeout(300)
        dc.set_blank_timeout(60)  # would signal if FIFO existed
        dc.wake()
        # No assertion needed — surviving without raising is the contract.
