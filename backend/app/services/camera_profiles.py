"""Per-printer-model camera tuning knobs.

Bambuddy talks to multiple Bambu Lab printer models that all expose a
camera but in subtly different ways:

- **Chamber image** (port 6000, proprietary binary protocol) — A1, A1
  Mini, P1P, P1S. Frame pacing and TLS quirks are firmware-driven and
  don't go through ffmpeg.
- **RTSPS** (port 322) — X1 series, X2D, H2 series, P2S. Wrapped by a
  local TLS proxy + ffmpeg to MJPEG.

The RTSPS path used to live with hard-coded module constants in
``camera.py``: a single ``-probesize 32 -analyzeduration 0`` tuned for
X1/H2 fast startup. That breaks the P2S on firmware 01.02.00.00, whose
RTSP keyframe pacing is slow enough that ffmpeg can't lock onto the
stream within 32 bytes and gives up with "not enough frames to estimate
rate" (#1395 follow-up — Tschipel's reproduction).

This module replaces those module constants with per-model
:class:`CameraProfile` entries. Defaults match the historical pre-fix
behaviour, so existing models (X1, H2, X2D, X1E) keep their fast-
startup tuning unchanged. Quirky models override the relevant fields
only — the P2S entry below is the first example.

Adding a new model's quirk is a config edit (an entry in ``_PROFILES``
plus the alias for its internal SSDP code if needed), not another
hard-coded global constant.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CameraProfile:
    """Tuning knobs for one printer model's camera path.

    All defaults reflect the historical X1/H2 behaviour (fast startup,
    minimal probing). Models with quirky firmware override individual
    fields rather than re-defining the whole profile.
    """

    # --- RTSPS / ffmpeg path -------------------------------------------------
    # ffmpeg's `-probesize` (bytes). Smaller = lower startup latency but
    # less margin to lock onto a stream whose first keyframe is delayed
    # or whose container metadata is incomplete. P2S 01.02.00.00 needs a
    # full MB to lock; X1/H2 lock within ~32 bytes.
    probesize: int = 32
    # ffmpeg's `-analyzeduration` (microseconds). 0 = skip format
    # analysis entirely. Same trade-off as probesize.
    analyzeduration: int = 0
    # Max consecutive ffmpeg respawns when the printer drops the RTSP
    # session mid-stream. Some firmwares cut the stream after a few
    # seconds (originally noted on P2S), so we transparently respawn
    # to keep the MJPEG client alive.
    rtsp_reconnect_max: int = 30
    # Seconds between ffmpeg respawn attempts.
    rtsp_reconnect_delay: float = 0.2

    # --- Extra ffmpeg input args ---------------------------------------------
    # Hook for future per-model knobs (e.g. `-fflags` overrides) without
    # changing the dataclass shape. Tuple, not list, so the dataclass
    # stays hashable / frozen-friendly.
    extra_ffmpeg_input_args: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

# Default profile = historical X1/H2 fast-startup behaviour. Used for
# every RTSP-capable model that doesn't have an entry in ``_PROFILES``.
DEFAULT_PROFILE = CameraProfile()

# Per-model overrides. Keys are uppercase display names (e.g. "P2S")
# AFTER alias normalisation, so internal SSDP codes ("N7") resolve via
# ``_MODEL_ALIASES`` below.
_PROFILES: dict[str, CameraProfile] = {
    # P2S firmware 01.02.00.00 RTSP keyframe pacing is slow enough that
    # ffmpeg's "32-byte probe + zero analyze" combo can't estimate the
    # frame rate. ffmpeg's own stderr literally says "consider increasing
    # probesize" (#1395 follow-up).
    "P2S": CameraProfile(
        probesize=1_000_000,
        analyzeduration=500_000,
    ),
}

# SSDP internal codes that should resolve to a display-name profile.
# Display-name lookup is the canonical path; this just lets the camera
# code pass through whatever ``Printer.model`` carries without each
# call site needing to know the code→name map.
_MODEL_ALIASES: dict[str, str] = {
    "N7": "P2S",  # P2S internal SSDP code
}


def get_camera_profile(model: str | None) -> CameraProfile:
    """Return the :class:`CameraProfile` for *model*, or the default.

    ``model`` can be either a display name (e.g. ``"P2S"``) or an
    internal SSDP code (e.g. ``"N7"``). Unknown models fall back to
    :data:`DEFAULT_PROFILE` so the camera path is never blocked on a
    missing entry.
    """
    if not model:
        return DEFAULT_PROFILE
    key = model.upper().strip()
    key = _MODEL_ALIASES.get(key, key)
    return _PROFILES.get(key, DEFAULT_PROFILE)
