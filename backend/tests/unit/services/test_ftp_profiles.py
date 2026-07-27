"""Per-model FTP profile registry (#1401).

Mirrors ``test_camera_profiles.py`` in shape — the FTP profile module
follows the same pattern.
"""

import ssl

from backend.app.services.ftp_profiles import (
    DEFAULT_PROFILE,
    FTPProfile,
    get_ftp_profile,
)


def test_default_profile_does_not_cap_tls():
    """Default profile keeps the historical Python-default TLS negotiation
    (typically TLS 1.3 on Python 3.13). Capping would be a silent
    regression for users who work fine today."""
    assert DEFAULT_PROFILE.cap_tls_v1_2 is False


def test_unknown_model_returns_default():
    """Unknown / missing models fall back to DEFAULT_PROFILE so the FTP
    path is never blocked on a missing entry."""
    assert get_ftp_profile(None) is DEFAULT_PROFILE
    assert get_ftp_profile("") is DEFAULT_PROFILE
    assert get_ftp_profile("Unknown Future Model") is DEFAULT_PROFILE


def test_p2s_caps_tls_v1_2():
    """P2S firmware 01.02.00.00 trips a vsFTPd + TLS 1.3 session-reuse
    bug on the data channel; the profile must cap to TLS 1.2 so session
    resumption is synchronous (#1401, reporter @iitazz)."""
    profile = get_ftp_profile("P2S")
    assert profile.cap_tls_v1_2 is True


def test_p2s_internal_ssdp_code_resolves_to_p2s():
    """SSDP internal code N7 → P2S profile. Camera profiles do the same
    thing — keeps callers free of the code↔display-name mapping."""
    profile = get_ftp_profile("N7")
    assert profile.cap_tls_v1_2 is True


def test_x2d_caps_tls_v1_2():
    """X2D firmware 01.01.00.00 fails implicit-FTPS handshake on port
    990 with WRONG_VERSION_NUMBER against Python 3.13's TLS-1.3 default
    (#1638, reporter @vasmarfas). The profile caps to TLS 1.2 by
    analogy with P2S."""
    profile = get_ftp_profile("X2D")
    assert profile.cap_tls_v1_2 is True


def test_x2d_internal_ssdp_code_resolves_to_x2d():
    """SSDP internal code N6 → X2D profile."""
    profile = get_ftp_profile("N6")
    assert profile.cap_tls_v1_2 is True


def test_h2c_caps_tls_v1_2():
    """H2C firmware 01.02.00.00 — same H2 generation / firmware line as
    P2S — intermittently fails the FTPS 3MF download and falls through to
    the no-3MF fallback archive, so nothing gets deducted (#2582, reporter
    @gyrene2083). The profile caps to TLS 1.2 by analogy with P2S."""
    profile = get_ftp_profile("H2C")
    assert profile.cap_tls_v1_2 is True


def test_h2c_internal_ssdp_codes_resolve_to_h2c():
    """SSDP internal codes O1C and O1C2 (dual-nozzle variant) → H2C
    profile, so the cap applies however the model string arrives."""
    assert get_ftp_profile("O1C").cap_tls_v1_2 is True
    assert get_ftp_profile("O1C2").cap_tls_v1_2 is True


def test_lookup_is_case_insensitive():
    """Printer.model may carry mixed case; the lookup normalises."""
    assert get_ftp_profile("p2s").cap_tls_v1_2 is True
    assert get_ftp_profile("P2s").cap_tls_v1_2 is True
    assert get_ftp_profile("x2d").cap_tls_v1_2 is True


def test_non_capped_models_still_default():
    """Spot-check: the models the user dogfoods today (X1C, H2D) stay on
    the default profile. Adding the P2S override must not accidentally
    flip these."""
    assert get_ftp_profile("X1C").cap_tls_v1_2 is False
    assert get_ftp_profile("H2D").cap_tls_v1_2 is False
    assert get_ftp_profile("P1S").cap_tls_v1_2 is False
    assert get_ftp_profile("A1").cap_tls_v1_2 is False


def test_profile_is_frozen():
    """FTPProfile is a frozen dataclass — runtime mutation should raise.
    Same guarantee CameraProfile has."""
    try:
        DEFAULT_PROFILE.cap_tls_v1_2 = True  # type: ignore[misc]
    except Exception as e:
        assert "frozen" in str(e).lower() or "FrozenInstanceError" in type(e).__name__
        return
    raise AssertionError("FTPProfile should be frozen but assignment succeeded")


def test_cap_tls_v1_2_actually_applied_to_ssl_context():
    """Pins the integration: when ``cap_tls_v1_2=True`` is passed to the
    FTPS subclass, the SSL context's ``maximum_version`` is set to
    TLSv1.2. Guards against a future refactor that drops the wiring
    between profile and context (the registry would still look
    correct, but the cap would silently stop applying)."""
    from backend.app.services.bambu_ftp import ImplicitFTP_TLS

    capped = ImplicitFTP_TLS(cap_tls_v1_2=True)
    assert capped.ssl_context.maximum_version == ssl.TLSVersion.TLSv1_2

    uncapped = ImplicitFTP_TLS(cap_tls_v1_2=False)
    # MAXIMUM_SUPPORTED is the "no cap applied" sentinel for SSLContext.
    assert uncapped.ssl_context.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED


def test_ftp_profile_dataclass_default_constructible():
    """Sanity: FTPProfile() with no args yields the default profile
    (every field has a default)."""
    fresh = FTPProfile()
    assert fresh == DEFAULT_PROFILE
