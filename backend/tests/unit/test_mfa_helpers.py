"""Unit tests for 2FA helper functions in mfa.py."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestPreAuthTokens:
    """Tests for the in-memory pre-auth token store."""

    def setup_method(self):
        """Clear token store before each test."""
        from backend.app.api.routes.mfa import _pre_auth_tokens

        _pre_auth_tokens.clear()

    def test_create_and_consume_token(self):
        from backend.app.api.routes.mfa import consume_pre_auth_token, create_pre_auth_token

        token = create_pre_auth_token("alice")
        username = consume_pre_auth_token(token)
        assert username == "alice"

    def test_token_is_single_use(self):
        from backend.app.api.routes.mfa import consume_pre_auth_token, create_pre_auth_token

        token = create_pre_auth_token("alice")
        consume_pre_auth_token(token)
        # Second use must return None
        assert consume_pre_auth_token(token) is None

    def test_invalid_token_returns_none(self):
        from backend.app.api.routes.mfa import consume_pre_auth_token

        assert consume_pre_auth_token("notavalidtoken") is None

    def test_expired_token_returns_none(self):
        from backend.app.api.routes.mfa import _pre_auth_tokens, consume_pre_auth_token, create_pre_auth_token

        token = create_pre_auth_token("alice")
        # Manually expire the token
        username, _ = _pre_auth_tokens[token]
        _pre_auth_tokens[token] = (username, datetime.now(timezone.utc) - timedelta(seconds=1))
        assert consume_pre_auth_token(token) is None

    def test_different_users_get_different_tokens(self):
        from backend.app.api.routes.mfa import create_pre_auth_token

        t1 = create_pre_auth_token("alice")
        t2 = create_pre_auth_token("bob")
        assert t1 != t2

    def test_token_is_url_safe_string(self):
        from backend.app.api.routes.mfa import create_pre_auth_token

        token = create_pre_auth_token("alice")
        assert isinstance(token, str)
        assert len(token) >= 32


class TestRateLimiting:
    """Tests for 2FA rate-limiting logic."""

    def setup_method(self):
        from backend.app.api.routes.mfa import _failed_2fa_attempts

        _failed_2fa_attempts.clear()

    def test_no_lockout_under_limit(self):
        from backend.app.api.routes.mfa import check_rate_limit, record_failed_attempt

        for _ in range(4):
            record_failed_attempt("alice")
        # Should not raise
        check_rate_limit("alice")

    def test_lockout_at_limit(self):
        from fastapi import HTTPException

        from backend.app.api.routes.mfa import check_rate_limit, record_failed_attempt

        for _ in range(5):
            record_failed_attempt("alice")
        with pytest.raises(HTTPException) as exc_info:
            check_rate_limit("alice")
        assert exc_info.value.status_code == 429

    def test_clear_resets_lockout(self):
        from backend.app.api.routes.mfa import (
            check_rate_limit,
            clear_failed_attempts,
            record_failed_attempt,
        )

        for _ in range(5):
            record_failed_attempt("alice")
        clear_failed_attempts("alice")
        # Should not raise after clearing
        check_rate_limit("alice")

    def test_old_attempts_expire(self):
        """Attempts older than LOCKOUT_WINDOW are ignored."""
        from backend.app.api.routes.mfa import (
            _failed_2fa_attempts,
            check_rate_limit,
        )

        # Inject 5 attempts that are already expired (20 min ago)
        old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
        _failed_2fa_attempts["alice"] = [old_time] * 5
        # Should not raise — all attempts are outside the window
        check_rate_limit("alice")

    def test_lockout_is_per_user(self):
        from backend.app.api.routes.mfa import check_rate_limit, record_failed_attempt

        for _ in range(5):
            record_failed_attempt("alice")
        # Bob is unaffected
        check_rate_limit("bob")


class TestBackupCodeGeneration:
    """Tests for backup code helpers."""

    def test_generates_ten_codes(self):
        from backend.app.api.routes.mfa import _generate_backup_codes

        plain, hashed = _generate_backup_codes()
        assert len(plain) == 10
        assert len(hashed) == 10

    def test_codes_are_eight_chars(self):
        from backend.app.api.routes.mfa import _generate_backup_codes

        plain, _ = _generate_backup_codes()
        for code in plain:
            assert len(code) == 8

    def test_codes_are_alphanumeric(self):
        import string

        from backend.app.api.routes.mfa import _generate_backup_codes

        allowed = set(string.ascii_uppercase + string.digits)
        plain, _ = _generate_backup_codes()
        for code in plain:
            assert all(c in allowed for c in code)

    def test_hashes_verify_against_plain(self):
        from passlib.context import CryptContext

        from backend.app.api.routes.mfa import _generate_backup_codes

        ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
        plain, hashed = _generate_backup_codes()
        for p, h in zip(plain, hashed, strict=True):
            assert ctx.verify(p, h)

    def test_codes_are_unique(self):
        from backend.app.api.routes.mfa import _generate_backup_codes

        plain, _ = _generate_backup_codes()
        assert len(set(plain)) == 10


class TestTOTPQRCode:
    """Tests for QR code generation helper."""

    def test_generates_base64_png(self):
        import base64

        from backend.app.api.routes.mfa import _generate_totp_qr_b64

        uri = "otpauth://totp/Bambuddy:testuser?secret=BASE32SECRET&issuer=Bambuddy"
        result = _generate_totp_qr_b64(uri)
        # Should be valid base64
        decoded = base64.b64decode(result)
        # PNG magic bytes
        assert decoded[:4] == b"\x89PNG"
