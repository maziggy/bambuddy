"""Tests for daemon.display_control — DisplayControl brightness and blanking."""

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
