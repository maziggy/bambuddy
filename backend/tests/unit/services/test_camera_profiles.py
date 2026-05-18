"""Unit tests for camera profile registry.

The registry decouples per-model camera tuning (probesize, analyzeduration,
reconnect cadence) from the hard-coded constants that lived in
``camera.py`` until #1395 follow-up. Adding a new model's quirk should
be a config edit, not a code change.
"""

from dataclasses import FrozenInstanceError

import pytest

from backend.app.services.camera_profiles import (
    DEFAULT_PROFILE,
    CameraProfile,
    get_camera_profile,
)


class TestGetCameraProfile:
    def test_unknown_model_returns_default(self):
        """Models with no override fall through to DEFAULT_PROFILE so the
        camera path is never blocked on a missing entry."""
        assert get_camera_profile("UNKNOWN_MODEL") is DEFAULT_PROFILE
        assert get_camera_profile("Future_Bambu_Model_X42") is DEFAULT_PROFILE

    def test_none_model_returns_default(self):
        """`None` / empty model (very early in connect handshake) must not
        crash; the default profile is safe for any RTSP-capable printer."""
        assert get_camera_profile(None) is DEFAULT_PROFILE
        assert get_camera_profile("") is DEFAULT_PROFILE

    def test_default_profile_preserves_historical_fast_startup(self):
        """X1/H2 fast-startup tuning is the historical baseline. The first
        refactor must not regress it for the printers that already worked.
        """
        assert DEFAULT_PROFILE.probesize == 32
        assert DEFAULT_PROFILE.analyzeduration == 0
        assert DEFAULT_PROFILE.rtsp_reconnect_max == 30
        assert DEFAULT_PROFILE.rtsp_reconnect_delay == 0.2

    def test_p2s_has_relaxed_probe(self):
        """P2S firmware 01.02.00.00 needs more probe room — ffmpeg's own
        diagnostic says so. This is the first per-model override and the
        regression to guard."""
        profile = get_camera_profile("P2S")
        assert profile is not DEFAULT_PROFILE
        # Order of magnitude up from the default — enough to lock onto a
        # slow-keyframe stream without adding multi-second startup.
        assert profile.probesize >= 1_000_000
        assert profile.analyzeduration >= 500_000

    def test_p2s_internal_code_resolves_to_p2s_profile(self):
        """SSDP internal codes (e.g. `N7` for P2S) must resolve to the
        same profile as their display name. Otherwise printers freshly
        connected (before display-name lookup completes) would use the
        default profile and hit the same #1395 bug."""
        assert get_camera_profile("N7") is get_camera_profile("P2S")

    def test_lookup_is_case_insensitive(self):
        """Display-name capitalisation should not matter — callers may
        carry lowercase or mixed-case values straight from MQTT."""
        assert get_camera_profile("p2s") is get_camera_profile("P2S")
        assert get_camera_profile("P2s") is get_camera_profile("P2S")

    def test_known_rtsp_models_keep_default_unchanged(self):
        """X1, X1C, X1E, H2D, H2S, X2D — every other RTSP-capable model
        must use the default profile until proven otherwise. Anything
        else means we silently changed behaviour for a model the user
        hasn't reported a problem on."""
        for model in ("X1", "X1C", "X1E", "X2D", "H2C", "H2D", "H2D PRO", "H2S"):
            assert get_camera_profile(model) is DEFAULT_PROFILE, (
                f"{model} unexpectedly has a non-default profile — review "
                "whether the change is intentional before shipping."
            )


class TestCameraProfileShape:
    def test_profile_is_frozen(self):
        """Profiles are immutable; mutating them at runtime would
        introduce action-at-a-distance for the camera generator."""
        with pytest.raises(FrozenInstanceError):
            DEFAULT_PROFILE.probesize = 999  # type: ignore[misc]

    def test_extra_ffmpeg_input_args_defaults_to_empty_tuple(self):
        """Profiles can declare extra `-flag value` pairs to splice into
        the ffmpeg input args without changing the dataclass shape.
        Default is empty so the historical command is unchanged."""
        p = CameraProfile()
        assert p.extra_ffmpeg_input_args == ()
