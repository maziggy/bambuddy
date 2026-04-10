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

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


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

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
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
