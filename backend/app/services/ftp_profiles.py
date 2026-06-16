"""Per-printer-model FTP tuning knobs.

Mirrors the shape of :mod:`backend.app.services.camera_profiles` — a
small registry of per-model overrides so quirky firmwares can be
tuned without sprinkling ``if model == "X":`` branches through
``bambu_ftp.py``. Adding a new model's quirk is a config edit (an
entry in ``_PROFILES`` plus the alias for its internal SSDP code if
needed), not another hard-coded branch.

The default profile matches the historical pre-fix behaviour, so
every model that doesn't have an entry here keeps its existing FTP
behaviour byte-for-byte.

Currently only the TLS-version cap lives here (P2S firmware
01.02.00.00 needs it — see ``cap_tls_v1_2`` below). The A1
data-channel-plaintext quirk still lives in :class:`BambuFTPClient`
via ``A1_MODELS`` / ``skip_session_reuse``; folding that into a
profile field is a future cleanup, not load-bearing for this fix.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FTPProfile:
    """Tuning knobs for one printer model's FTP path.

    All defaults reflect the historical behaviour. Models with quirky
    firmware override individual fields rather than re-defining the
    whole profile.
    """

    # Pin the SSL context's ``maximum_version`` to TLS 1.2.
    #
    # Python 3.13's default ``ssl.create_default_context()`` negotiates
    # TLS 1.3 when both peers support it. The Bambuddy Docker image is
    # ``python:3.13-slim-trixie``, so every Docker user gets 1.3 by
    # default. Some Bambu printer firmwares (P2S 01.02.00.00 confirmed
    # by @iitazz, #1401) implement session reuse on the FTPS data
    # channel against an old vsFTPd build that doesn't tolerate TLS
    # 1.3's asynchronous session-ticket model: the data channel gets
    # torn down mid-stream and the upload aborts with 426 "Failure
    # reading network stream" — visible as a clean truncation at a
    # chunk boundary (one reporter saw exactly 7 × 64 KB landed on
    # the printer). Capping to TLS 1.2 makes session resumption
    # synchronous and the upload completes normally.
    #
    # **Defaults to False** — only applied to printer models where a
    # reporter has confirmed the symptom. Existing P1S / X1C / H2D
    # installs that work fine today stay on the negotiated TLS 1.3.
    # This is deliberately conservative; flipping a printer to the
    # capped path is a config edit when a new model surfaces the
    # same bug.
    cap_tls_v1_2: bool = False


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

# Default profile = historical behaviour. Used for every model that
# doesn't have an entry in ``_PROFILES``.
DEFAULT_PROFILE = FTPProfile()

# Per-model overrides. Keys are uppercase display names (e.g. "P2S")
# AFTER alias normalisation, so internal SSDP codes ("N7") resolve via
# ``_MODEL_ALIASES`` below.
_PROFILES: dict[str, FTPProfile] = {
    # P2S firmware 01.02.00.00 trips the vsFTPd + TLS 1.3 session-reuse
    # bug on the FTPS data channel (#1401, reporter @iitazz). Cap to
    # TLS 1.2 so session resumption is synchronous and the upload
    # completes.
    "P2S": FTPProfile(
        cap_tls_v1_2=True,
    ),
    # X2D firmware 01.01.00.00 fails the implicit-FTPS handshake on
    # port 990 with ``[SSL: WRONG_VERSION_NUMBER]`` against Python
    # 3.13's default TLS-1.3 ClientHello (#1638, reporter @vasmarfas).
    # Without the 3MF download the print falls through to the no-3MF
    # fallback archive path and the card lands almost empty (no
    # filament total, no layers, no MakerWorld link). Cap to TLS 1.2
    # by analogy with P2S; if the symptom turns out to be a different
    # FTPS variant on the X2D (explicit AUTH TLS, different port) the
    # entry stays useful as a per-model tuning slot for the follow-up.
    "X2D": FTPProfile(
        cap_tls_v1_2=True,
    ),
}

# SSDP internal codes that should resolve to a display-name profile.
# Mirrors the same map in :mod:`camera_profiles`.
_MODEL_ALIASES: dict[str, str] = {
    "N7": "P2S",  # P2S internal SSDP code
    "N6": "X2D",  # X2D internal SSDP code
}


def get_ftp_profile(model: str | None) -> FTPProfile:
    """Return the :class:`FTPProfile` for *model*, or the default.

    ``model`` can be either a display name (e.g. ``"P2S"``) or an
    internal SSDP code (e.g. ``"N7"``). Unknown / missing models fall
    back to :data:`DEFAULT_PROFILE` so the FTP path is never blocked
    on a missing entry.
    """
    if not model:
        return DEFAULT_PROFILE
    key = model.upper().strip()
    key = _MODEL_ALIASES.get(key, key)
    return _PROFILES.get(key, DEFAULT_PROFILE)
