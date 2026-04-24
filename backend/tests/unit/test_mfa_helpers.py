"""Unit tests for 2FA helper functions in mfa.py."""

import base64
import string

import pytest
from passlib.context import CryptContext

from backend.app.api.routes.mfa import _generate_backup_codes, _generate_totp_qr_b64, _is_valid_email_shaped


class TestBackupCodeGeneration:
    """Tests for backup code helpers."""

    def test_generates_ten_codes(self):
        plain, hashed = _generate_backup_codes()
        assert len(plain) == 10
        assert len(hashed) == 10

    def test_codes_are_eight_chars(self):
        plain, _ = _generate_backup_codes()
        for code in plain:
            assert len(code) == 8

    def test_codes_are_alphanumeric(self):
        allowed = set(string.ascii_uppercase + string.digits)
        plain, _ = _generate_backup_codes()
        for code in plain:
            assert all(c in allowed for c in code)

    def test_hashes_verify_against_plain(self):
        ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
        plain, hashed = _generate_backup_codes()
        for p, h in zip(plain, hashed, strict=True):
            assert ctx.verify(p, h)

    def test_codes_are_unique(self):
        plain, _ = _generate_backup_codes()
        assert len(set(plain)) == 10


class TestTOTPQRCode:
    """Tests for QR code generation helper."""

    def test_generates_base64_png(self):
        uri = "otpauth://totp/Bambuddy:testuser?secret=BASE32SECRET&issuer=Bambuddy"
        result = _generate_totp_qr_b64(uri)
        decoded = base64.b64decode(result)
        assert decoded[:4] == b"\x89PNG"


class TestIsValidEmailShaped:
    """Direct tests for the SEC-2 email-shape check used when resolving custom
    OIDC claims like ``preferred_username`` / ``upn``. Without a dot in the
    domain or with embedded whitespace the claim is not email-shaped and
    Bambuddy must drop it — otherwise notifications / password resets would
    ship to garbage addresses."""

    @pytest.mark.parametrize(
        "value",
        [
            "alice@example.com",
            "a@b.c",
            "first.last@sub.example.co.uk",
            "user+tag@example.com",
        ],
    )
    def test_accepts_well_formed_addresses(self, value):
        assert _is_valid_email_shaped(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "a@b",  # missing dot in domain
            "@example.com",  # missing local part
            "alice@",  # missing domain
            ".@.",  # dots alone are not a domain
            "nodomain",  # missing @ entirely
            "a b@example.com",  # whitespace in local
            "a@exa mple.com",  # whitespace in domain
            "a@b.c\n",  # trailing newline — fullmatch guard
            "a\n@b.c",  # embedded newline
        ],
    )
    def test_rejects_malformed_values(self, value):
        assert _is_valid_email_shaped(value) is False

    def test_rejects_pathologically_long_value(self):
        # 300-char well-shaped string — above the 255 cap.
        long_local = "a" * 260
        assert _is_valid_email_shaped(f"{long_local}@example.com") is False
