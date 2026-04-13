"""Ephemeral authentication tokens and rate-limit events.

These tables replace the module-level in-memory dicts in mfa.py, making
the 2FA / OIDC flow compatible with multi-worker deployments and persistent
across server restarts.

Tables
------
AuthEphemeralToken
    Short-lived, single-use tokens for:
    - pre_auth   : issued after password check, consumed when 2FA is verified
    - oidc_state : CSRF nonce for the OIDC authorization-code flow
    - oidc_exchange : short bridge token from the OIDC callback to the SPA

AuthRateLimitEvent
    Timestamped events used for sliding-window rate limiting:
    - 2fa_attempt  : each failed 2FA verification attempt
    - email_send   : each OTP email sent (prevents email flooding)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class TokenType(str, Enum):
    """T3: Enumerated token types for AuthEphemeralToken.token_type.

    Using str-based Enum keeps the stored values human-readable and
    backward-compatible with existing rows.
    """

    PRE_AUTH = "pre_auth"
    OIDC_STATE = "oidc_state"
    OIDC_EXCHANGE = "oidc_exchange"
    PASSWORD_RESET = "password_reset"
    EMAIL_OTP_SETUP = "email_otp_setup"


class EventType(str, Enum):
    """T3: Enumerated event types for AuthRateLimitEvent.event_type.

    Using str-based Enum keeps the stored values human-readable and
    backward-compatible with existing rows.
    """

    TWO_FA_ATTEMPT = "2fa_attempt"
    EMAIL_SEND = "email_send"
    LOGIN_ATTEMPT = "login_attempt"
    LOGIN_IP = "login_ip"


class AuthEphemeralToken(Base):
    """Single-use, time-limited token for pre-auth / OIDC flows."""

    __tablename__ = "auth_ephemeral_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    token_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'pre_auth' | 'oidc_state' | 'oidc_exchange'

    # pre_auth + oidc_exchange: which user this session belongs to
    username: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # oidc_state: which provider initiated the flow
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # oidc_state: replay-protection nonce embedded in the ID token
    nonce: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # oidc_state: PKCE code verifier (S256 method)
    code_verifier: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # pre_auth: HttpOnly cookie value bound to this token to prevent token theft
    # (XSS can read JS memory but cannot read HttpOnly cookies).
    challenge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ------------------------------------------------------------------
    # T1: Classmethod factories — enforce required fields per token type
    # and prevent accidentally leaving optional fields at their defaults.
    # ------------------------------------------------------------------

    @classmethod
    def new_pre_auth(
        cls,
        token: str,
        username: str,
        expires_at: datetime,
        challenge_id: str | None = None,
    ) -> AuthEphemeralToken:
        """Create a pre-auth token (issued after password check, before 2FA)."""
        return cls(
            token=token,
            token_type=TokenType.PRE_AUTH,
            username=username,
            expires_at=expires_at,
            challenge_id=challenge_id,
        )

    @classmethod
    def new_oidc_state(
        cls,
        token: str,
        provider_id: int,
        nonce: str,
        code_verifier: str,
        expires_at: datetime,
    ) -> AuthEphemeralToken:
        """Create an OIDC state token (CSRF protection + PKCE for authorize redirect)."""
        return cls(
            token=token,
            token_type=TokenType.OIDC_STATE,
            provider_id=provider_id,
            nonce=nonce,
            code_verifier=code_verifier,
            expires_at=expires_at,
        )

    @classmethod
    def new_oidc_exchange(
        cls,
        token: str,
        username: str,
        expires_at: datetime,
    ) -> AuthEphemeralToken:
        """Create an OIDC exchange token (bridge from callback to SPA)."""
        return cls(
            token=token,
            token_type=TokenType.OIDC_EXCHANGE,
            username=username,
            expires_at=expires_at,
        )

    @classmethod
    def new_password_reset(
        cls,
        token: str,
        username: str,
        expires_at: datetime,
    ) -> AuthEphemeralToken:
        """Create a password-reset token (single-use link emailed to the user)."""
        return cls(
            token=token,
            token_type=TokenType.PASSWORD_RESET,
            username=username,
            expires_at=expires_at,
        )

    @classmethod
    def new_email_otp_setup(
        cls,
        token: str,
        username: str,
        code_hash: str,
        expires_at: datetime,
    ) -> AuthEphemeralToken:
        """Create an email-OTP setup token.

        The ``code_hash`` is stored in the ``nonce`` column (field reuse
        documented inline in the enable_email_otp endpoint).
        """
        return cls(
            token=token,
            token_type=TokenType.EMAIL_OTP_SETUP,
            username=username,
            nonce=code_hash,
            expires_at=expires_at,
        )


class AuthRateLimitEvent(Base):
    """Timestamped events used for sliding-window rate limiting."""

    __tablename__ = "auth_rate_limit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # '2fa_attempt' | 'email_send'
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
