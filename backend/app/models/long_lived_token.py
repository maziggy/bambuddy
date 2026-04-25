"""Long-lived camera-stream tokens (#1108).

Issue #1108: the existing 60-minute ``camera_stream`` ephemeral tokens are
too short-lived for home-automation integrations (Home Assistant cards,
Frigate, kiosks), which expect a token they can paste once and forget.

Why a separate table from ``AuthEphemeralToken``:

- These are user-owned, named, and revocable from the UI — different
  lifecycle from ephemeral / single-use tokens.
- Hashed at rest (bcrypt). Ephemeral tokens are stored as raw strings
  because their short TTL caps the impact of a DB read; a long-lived
  token must survive a DB dump unscathed.

Why a separate table from ``api_keys``:

- ``api_keys`` is for webhook integrations and has no ``user_id`` FK
  (the keys are global). Long-lived camera tokens are explicitly per-user
  so the UI can show "your tokens" and so a leak can be traced to one user.
- Different permission shape (``api_keys`` carries can_queue / can_control
  flags; long-lived tokens are pure read-only camera streaming).

V1 hard rules:

- ``expires_at`` is required (the issue's ``expire_in: 0 = never`` was
  rejected — irrevocable infinite tokens are a footgun).
- 365-day max — enforced in the create route, not the DB, so a future
  policy change is just a config bump.
- Scope column exists today ("camera_stream" is the only valid value)
  to keep the door open for other long-lived scopes later without a
  schema migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class LongLivedToken(Base):
    """Per-user, hashed-at-rest, revocable token for long-running camera viewers."""

    __tablename__ = "long_lived_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # User-given label — "Home Assistant", "Kitchen kiosk", etc.
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Public lookup prefix — first 8 chars of the secret part. Indexed so
    # verify() can fetch one row instead of scanning + bcrypting all rows.
    # Format: ``bblt_<8-char-prefix>_<32-char-secret>``.
    lookup_prefix: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    # bcrypt hash of the 32-char secret part. Never stored or returned in plaintext.
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # V1: only "camera_stream" is accepted. Column exists so future scopes
    # don't need a schema migration.
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="camera_stream")
    # Required — no infinite tokens. Capped at 365 days at create time.
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Updated on successful verify (rate-limited to once per minute per token
    # to avoid thrashing the DB on every MJPEG keep-alive read).
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set when the user (or an admin) revokes; verify treats revoked == invalid.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<LongLivedToken id={self.id} user_id={self.user_id} name={self.name!r} scope={self.scope}>"
