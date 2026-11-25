"""3D Filament Profiles routes (#2981).

    POST /threedfp/device/start   ask 3DFP for a device code, keep it, return the user_code
    POST /threedfp/device/poll    one poll of the token endpoint; stores the token once approved
    GET  /threedfp/status         connected or not, from what is stored (no 3DFP call)
    GET  /threedfp/account        the live plan, scopes and quota (3DFP's /me, free of quota)
    POST /threedfp/disconnect     revoke the token at 3DFP and forget it
    GET  /threedfp/lookup?q=      a barcode, a 3DFP filament id or a 3DFP filament link
    GET  /threedfp/filaments/{id} one filament with its weights and store links

    GET/PUT /threedfp/spool-links/{inventory}/{spool_id}   the filament a spool is linked to

Each user connects their own account, stored on their user row, or in the
settings table when auth is off, as for Orca Cloud. Lookups use the caller's
own token, so a free account's 100 requests a month are its own.

A 401 is never returned for 3DFP's answers: the frontend signs the user out of
Bambuddy on some 401 texts, and 3DFP rejecting its token says nothing about the
Bambuddy session. A connection 3DFP no longer accepts is 409.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import _cloud_api_key_gate, cloud_caller
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.threedfp_spool_link import ThreeDfpSpoolLink
from backend.app.models.user import User
from backend.app.schemas.threedfp import (
    ThreeDfpAccountResponse,
    ThreeDfpDevicePollResponse,
    ThreeDfpDeviceStartResponse,
    ThreeDfpFilament,
    ThreeDfpLookupResponse,
    ThreeDfpQuota,
    ThreeDfpSpoolLinkBody,
    ThreeDfpStatusResponse,
    ThreeDfpStore,
)
from backend.app.services.threedfp import DevicePoll, ThreeDfpClient, ThreeDfpError

logger = logging.getLogger(__name__)

# Same API-key fence as the other cloud accounts: a key needs its owner and the
# cloud scope, and the owner's own connection is the one used.
router = APIRouter(prefix="/threedfp", tags=["threedfp"], dependencies=[Depends(_cloud_api_key_gate)])
# The spool links are inventory data, not account access: no cloud fence.
links_router = APIRouter(prefix="/threedfp", tags=["threedfp"])

# 3DFP's device codes last 600 s; a pending attempt older than this is dead.
DEVICE_CODE_TTL = timedelta(minutes=10)

# Settings keys for the auth-disabled install. The device code is named
# "secret" so a Git backup writes a placeholder for it, as for the token.
_KEYS = {
    "token": "threedfp_token",
    "email": "threedfp_email",
    "pending_device_code": "threedfp_pending_device_secret",
    "pending_interval": "threedfp_pending_interval",
    "pending_at": "threedfp_pending_at",
}

_DISCONNECTED = "3D Filament Profiles no longer accepts this connection. Connect again in Settings."
_NOT_CONNECTED = "3D Filament Profiles is not connected. Connect it in Settings."


class _Creds:
    __slots__ = ("token", "email", "pending_device_code", "pending_interval", "pending_at")

    def __init__(self) -> None:
        self.token: str | None = None
        self.email: str | None = None
        self.pending_device_code: str | None = None
        self.pending_interval: int | None = None
        self.pending_at: datetime | None = None


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


async def _load(db: AsyncSession, user: User | None) -> _Creds:
    creds = _Creds()
    if user is not None:
        creds.token = user.threedfp_token
        creds.email = user.threedfp_email
        creds.pending_device_code = user.threedfp_pending_device_code
        creds.pending_interval = user.threedfp_pending_interval
        # Naive on both databases and stored as UTC.
        creds.pending_at = _as_utc(user.threedfp_pending_at)
        return creds
    rows = await db.execute(select(Settings).where(Settings.key.in_(list(_KEYS.values()))))
    raw = {s.key: s.value for s in rows.scalars().all()}
    creds.token = raw.get(_KEYS["token"]) or None
    creds.email = raw.get(_KEYS["email"]) or None
    creds.pending_device_code = raw.get(_KEYS["pending_device_code"]) or None
    try:
        creds.pending_interval = int(raw[_KEYS["pending_interval"]])
    except (KeyError, TypeError, ValueError):
        creds.pending_interval = None
    try:
        creds.pending_at = _as_utc(datetime.fromisoformat(raw[_KEYS["pending_at"]]))
    except (KeyError, TypeError, ValueError):
        creds.pending_at = None
    return creds


async def _save(db: AsyncSession, user: User | None, **values: object) -> None:
    """Write the given fields (``token``, ``email``, ``pending_*``); None clears one."""
    if user is not None:
        columns = {f"threedfp_{name}": value for name, value in values.items()}
        if "pending_at" in values and isinstance(values["pending_at"], datetime):
            columns["threedfp_pending_at"] = values["pending_at"].astimezone(timezone.utc).replace(tzinfo=None)
        await db.execute(update(User).where(User.id == user.id).values(**columns))
        await db.commit()
        return
    keys = {_KEYS[name]: value for name, value in values.items()}
    existing = {
        s.key: s for s in (await db.execute(select(Settings).where(Settings.key.in_(list(keys))))).scalars().all()
    }
    for key, value in keys.items():
        row = existing.get(key)
        if value is None:
            if row is not None:
                await db.delete(row)
            continue
        text = value.isoformat() if isinstance(value, datetime) else str(value)
        if row is not None:
            row.value = text
        else:
            db.add(Settings(key=key, value=text))
    await db.commit()


async def _clear(db: AsyncSession, user: User | None) -> None:
    await _save(db, user, **dict.fromkeys(_KEYS))


async def _clear_pending(db: AsyncSession, user: User | None) -> None:
    await _save(db, user, pending_device_code=None, pending_interval=None, pending_at=None)


async def _fail(e: ThreeDfpError, db: AsyncSession, user: User | None) -> NoReturn:
    """Turn a 3DFP error into the answer for the browser."""
    if e.token_is_dead:
        await _clear(db, user)
        raise HTTPException(status_code=409, detail=_DISCONNECTED) from e
    if e.status == 402:
        raise HTTPException(status_code=402, detail="Your 3D Filament Profiles plan does not include this.") from e
    if e.status == 429:
        headers = {"Retry-After": e.retry_after} if e.retry_after else None
        raise HTTPException(
            status_code=429, detail="3D Filament Profiles request limit reached.", headers=headers
        ) from e
    if e.status == 404:
        raise HTTPException(status_code=404, detail="Not found on 3D Filament Profiles.") from e
    logger.warning("3D Filament Profiles call failed: %s", e)
    raise HTTPException(status_code=502, detail="3D Filament Profiles could not be reached.") from e


async def _token(db: AsyncSession, user: User | None) -> str:
    token = (await _load(db, user)).token
    if not token:
        raise HTTPException(status_code=409, detail=_NOT_CONNECTED)
    return token


_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_TEXT_FIELDS = ("brand_name", "material", "material_type", "color", "bambu_filament_id")
_NUMBER_FIELDS = ("nominal_weight", "spool_weight", "temp_min", "temp_max")


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _filament(record: dict) -> ThreeDfpFilament:
    """Keep the fields the spool form uses, dropping a colour or store link
    that isn't what it claims to be: the store url ends up in an href."""
    rgb = record.get("rgb")
    match = _HEX.match(rgb) if isinstance(rgb, str) else None
    stores = []
    for store in record.get("stores") or []:
        if isinstance(store, dict) and isinstance(store.get("url"), str) and store["url"].startswith("https://"):
            try:
                stores.append(ThreeDfpStore.model_validate(store))
            except ValueError:
                continue
    page = record.get("url")
    fields: dict[str, object] = {key: _text(record.get(key)) for key in _TEXT_FIELDS}
    fields.update({key: _number(record.get(key)) for key in _NUMBER_FIELDS})
    return ThreeDfpFilament.model_validate(
        {
            **fields,
            "id": record["id"],
            "discontinued": record.get("discontinued") is True,
            "rgb": f"#{match.group(1).upper()}" if match else None,
            "url": page if isinstance(page, str) and page.startswith("https://") else None,
            "stores": stores,
        }
    )


_DETAILS_URL = re.compile(r"/filament/details/(\d+)")


def parse_lookup(query: str) -> tuple[Literal["gtin", "id"], str] | None:
    """A barcode (8 to 14 digits), a 3DFP filament id, or a link to a
    filament's page on 3dfilamentprofiles.com."""
    query = query.strip()
    link = _DETAILS_URL.search(query)
    if link:
        return "id", link.group(1)
    digits = re.sub(r"[\s-]", "", query)
    if not digits.isdigit():
        return None
    if 8 <= len(digits) <= 14:
        return "gtin", digits
    if len(digits) < 8 and int(digits) > 0:
        return "id", str(int(digits))
    return None


# --- Connecting ------------------------------------------------------------


@router.post("/device/start", response_model=ThreeDfpDeviceStartResponse)
async def device_start(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = cloud_caller(Permission.THREEDFP_AUTH),
):
    # 3DFP shows the name when the user approves and in their connected apps.
    host = request.url.hostname
    device_name = f"Bambuddy on {host}" if host else "Bambuddy"
    async with ThreeDfpClient() as client:
        try:
            data = await client.request_device_code(device_name)
        except ThreeDfpError as e:
            logger.warning("3D Filament Profiles device code failed: %s", e)
            raise HTTPException(status_code=502, detail="3D Filament Profiles could not start connecting.") from e
    interval = int(data.get("interval") or 5)
    await _save(
        db,
        current_user,
        pending_device_code=data["device_code"],
        pending_interval=interval,
        pending_at=datetime.now(timezone.utc),
    )
    return ThreeDfpDeviceStartResponse(
        user_code=str(data["user_code"]),
        verification_uri=str(data.get("verification_uri") or ""),
        verification_uri_complete=str(data.get("verification_uri_complete") or data.get("verification_uri") or ""),
        interval=interval,
        expires_in=int(data.get("expires_in") or DEVICE_CODE_TTL.total_seconds()),
    )


@router.post("/device/poll", response_model=ThreeDfpDevicePollResponse)
async def device_poll(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = cloud_caller(Permission.THREEDFP_AUTH),
):
    creds = await _load(db, current_user)
    if not creds.pending_device_code or not creds.pending_at:
        raise HTTPException(status_code=400, detail="No connection is being set up. Click Connect first.")
    if datetime.now(timezone.utc) - creds.pending_at > DEVICE_CODE_TTL:
        await _clear_pending(db, current_user)
        return ThreeDfpDevicePollResponse(status=DevicePoll.EXPIRED)

    async with ThreeDfpClient() as client:
        try:
            status = await client.poll_token(creds.pending_device_code)
        except ThreeDfpError as e:
            logger.warning("3D Filament Profiles token poll failed: %s", e)
            raise HTTPException(status_code=502, detail="3D Filament Profiles could not be reached.") from e
        if status in DevicePoll.ONGOING:
            return ThreeDfpDevicePollResponse.model_validate({"status": status})
        if status in DevicePoll.TERMINAL:
            await _clear_pending(db, current_user)
            return ThreeDfpDevicePollResponse.model_validate({"status": status})

        email = None
        try:
            account = (await client.me()).get("account")
            if isinstance(account, dict) and isinstance(account.get("email"), str):
                email = account["email"]
        except ThreeDfpError as e:
            # The token is what matters; the email only labels the card.
            logger.info("3D Filament Profiles /me failed right after connecting: %s", e)

    await _save(
        db,
        current_user,
        token=client.token,
        email=email,
        pending_device_code=None,
        pending_interval=None,
        pending_at=None,
    )
    return ThreeDfpDevicePollResponse(status=DevicePoll.COMPLETE, connected=True)


@router.get("/status", response_model=ThreeDfpStatusResponse)
async def get_status(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = cloud_caller(Permission.THREEDFP_AUTH),
):
    creds = await _load(db, current_user)
    return ThreeDfpStatusResponse(connected=bool(creds.token), email=creds.email)


@router.get("/account", response_model=ThreeDfpAccountResponse)
async def get_account(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = cloud_caller(Permission.THREEDFP_AUTH),
):
    async with ThreeDfpClient(await _token(db, current_user)) as client:
        try:
            me = await client.me()
        except ThreeDfpError as e:
            await _fail(e, db, current_user)
    raw_account = me.get("account")
    account: dict = raw_account if isinstance(raw_account, dict) else {}
    email = account.get("email") if isinstance(account.get("email"), str) else None
    if email and email != (await _load(db, current_user)).email:
        await _save(db, current_user, email=email)
    quota = me.get("quota") if isinstance(me.get("quota"), dict) else None
    upgrade = me.get("upgrade_url")
    scopes = me.get("active_scopes")
    return ThreeDfpAccountResponse(
        email=email,
        plan=me.get("plan") if isinstance(me.get("plan"), str) else None,
        active_scopes=[s for s in scopes if isinstance(s, str)] if isinstance(scopes, list) else [],
        quota=ThreeDfpQuota.model_validate(quota) if quota else None,
        upgrade_url=upgrade if isinstance(upgrade, str) and upgrade.startswith("https://") else None,
    )


@router.post("/disconnect")
async def disconnect(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = cloud_caller(Permission.THREEDFP_AUTH),
):
    creds = await _load(db, current_user)
    if creds.token:
        async with ThreeDfpClient(creds.token) as client:
            await client.revoke()
    await _clear(db, current_user)
    return {"success": True}


# --- Lookup ----------------------------------------------------------------


@router.get("/lookup", response_model=ThreeDfpLookupResponse)
async def lookup(
    q: str = Query(..., min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = cloud_caller(Permission.THREEDFP_AUTH),
):
    """Filaments for a barcode, or the one filament for an id or a link.

    A barcode answers summaries (no weights or stores); the form fetches the
    chosen one with ``/filaments/{id}``.
    """
    parsed = parse_lookup(q)
    if parsed is None:
        raise HTTPException(
            status_code=400, detail="Enter a barcode, a 3D Filament Profiles filament id or a link to one."
        )
    kind, value = parsed
    async with ThreeDfpClient(await _token(db, current_user)) as client:
        try:
            if kind == "gtin":
                records = await client.lookup_gtin(value)
            else:
                records = [await client.get_filament(int(value))]
        except ThreeDfpError as e:
            if e.status == 404:
                return ThreeDfpLookupResponse(results=[])
            await _fail(e, db, current_user)
    results = []
    for record in records:
        if isinstance(record.get("id"), int):
            results.append(_filament(record))
    return ThreeDfpLookupResponse(results=results)


@router.get("/filaments/{filament_id}", response_model=ThreeDfpFilament)
async def get_filament(
    filament_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = cloud_caller(Permission.THREEDFP_AUTH),
):
    async with ThreeDfpClient(await _token(db, current_user)) as client:
        try:
            record = await client.get_filament(filament_id)
        except ThreeDfpError as e:
            await _fail(e, db, current_user)
    return _filament(record)


# --- Spool links -----------------------------------------------------------

Inventory = Literal["inventory", "spoolman"]


def _link_filter(inventory: Inventory, spool_id: int):
    if inventory == "inventory":
        return ThreeDfpSpoolLink.spool_id == spool_id
    return ThreeDfpSpoolLink.spoolman_spool_id == spool_id


@links_router.get("/spool-links/{inventory}/{spool_id}", response_model=ThreeDfpSpoolLinkBody)
async def get_spool_link(
    inventory: Inventory,
    spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    link = (await db.execute(select(ThreeDfpSpoolLink).where(_link_filter(inventory, spool_id)))).scalar_one_or_none()
    return ThreeDfpSpoolLinkBody(filament_id=link.filament_id if link else None)


@links_router.put("/spool-links/{inventory}/{spool_id}", response_model=ThreeDfpSpoolLinkBody)
async def put_spool_link(
    inventory: Inventory,
    body: ThreeDfpSpoolLinkBody,
    spool_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Link a spool to a 3DFP filament, or unlink it with ``null``."""
    if inventory == "inventory":
        if (await db.execute(select(Spool.id).where(Spool.id == spool_id))).scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Spool not found")
    else:
        # The spool lives in Spoolman; only the link is Bambuddy's.
        from backend.app.api.routes.spoolman_inventory import _get_client, _translate_spoolman_errors

        client = await _get_client(db)
        async with _translate_spoolman_errors():
            await client.get_spool(spool_id)

    link = (await db.execute(select(ThreeDfpSpoolLink).where(_link_filter(inventory, spool_id)))).scalar_one_or_none()
    if body.filament_id is None:
        if link is not None:
            await db.delete(link)
    elif link is not None:
        link.filament_id = body.filament_id
    else:
        db.add(
            ThreeDfpSpoolLink(
                spool_id=spool_id if inventory == "inventory" else None,
                spoolman_spool_id=spool_id if inventory == "spoolman" else None,
                filament_id=body.filament_id,
            )
        )
    await db.commit()
    return ThreeDfpSpoolLinkBody(filament_id=body.filament_id)
