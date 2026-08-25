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
    # ``ssl.create_default_context()`` negotiates TLS 1.3 when both peers
    # support it. Some Bambu printer firmwares (P2S 01.02.00.00 confirmed
    # by @iitazz, #1401) implement session reuse on the FTPS data
    # channel against an old vsFTPd build that doesn't tolerate TLS
    # 1.3's asynchronous session-ticket model: the data channel gets
    # torn down mid-stream and the upload aborts with 426 "Failure
    # reading network stream" — visible as a clean truncation at a
    # chunk boundary (one reporter saw exactly 7 × 64 KB landed on
    # the printer). Capping to TLS 1.2 makes session resumption
    # synchronous and the upload completes normally.
    #
    # This cap only bites on models that *offer* 1.3 in the first place,
    # and on the evidence so far none of them do. Probed directly on
    # :990, an X1C and an H2D refuse TLS 1.0, 1.1 and 1.3 and complete
    # only on 1.2; @grolmus then probed a 9-printer farm (#2780,
    # 2026-08-21) and got the same result on six P2S units, two X1C and
    # an H2D — tls1_3 refused, tls1_2 ok, every one. This comment used
    # to claim "the P2S evidently does offer 1.3"; six say otherwise.
    #
    # A cap is also not needed to reach a 1.2-only peer. Measured
    # against a local TLS-1.2-only server with the same context this
    # module builds: an uncapped client negotiates 1.2 and connects.
    # A client forced to 1.3 gets TLSV1_ALERT_PROTOCOL_VERSION — never
    # WRONG_VERSION_NUMBER, which comes from bytes that are not a TLS
    # record at all. See
    # ``tests/unit/services/test_cleartext_probe_2780.py``, which pins
    # both measurements so this comment stays falsifiable.
    #
    # So the entries below are kept as tuning slots and as a record of
    # what each reporter saw, not because the mechanism is understood.
    # Two of the three explain a symptom this cap cannot affect; see
    # their own comments.
    # (P1S untested; no claim made either way.)
    #
    # **Defaults to False** — only applied to printer models where a
    # reporter has confirmed the symptom. This is deliberately
    # conservative; flipping a printer to the capped path is a config
    # edit when a new model surfaces the same bug.
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
    # P2S firmware 01.02.00.00 (#1401, reporter @iitazz). Symptom is a
    # 426 truncation part-way through a transfer, on the data channel —
    # a different failure from the handshake ones below, and the only
    # one here whose mechanism a TLS-1.3 session-ticket problem could
    # actually explain. The reporter confirmed the fix.
    #
    # Unresolved: @grolmus's six P2S units refuse TLS 1.3 outright
    # (#2780), so on their firmware the negotiated version was already
    # 1.2 and this cap changes nothing. Either the firmware moved
    # between the two reports, or #1401 was fixed by something else in
    # the same change. Kept because a reporter confirmed it and no one
    # has hardware to re-test it on.
    "P2S": FTPProfile(
        cap_tls_v1_2=True,
    ),
    # X2D firmware 01.01.00.00 fails the implicit-FTPS handshake on
    # port 990 with ``[SSL: WRONG_VERSION_NUMBER]`` (#1638, reporter
    # @vasmarfas). Without the 3MF download the print falls through to
    # the no-3MF fallback archive path and the card lands almost empty
    # (no filament total, no layers, no MakerWorld link).
    #
    # RE-TEST WANTED. This was capped on the reading that the error came
    # from "Python 3.13's default TLS-1.3 ClientHello". That reading is
    # now measured wrong: WRONG_VERSION_NUMBER is what a *non-TLS*
    # answer produces, a version mismatch reports itself differently,
    # and an uncapped client reaches a 1.2-only peer unaided (#2780).
    # So this cap cannot be what changed the outcome, and the X2D is
    # most likely answering :990 with something that is not TLS — the
    # cleartext probe in ``bambu_ftp`` will now say what. Left in place
    # rather than removed: nobody here has an X2D, and the entry costs
    # nothing on a printer that does not offer 1.3 anyway.
    "X2D": FTPProfile(
        cap_tls_v1_2=True,
    ),
    # H2C firmware 01.02.00.00 (#2582, reporter @gyrene2083). The sliced
    # 3MF intermittently fails to come off the printer over FTPS, so the
    # print drops to the no-3MF fallback archive with no slice data —
    # which is why the Print Log shows no filament and nothing is
    # deducted.
    #
    # RE-TEST WANTED, same reasoning as the X2D above. Capped "by
    # analogy with P2S" on the belief that the profile-less path "ran on
    # the Python-default TLS 1.3"; measurement says a 1.2-only peer
    # negotiates 1.2 without a cap, so there was no 1.3 to fall back
    # from (#2780). "Intermittent" now points somewhere better: it is
    # the signature of the transient non-TLS refusal @grolmus sees on
    # his P2S units, which is the same H2 firmware line.
    "H2C": FTPProfile(
        cap_tls_v1_2=True,
    ),
}

# SSDP internal codes that should resolve to a display-name profile.
# Mirrors the same map in :mod:`camera_profiles`.
_MODEL_ALIASES: dict[str, str] = {
    "N7": "P2S",  # P2S internal SSDP code
    "N6": "X2D",  # X2D internal SSDP code
    "O1C": "H2C",  # H2C internal SSDP code
    "O1C2": "H2C",  # H2C dual-nozzle variant SSDP code
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
