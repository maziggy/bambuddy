"""
Orca Cloud API Routes

Device-pairing (RFC 8628) connect/disconnect + profile sync endpoints for the
Orca Cloud external-app surface.

Auth shape (see :mod:`backend.app.services.orca_cloud` for the deep dive):

    POST /orca-cloud/device/start
        Request a device code, persist it server-side (TTL 10 min), return the
        user_code + verification URIs + poll interval.
    POST /orca-cloud/device/poll
        One poll of the token endpoint. Returns an in-progress status while the
        user approves; on approval, persists the token pair and reports
        connected. The frontend calls this every ``interval`` seconds.
    GET  /orca-cloud/status
        Connected/disconnected + user_id.
    POST /orca-cloud/logout
        Clear stored tokens (Bambuddy then has no token to use; the user can
        also disconnect from Orca Cloud's own settings to revoke server-side).
    GET  /orca-cloud/profiles
        List of the user's Orca Cloud profiles, grouped by type. JIT-refreshes
        the access token if it's within the refresh leeway of expiry.
    GET  /orca-cloud/profiles/{id}
        Single profile's full content.

Storage shape mirrors the Bambu Cloud surface: per-user columns on ``users``
when auth is enabled, fallback to global ``settings`` keys when auth is
disabled. The transient pending device-code state (device_code, interval,
started_at) reuses the ``orca_cloud_pending_*`` columns — same dual-mode
pattern; no schema change from the previous PKCE flow.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import _cloud_api_key_gate, cloud_caller
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.orca_cloud import (
    OrcaAuthStatusResponse,
    OrcaDevicePollResponse,
    OrcaDeviceStartResponse,
    OrcaProfileDetail,
    OrcaProfileListResponse,
    OrcaProfileMeta,
)
from backend.app.services.orca_cloud import (
    DEVICE_CODE_TTL,
    DevicePoll,
    OrcaCloudAuthError,
    OrcaCloudError,
    OrcaCloudService,
)

logger = logging.getLogger(__name__)

# Router-level dependency: enforce the same API-key cloud-access fence as the
# Bambu Cloud router (rejects ownerless legacy keys, requires the
# ``can_access_cloud`` scope, stashes the owner on ``request.state`` so
# per-route deps can resolve it as the effective ``current_user``).
# Without this gate the kiosk's API-keyed requests sail past with
# ``current_user=None`` → ``_build_authenticated_service`` falls back to
# the global Settings table → no Orca token → 401, no presets surfaced.
# Bambu Cloud works in the same kiosk because its router has this gate.
router = APIRouter(prefix="/orca-cloud", tags=["orca-cloud"], dependencies=[Depends(_cloud_api_key_gate)])

# Orca ``content.type`` values map onto Bambu Cloud's preset type vocabulary.
# Empirically (confirmed against a live account on 2026-06-04): Orca uses
# ``"printer"`` / ``"print"`` / ``"filament"`` — NOT the BambuStudio
# ``"machine"`` / ``"process"`` / ``"filament"`` triplet that lives elsewhere
# in the OrcaSlicer source. The aliases keep us forward-compatible if Orca
# ever flips back to the older naming.
_ORCA_TYPE_TO_BAMBU = {
    "filament": "filament",
    "printer": "printer",
    "machine": "printer",  # alias for the BambuStudio-style naming
    "print": "process",
    "process": "process",  # alias for the BambuStudio-style naming
}


def _orca_to_setting(orca_profile: dict) -> OrcaProfileMeta | None:
    """Normalize one Orca profile (``{id, name, content, ...}``) into a
    ``SlicerSetting``-shaped row. Returns ``None`` if the content isn't a dict
    or the type isn't one we render."""
    content = orca_profile.get("content") or {}
    if not isinstance(content, dict):
        return None
    bambu_type = _ORCA_TYPE_TO_BAMBU.get(str(content.get("type", "")))
    if bambu_type is None:
        return None
    pid = orca_profile.get("id")
    if pid is None:
        return None
    updated = orca_profile.get("updated_time")
    return OrcaProfileMeta(
        setting_id=str(pid),
        name=str(orca_profile.get("name") or pid),
        type=bambu_type,
        version=_str_or_none(content.get("version")),
        # ``from`` distinguishes ``system`` (bundled) from ``User`` (custom),
        # same field the Bambu source-of-truth uses for that distinction.
        user_id=_str_or_none(content.get("user_id") or content.get("from")),
        updated_time=str(updated) if updated is not None else None,
        # Every profile that lives in the user's Orca Cloud account is by
        # definition user-authored; bundled defaults aren't synced.
        is_custom=True,
    )


def _str_or_none(value: object) -> str | None:
    """Cast non-empty scalars to ``str``; pass ``None`` and empty values
    through unchanged. Used to keep the response shape consistent when
    Orca's source data has heterogenous typing for the same field."""
    if value is None:
        return None
    s = str(value)
    return s if s else None


# Settings table keys for the auth-disabled fallback. Mirrors the Bambu Cloud
# pattern (``bambu_cloud_token`` etc.) so administrators inspecting the
# settings table see a consistent prefix. The ``pending_*`` keys hold the
# transient device-code state (device_code / interval / started_at).
_SETTINGS_KEYS = {
    "token": "orca_cloud_token",
    "refresh_token": "orca_cloud_refresh_token",
    "expires_at": "orca_cloud_expires_at",  # ISO 8601 UTC string
    "email": "orca_cloud_email",
    "user_id": "orca_cloud_user_id",
    "pending_device_code": "orca_cloud_pending_verifier",  # reused column
    "pending_interval": "orca_cloud_pending_state",  # reused column
    "pending_at": "orca_cloud_pending_at",  # ISO 8601 UTC string
}


# ---------------------------------------------------------------------------
# Storage helpers — bridge User-row vs Settings-table fallback transparently
# ---------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 UTC. ``None`` passes through."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _as_utc(dt: datetime | None) -> datetime | None:
    """Attach ``tzinfo=UTC`` to a naive datetime that we know was stored as
    UTC. ``None`` passes through. Already-aware datetimes are converted to
    UTC to normalize."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string back to a UTC datetime. ``None`` passes through."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class _OrcaCredentials:
    """Lightweight bag for stored Orca Cloud credentials. We use a class
    rather than a dataclass so the helpers can mutate it as needed during
    JIT-refresh without rebuilding the whole object.

    ``pending_device_code`` / ``pending_interval`` / ``pending_at`` hold the
    in-flight device-code pairing state (reusing the ``orca_cloud_pending_*``
    columns that the old PKCE flow used for its verifier/state)."""

    __slots__ = (
        "token",
        "refresh_token",
        "expires_at",
        "email",
        "user_id",
        "pending_device_code",
        "pending_interval",
        "pending_at",
    )

    def __init__(self) -> None:
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: datetime | None = None
        self.email: str | None = None
        self.user_id: str | None = None
        self.pending_device_code: str | None = None
        self.pending_interval: str | None = None
        self.pending_at: datetime | None = None


async def _load_credentials(db: AsyncSession, user: User | None) -> _OrcaCredentials:
    """Load stored Orca Cloud credentials for the caller (user-row when auth
    is enabled, Settings fallback when auth is disabled).

    Datetimes coming back from the User row are NAIVE on the Postgres side
    (asyncpg strips tzinfo for ``TIMESTAMP WITHOUT TIME ZONE`` columns) but
    represent UTC moments because that's what we stored. We attach
    ``tzinfo=UTC`` here so downstream comparisons against
    ``datetime.now(timezone.utc)`` don't get shifted by the host's local
    offset — ``naive_dt.astimezone(UTC)`` would assume local time, which on
    a UTC+2 host turns a 1-minute-old pending state into a 2h1m one and
    fires the 10-minute TTL guard immediately."""
    creds = _OrcaCredentials()
    if user is not None:
        creds.token = user.orca_cloud_token
        creds.refresh_token = user.orca_cloud_refresh_token
        creds.expires_at = _as_utc(user.orca_cloud_expires_at)
        creds.email = user.orca_cloud_email
        creds.user_id = user.orca_cloud_user_id
        creds.pending_device_code = user.orca_cloud_pending_verifier
        creds.pending_interval = user.orca_cloud_pending_state
        creds.pending_at = _as_utc(user.orca_cloud_pending_at)
        return creds

    result = await db.execute(select(Settings).where(Settings.key.in_(list(_SETTINGS_KEYS.values()))))
    raw = {s.key: s.value for s in result.scalars().all()}
    creds.token = raw.get(_SETTINGS_KEYS["token"])
    creds.refresh_token = raw.get(_SETTINGS_KEYS["refresh_token"])
    creds.expires_at = _parse_iso(raw.get(_SETTINGS_KEYS["expires_at"]))
    creds.email = raw.get(_SETTINGS_KEYS["email"])
    creds.user_id = raw.get(_SETTINGS_KEYS["user_id"])
    creds.pending_device_code = raw.get(_SETTINGS_KEYS["pending_device_code"])
    creds.pending_interval = raw.get(_SETTINGS_KEYS["pending_interval"])
    creds.pending_at = _parse_iso(raw.get(_SETTINGS_KEYS["pending_at"]))
    return creds


async def _persist_pending_device(
    db: AsyncSession,
    user: User | None,
    device_code: str,
    interval: int,
    when: datetime,
) -> None:
    """Store the transient device-code state used by ``/device/start`` ->
    ``/device/poll``. The device_code is a secret kept server-side."""
    if user is not None:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                orca_cloud_pending_verifier=device_code,
                orca_cloud_pending_state=str(interval),
                orca_cloud_pending_at=when,
            )
        )
        await db.commit()
        return
    await _upsert_settings(
        db,
        {
            _SETTINGS_KEYS["pending_device_code"]: device_code,
            _SETTINGS_KEYS["pending_interval"]: str(interval),
            _SETTINGS_KEYS["pending_at"]: _iso(when),
        },
    )


async def _clear_pending_device(db: AsyncSession, user: User | None) -> None:
    """Wipe just the pending device-code state (on terminal poll outcomes),
    leaving any existing tokens untouched."""
    if user is not None:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                orca_cloud_pending_verifier=None,
                orca_cloud_pending_state=None,
                orca_cloud_pending_at=None,
            )
        )
        await db.commit()
        return
    await _upsert_settings(
        db,
        {
            _SETTINGS_KEYS["pending_device_code"]: None,
            _SETTINGS_KEYS["pending_interval"]: None,
            _SETTINGS_KEYS["pending_at"]: None,
        },
    )


async def _persist_tokens(
    db: AsyncSession,
    user: User | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
    email: str | None,
    user_id: str | None,
) -> None:
    """Atomically write the new access/refresh pair to whichever backing store
    the deployment uses. Also clears the pending device-code state on the same
    write, since by this point the pairing is complete."""
    if user is not None:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                orca_cloud_token=access_token,
                orca_cloud_refresh_token=refresh_token,
                orca_cloud_expires_at=expires_at,
                orca_cloud_email=email,
                orca_cloud_user_id=user_id,
                orca_cloud_pending_verifier=None,
                orca_cloud_pending_state=None,
                orca_cloud_pending_at=None,
            )
        )
        await db.commit()
        return
    await _upsert_settings(
        db,
        {
            _SETTINGS_KEYS["token"]: access_token,
            _SETTINGS_KEYS["refresh_token"]: refresh_token,
            _SETTINGS_KEYS["expires_at"]: _iso(expires_at),
            _SETTINGS_KEYS["email"]: email,
            _SETTINGS_KEYS["user_id"]: user_id,
            _SETTINGS_KEYS["pending_device_code"]: None,
            _SETTINGS_KEYS["pending_interval"]: None,
            _SETTINGS_KEYS["pending_at"]: None,
        },
    )


async def _persist_rotated_tokens(
    db: AsyncSession,
    user: User | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
) -> None:
    """Persist tokens after a refresh — does NOT touch email/user_id and does
    NOT touch the pending state (refresh happens long after pairing)."""
    if user is not None:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                orca_cloud_token=access_token,
                orca_cloud_refresh_token=refresh_token,
                orca_cloud_expires_at=expires_at,
            )
        )
        await db.commit()
        return
    await _upsert_settings(
        db,
        {
            _SETTINGS_KEYS["token"]: access_token,
            _SETTINGS_KEYS["refresh_token"]: refresh_token,
            _SETTINGS_KEYS["expires_at"]: _iso(expires_at),
        },
    )


async def _clear_credentials(db: AsyncSession, user: User | None) -> None:
    """Wipe everything Orca-related (tokens, identity, pending state)."""
    if user is not None:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                orca_cloud_token=None,
                orca_cloud_refresh_token=None,
                orca_cloud_expires_at=None,
                orca_cloud_email=None,
                orca_cloud_user_id=None,
                orca_cloud_pending_verifier=None,
                orca_cloud_pending_state=None,
                orca_cloud_pending_at=None,
            )
        )
        await db.commit()
        return
    result = await db.execute(select(Settings).where(Settings.key.in_(list(_SETTINGS_KEYS.values()))))
    for setting in result.scalars().all():
        await db.delete(setting)
    await db.commit()


async def _upsert_settings(db: AsyncSession, values: dict[str, str | None]) -> None:
    """Idempotent upsert into the Settings table. ``None`` values delete the row."""
    keys = [k for k, _ in values.items()]
    result = await db.execute(select(Settings).where(Settings.key.in_(keys)))
    existing = {s.key: s for s in result.scalars().all()}
    for key, value in values.items():
        row = existing.get(key)
        if value is None:
            if row is not None:
                await db.delete(row)
            continue
        if row is not None:
            row.value = value
        else:
            db.add(Settings(key=key, value=value))
    await db.commit()


# ---------------------------------------------------------------------------
# Authenticated service builder with JIT refresh
# ---------------------------------------------------------------------------


async def _build_authenticated_service(
    db: AsyncSession,
    user: User | None,
) -> OrcaCloudService:
    """Construct an :class:`OrcaCloudService` pre-populated with stored
    credentials. If the access token is within the refresh-leeway of expiry,
    proactively refresh and persist the new pair BEFORE returning, so the
    next API call doesn't time out mid-flight on an expired token.

    We don't lock around the refresh: Orca tolerates concurrent refreshes for
    ~60s (each racer gets its own valid pair on the same connection rather than
    a revoke), so a lost race here is harmless — last-write-wins on the stored
    pair, and whichever pair we keep is valid."""
    creds = await _load_credentials(db, user)
    if not creds.token:
        raise HTTPException(status_code=401, detail="Orca Cloud is not connected — sign in first.")

    svc = OrcaCloudService()
    svc.set_tokens(creds.token, creds.refresh_token, creds.expires_at)
    if not svc.is_authenticated:
        if not svc.refresh_token:
            raise HTTPException(
                status_code=401,
                detail="Orca Cloud session expired and no refresh token is stored — sign in again.",
            )
        try:
            await svc.refresh()
        except OrcaCloudAuthError as e:
            # Refresh token was revoked or rotated out from under us. Clear
            # the stale credentials so the UI flips to disconnected.
            await _clear_credentials(db, user)
            raise HTTPException(status_code=401, detail=f"Orca Cloud session refresh failed: {e}") from e
        except OrcaCloudError as e:
            raise HTTPException(status_code=502, detail=f"Orca Cloud unreachable: {e}") from e
        # Persist new pair BEFORE returning. A crash between here and the
        # downstream API call would still leave the user with valid stored
        # tokens for the next request.
        await _persist_rotated_tokens(db, user, svc.access_token, svc.refresh_token, svc.token_expiry)
    return svc


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.post("/device/start", response_model=OrcaDeviceStartResponse)
async def device_start(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = cloud_caller(Permission.ORCA_CLOUD_AUTH),
):
    """Begin device pairing. Requests a device code from Orca, stores it
    server-side (the device_code is a secret and never leaves the backend),
    and returns the user_code + verification URIs + poll interval for the
    frontend to display and poll against."""
    svc = OrcaCloudService()
    # instance_url/label are display-only anti-phishing context on the approval
    # card. base_url may be off behind a reverse proxy, but it's harmless if so.
    instance_url = str(request.base_url).rstrip("/") or None
    try:
        data = await svc.request_device_code(instance_url=instance_url, instance_label="Bambuddy")
    except OrcaCloudAuthError as e:
        # invalid_client etc. — an operator misconfiguration, not user error.
        raise HTTPException(status_code=502, detail=f"Orca Cloud pairing is misconfigured: {e}") from e
    except OrcaCloudError as e:
        raise HTTPException(status_code=502, detail=f"Orca Cloud unreachable: {e}") from e

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    if not device_code or not user_code:
        raise HTTPException(status_code=502, detail="Orca Cloud returned an incomplete device-code response.")

    interval = int(data.get("interval") or 5)
    expires_in = int(data.get("expires_in") or DEVICE_CODE_TTL.total_seconds())
    await _persist_pending_device(db, current_user, device_code, interval, datetime.now(timezone.utc))

    return OrcaDeviceStartResponse(
        user_code=user_code,
        verification_uri=str(data.get("verification_uri") or ""),
        verification_uri_complete=str(data.get("verification_uri_complete") or ""),
        interval=interval,
        expires_in=expires_in,
    )


@router.post("/device/poll", response_model=OrcaDevicePollResponse)
async def device_poll(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = cloud_caller(Permission.ORCA_CLOUD_AUTH),
):
    """Poll the token endpoint once for the in-flight pairing. Returns an
    in-progress status while the user approves; on approval persists the token
    pair (clearing the pending state) and reports connected."""
    creds = await _load_credentials(db, current_user)
    if not creds.pending_device_code or not creds.pending_at:
        raise HTTPException(
            status_code=400,
            detail="No pending Orca Cloud pairing. Click Connect first to start the flow.",
        )

    # creds.pending_at is already tz-aware UTC after _load_credentials' _as_utc
    # normalization. Subtracting two aware UTC datetimes gives a real delta.
    age = datetime.now(timezone.utc) - creds.pending_at
    if age > DEVICE_CODE_TTL:
        await _clear_pending_device(db, current_user)
        return OrcaDevicePollResponse(status=DevicePoll.EXPIRED, connected=False)

    svc = OrcaCloudService()
    try:
        status, token_data = await svc.poll_token(creds.pending_device_code)
    except OrcaCloudError as e:
        raise HTTPException(status_code=502, detail=f"Orca Cloud unreachable: {e}") from e

    if status in DevicePoll.ONGOING:
        return OrcaDevicePollResponse(status=status, connected=False)

    if status in DevicePoll.TERMINAL:
        # access_denied / expired_token — the attempt is dead; clear it so the
        # user starts fresh next time.
        await _clear_pending_device(db, current_user)
        return OrcaDevicePollResponse(status=status, connected=False)

    # COMPLETE — tokens issued and applied to svc. Introspect for the user_id
    # (the external API's /me doesn't return an email, so email stays None).
    user_id: str | None = None
    try:
        info = await svc.introspect()
        if isinstance(info, dict):
            user_id = _str_or_none(info.get("user_id"))
    except OrcaCloudError as e:
        # Don't fail the whole pairing over the side introspection call — we
        # have valid tokens, which is the load-bearing part.
        logger.warning("Orca Cloud introspection failed after successful pairing: %s", e)

    await _persist_tokens(db, current_user, svc.access_token, svc.refresh_token, svc.token_expiry, None, user_id)
    return OrcaDevicePollResponse(status=DevicePoll.COMPLETE, connected=True, email=None, user_id=user_id)


@router.get("/status", response_model=OrcaAuthStatusResponse)
async def get_status(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = cloud_caller(Permission.ORCA_CLOUD_AUTH),
):
    """Return whether the caller has an Orca Cloud session stored, plus
    identifier details for display. Does NOT make a live API call."""
    creds = await _load_credentials(db, current_user)
    return OrcaAuthStatusResponse(
        connected=bool(creds.token),
        email=creds.email,
        user_id=creds.user_id,
    )


@router.post("/logout")
async def logout(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = cloud_caller(Permission.ORCA_CLOUD_AUTH),
):
    """Clear stored Orca Cloud credentials. Does not call Orca's disconnect
    endpoint (the user can revoke server-side from Orca Cloud's own settings;
    Bambuddy will no longer have the token to use either way)."""
    await _clear_credentials(db, current_user)
    return {"success": True}


@router.get("/profiles", response_model=OrcaProfileListResponse)
async def list_profiles(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = cloud_caller(Permission.ORCA_CLOUD_AUTH),
):
    """Return profile metadata grouped by type (``filament`` / ``printer``
    / ``process``), matching the ``SlicerSettingsResponse`` shape the
    Bambu Cloud tab consumes. This lets the frontend render Orca profiles
    with the same visual components — same cards, same filter bar, same
    grouping — without separate UI code paths."""
    svc = await _build_authenticated_service(db, current_user)
    try:
        raw_profiles = await svc.list_profiles()
    except OrcaCloudAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except OrcaCloudError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    grouped: dict[str, list[OrcaProfileMeta]] = {"filament": [], "printer": [], "process": []}
    # Log any unknown content.type values we silently drop, so a future
    # change in Orca's type vocabulary surfaces in the logs rather than
    # quietly losing profiles.
    unknown_types: dict[str, int] = {}
    for entry in raw_profiles:
        setting = _orca_to_setting(entry)
        if setting is None:
            content = entry.get("content") if isinstance(entry, dict) else None
            raw_type = (content.get("type") if isinstance(content, dict) else None) or "<missing>"
            unknown_types[str(raw_type)] = unknown_types.get(str(raw_type), 0) + 1
            continue
        grouped[setting.type].append(setting)
    if unknown_types:
        logger.warning(
            "Orca Cloud profile list dropped %d profiles with unmapped content.type values: %s",
            sum(unknown_types.values()),
            unknown_types,
        )
    return OrcaProfileListResponse(**grouped)


@router.get("/profiles/{profile_id}", response_model=OrcaProfileDetail)
async def get_profile(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = cloud_caller(Permission.ORCA_CLOUD_AUTH),
):
    """Fetch a single profile's full content, shaped like
    ``SlicerSettingDetail`` so the Bambu Cloud detail modal can render it
    unchanged. The inner ``setting`` field is the raw slicer-format JSON
    Orca stores — same shape Bambu Cloud uses since OrcaSlicer is a
    BambuStudio fork."""
    svc = await _build_authenticated_service(db, current_user)
    try:
        profile = await svc.get_profile(profile_id)
    except OrcaCloudAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except OrcaCloudError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e)) from e
        raise HTTPException(status_code=502, detail=str(e)) from e
    content = profile.get("content") if isinstance(profile, dict) else None
    if not isinstance(content, dict):
        content = {}
    orca_type = str(content.get("type", ""))
    bambu_type = _ORCA_TYPE_TO_BAMBU.get(orca_type, orca_type)
    update_time = profile.get("updated_time") if isinstance(profile, dict) else None
    return OrcaProfileDetail(
        setting_id=str(profile_id),
        name=str(profile.get("name") if isinstance(profile, dict) else "") or str(profile_id),
        type=bambu_type,
        version=_str_or_none(content.get("version")),
        base_id=_str_or_none(content.get("inherits") or content.get("base_id")),
        update_time=str(update_time) if update_time is not None else None,
        setting=content,
    )
