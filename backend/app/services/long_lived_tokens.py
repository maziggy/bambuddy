"""Service layer for long-lived camera-stream tokens (#1108).

Token format: ``bblt_<8-char-prefix>_<32-char-secret>``.

- The full token is shown to the user **exactly once** at create time.
- ``lookup_prefix`` (the 8-char middle part) is indexed and used to cheaply
  fetch the candidate row — at most one in practice — without scanning the
  whole table on every request.
- ``secret_hash`` is a pbkdf2_sha256 hash of the full token (matching the
  rest of the codebase's password hashing). Even a DB dump can't be replayed
  against the camera endpoint.
- ``last_used_at`` is updated on successful verify, but rate-limited to once
  per minute per token so an MJPEG keep-alive doesn't write to the DB on
  every chunk.
- ``revoked_at`` set → verify returns False; admins or the owning user can
  flip it.

Maximum lifetime is 365 days (issue #1108 explicitly rejected "infinite"
tokens — a leaked permanent token would be irrevocable footgun-by-design).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import get_password_hash, verify_password
from backend.app.models.long_lived_token import LongLivedToken

# Issue #1108 hard cap. Bump here if policy changes — UI default is shorter
# (90 days) and the create route enforces this ceiling.
MAX_TOKEN_LIFETIME_DAYS = 365

# Only V1 scope. Adding "snapshot" or "control" later means adding a value
# to this tuple and an `if scope == ...` branch in the route, no schema work.
ALLOWED_SCOPES: frozenset[str] = frozenset({"camera_stream"})

# Don't write to last_used_at more than once per minute per token. MJPEG
# streams call verify() at most once per fetch (the browser holds the
# connection open), but snapshots may rapid-fire — this caps DB churn.
_LAST_USED_DEBOUNCE = timedelta(minutes=1)

# Token format constants — kept in one place so format changes are localized.
_TOKEN_PREFIX = "bblt_"
_LOOKUP_LEN = 8
_SECRET_LEN = 32  # urlsafe characters → ~190 bits of entropy


@dataclass(frozen=True)
class CreatedToken:
    """Returned to the route on create. ``plaintext`` is shown to the user
    exactly once and never persisted; only ``record`` survives in the DB.
    """

    record: LongLivedToken
    plaintext: str


def _generate_token_parts() -> tuple[str, str, str]:
    """Return ``(plaintext, lookup_prefix, hash_input)``.

    ``hash_input`` is the same string we hand to pbkdf2 so verify() can
    produce a matching hash from the user-submitted token.

    The prefix is hex on purpose — ``token_urlsafe`` can emit ``_`` which
    would collide with the ``bblt_<prefix>_<secret>`` format separator and
    break the parser. Hex is fine for a non-secret indexed lookup column;
    the security comes from the 32-char ``token_urlsafe`` secret part.
    """
    lookup_prefix = secrets.token_hex(_LOOKUP_LEN // 2)  # 4 bytes → 8 hex chars
    secret_part = secrets.token_urlsafe(48).replace("_", "").replace("-", "")[:_SECRET_LEN]
    plaintext = f"{_TOKEN_PREFIX}{lookup_prefix}_{secret_part}"
    return plaintext, lookup_prefix, plaintext


def _parse_token(token: str) -> tuple[str, str] | None:
    """Pull ``(lookup_prefix, full_token)`` from a submitted string.

    Returns None if the format doesn't match — short-circuits the DB lookup
    on garbage / wrong-format inputs.
    """
    if not token.startswith(_TOKEN_PREFIX):
        return None
    rest = token[len(_TOKEN_PREFIX) :]
    sep = rest.find("_")
    if sep != _LOOKUP_LEN:
        return None
    lookup_prefix = rest[:_LOOKUP_LEN]
    return lookup_prefix, token


def _is_expired(record: LongLivedToken, now: datetime) -> bool:
    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= now


async def create_token(
    db: AsyncSession,
    *,
    user_id: int,
    name: str,
    expires_in_days: int,
    scope: str = "camera_stream",
) -> CreatedToken:
    """Mint a new long-lived token. Caller is responsible for permission checks.

    Raises ValueError if ``expires_in_days`` exceeds the policy cap or
    ``scope`` is not in ``ALLOWED_SCOPES``. The route translates these into
    a 400 with the offending field.
    """
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"unsupported scope: {scope!r}")
    if expires_in_days <= 0:
        raise ValueError("expires_in_days must be positive (#1108: no infinite tokens)")
    if expires_in_days > MAX_TOKEN_LIFETIME_DAYS:
        raise ValueError(f"expires_in_days exceeds policy maximum of {MAX_TOKEN_LIFETIME_DAYS}")
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > 100:
        raise ValueError("name must be 100 chars or fewer")

    plaintext, lookup_prefix, hash_input = _generate_token_parts()
    now = datetime.now(timezone.utc)
    record = LongLivedToken(
        user_id=user_id,
        name=name,
        lookup_prefix=lookup_prefix,
        secret_hash=get_password_hash(hash_input),
        scope=scope,
        expires_at=now + timedelta(days=expires_in_days),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return CreatedToken(record=record, plaintext=plaintext)


async def verify_token(db: AsyncSession, token: str, *, scope: str = "camera_stream") -> LongLivedToken | None:
    """Validate a token. Returns the matching record on success, None otherwise.

    The bcrypt-style verify is the slow step (intentional — pbkdf2 by design),
    so we pre-filter by the indexed ``lookup_prefix`` to ensure the verify
    runs against at most one or two candidate rows.
    """
    parsed = _parse_token(token)
    if parsed is None:
        return None
    lookup_prefix, full_token = parsed

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(LongLivedToken).where(
            LongLivedToken.lookup_prefix == lookup_prefix,
            LongLivedToken.scope == scope,
            LongLivedToken.revoked_at.is_(None),
        )
    )
    candidates = result.scalars().all()
    for record in candidates:
        if _is_expired(record, now):
            continue
        if not verify_password(full_token, record.secret_hash):
            continue
        # Record use, but rate-limit DB writes to keep MJPEG-keepalive cheap.
        last = record.last_used_at
        if last is None or _coerce_utc(last) + _LAST_USED_DEBOUNCE <= now:
            record.last_used_at = now
            await db.commit()
        return record
    return None


def _coerce_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def list_user_tokens(db: AsyncSession, user_id: int) -> list[LongLivedToken]:
    """All non-revoked tokens for a user, newest first. Includes expired ones
    (the UI shows them so the user can clean them up).
    """
    result = await db.execute(
        select(LongLivedToken)
        .where(LongLivedToken.user_id == user_id, LongLivedToken.revoked_at.is_(None))
        .order_by(LongLivedToken.created_at.desc())
    )
    return list(result.scalars().all())


async def list_all_tokens(db: AsyncSession) -> list[LongLivedToken]:
    """Admin view of every non-revoked token in the system, newest first."""
    result = await db.execute(
        select(LongLivedToken).where(LongLivedToken.revoked_at.is_(None)).order_by(LongLivedToken.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_token(db: AsyncSession, token_id: int) -> bool:
    """Mark a token revoked. Returns True if a row was updated, False if the
    id didn't exist or was already revoked.
    """
    result = await db.execute(select(LongLivedToken).where(LongLivedToken.id == token_id))
    record = result.scalar_one_or_none()
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return True
