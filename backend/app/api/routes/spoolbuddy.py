"""SpoolBuddy device management API routes."""

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.websocket import ws_manager
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice
from backend.app.models.user import User
from backend.app.schemas.spoolbuddy import (
    CalibrationResponse,
    DeviceRegisterRequest,
    DeviceResponse,
    DiagnosticResultRequest,
    DisplaySettingsRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    ScaleReadingRequest,
    SetCalibrationFactorRequest,
    SetTareRequest,
    SystemCommandRequest,
    SystemCommandResultRequest,
    SystemConfigRequest,
    TagRemovedRequest,
    TagScannedRequest,
    UpdateSpoolWeightRequest,
    UpdateStatusRequest,
    WriteTagRequest,
    WriteTagResultRequest,
)
from backend.app.services.spool_tag_matcher import get_spool_by_tag
from backend.app.services.spoolman import SpoolmanClientError, SpoolmanNotFoundError, SpoolmanUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spoolbuddy", tags=["spoolbuddy"])

OFFLINE_THRESHOLD_SECONDS = 30
ONLINE_BROADCAST_INTERVAL_SECONDS = 10
_SSRF_WARN_THROTTLE_SECONDS = 60
_spoolbuddy_online_last_broadcast: dict[str, float] = {}
_ssrf_warn_last_broadcast: dict[str, float] = {}
_diagnostic_results: dict[tuple[str, str], dict] = {}


@contextlib.asynccontextmanager
async def _translate_spoolbuddy_errors():
    """Translate Spoolman typed exceptions to HTTP for SpoolBuddy endpoints."""
    try:
        yield
    except SpoolmanNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Spool not found in Spoolman") from exc
    except SpoolmanClientError as exc:
        raise HTTPException(status_code=502, detail="Spoolman rejected the request") from exc
    except SpoolmanUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Spoolman server is not reachable") from exc


async def _get_spoolman_client_or_none(db: AsyncSession):
    """Return a SpoolmanClient if Spoolman is enabled with a safe URL, else None."""
    from backend.app.api.routes._spoolman_helpers import assert_safe_spoolman_url
    from backend.app.models.settings import Settings
    from backend.app.services.spoolman import get_spoolman_client, init_spoolman_client

    settings_result = await db.execute(select(Settings))
    settings_dict = {s.key: s.value for s in settings_result.scalars().all()}
    spoolman_url = settings_dict.get("spoolman_url", "").strip()
    spoolman_enabled = settings_dict.get("spoolman_enabled", "false").lower() == "true" and bool(spoolman_url)

    if not spoolman_enabled:
        return None

    # SSRF guard: reject dangerous schemes, cloud-metadata IPs (169.254.169.254, 100.100.100.200,
    # fd00:ec2::254), multicast and unspecified addresses — loopback and RFC-1918 ranges are
    # intentionally permitted (Spoolman commonly runs on the same host or home LAN).
    try:
        assert_safe_spoolman_url(spoolman_url)
    except ValueError as exc:
        logger.warning(
            "Spoolman integration disabled: URL %r rejected by SSRF guard: %s",
            spoolman_url,
            exc,
        )
        now = time.monotonic()
        if now - _ssrf_warn_last_broadcast.get(spoolman_url, 0) > _SSRF_WARN_THROTTLE_SECONDS:
            _ssrf_warn_last_broadcast[spoolman_url] = now
            await ws_manager.broadcast(
                {
                    "type": "spoolman_ssrf_blocked",
                    "detail": "Spoolman URL was rejected by the SSRF guard",
                }
            )
        return None

    client = await get_spoolman_client()
    if not client or client.base_url != spoolman_url.rstrip("/"):
        try:
            client = await init_spoolman_client(spoolman_url)
        except ValueError as exc:
            logger.warning(
                "Spoolman integration disabled: URL %r rejected on re-initialisation: %s",
                spoolman_url,
                exc,
            )
            return None
    return client


def _is_online(device: SpoolBuddyDevice) -> bool:
    if not device.last_seen:
        return False
    return (
        datetime.now(timezone.utc) - device.last_seen.replace(tzinfo=timezone.utc)
    ).total_seconds() < OFFLINE_THRESHOLD_SECONDS


def _device_to_response(device: SpoolBuddyDevice) -> DeviceResponse:
    return DeviceResponse(
        id=device.id,
        device_id=device.device_id,
        hostname=device.hostname,
        ip_address=device.ip_address,
        firmware_version=device.firmware_version,
        has_nfc=device.has_nfc,
        has_scale=device.has_scale,
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
        nfc_reader_type=device.nfc_reader_type,
        nfc_connection=device.nfc_connection,
        backend_url=device.backend_url,
        display_brightness=device.display_brightness,
        display_blank_timeout=device.display_blank_timeout,
        has_backlight=device.has_backlight,
        last_calibrated_at=device.last_calibrated_at,
        last_seen=device.last_seen,
        pending_command=device.pending_command,
        nfc_ok=device.nfc_ok,
        scale_ok=device.scale_ok,
        uptime_s=device.uptime_s,
        update_status=device.update_status,
        update_message=device.update_message,
        system_stats=json.loads(device.system_stats) if device.system_stats else None,
        online=_is_online(device),
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


def _should_broadcast_online(device_id: str, force: bool = False) -> bool:
    if force:
        _spoolbuddy_online_last_broadcast[device_id] = time.time()
        return True

    now_ts = time.time()
    last_ts = _spoolbuddy_online_last_broadcast.get(device_id, 0.0)
    if now_ts - last_ts >= ONLINE_BROADCAST_INTERVAL_SECONDS:
        _spoolbuddy_online_last_broadcast[device_id] = now_ts
        return True
    return False


# --- Device endpoints ---


@router.post("/devices/register", response_model=DeviceResponse)
async def register_device(
    req: DeviceRegisterRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Register or re-register a SpoolBuddy device."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == req.device_id))
    device = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if device:
        device.hostname = req.hostname
        device.ip_address = req.ip_address
        device.firmware_version = req.firmware_version
        device.has_nfc = req.has_nfc
        device.has_scale = req.has_scale
        device.nfc_reader_type = req.nfc_reader_type
        device.nfc_connection = req.nfc_connection
        if req.backend_url:
            device.backend_url = req.backend_url
        device.has_backlight = req.has_backlight
        device.last_seen = now
        # Clear stale update status on re-registration (daemon restarted after update)
        if device.update_status in ("pending", "updating", "complete", "error"):
            device.update_status = None
            device.update_message = None
        logger.info("SpoolBuddy device re-registered: %s (%s)", req.device_id, req.hostname)
    else:
        device = SpoolBuddyDevice(
            device_id=req.device_id,
            hostname=req.hostname,
            ip_address=req.ip_address,
            firmware_version=req.firmware_version,
            has_nfc=req.has_nfc,
            has_scale=req.has_scale,
            tare_offset=req.tare_offset,
            calibration_factor=req.calibration_factor,
            nfc_reader_type=req.nfc_reader_type,
            nfc_connection=req.nfc_connection,
            has_backlight=req.has_backlight,
            backend_url=req.backend_url,
            last_seen=now,
        )
        db.add(device)
        logger.info("SpoolBuddy device registered: %s (%s)", req.device_id, req.hostname)

    await db.commit()
    await db.refresh(device)

    _spoolbuddy_online_last_broadcast[device.device_id] = time.time()
    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_online",
            "device_id": device.device_id,
            "hostname": device.hostname,
        }
    )

    response = _device_to_response(device)

    # Include SSH public key so the daemon can auto-deploy it
    try:
        from backend.app.services.spoolbuddy_ssh import get_public_key

        response.ssh_public_key = await get_public_key()
    except Exception as exc:
        logger.warning("Could not attach SSH public key to heartbeat response: %s", exc)

    return response


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """List all registered SpoolBuddy devices."""
    result = await db.execute(select(SpoolBuddyDevice).order_by(SpoolBuddyDevice.hostname))
    devices = list(result.scalars().all())
    return [_device_to_response(d) for d in devices]


@router.delete("/devices/{device_id}")
async def unregister_device(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_DELETE),
):
    """Unregister a SpoolBuddy device. The daemon can re-register via heartbeat later."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    await db.delete(device)
    await db.commit()
    _spoolbuddy_online_last_broadcast.pop(device_id, None)
    logger.info("SpoolBuddy device unregistered: %s (%s)", device_id, device.hostname)
    await ws_manager.broadcast({"type": "spoolbuddy_unregistered", "device_id": device_id})
    return {"status": "deleted", "device_id": device_id}


@router.post("/devices/{device_id}/heartbeat", response_model=HeartbeatResponse)
async def device_heartbeat(
    device_id: str,
    req: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Daemon heartbeat — updates status and returns pending commands."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    was_offline = not _is_online(device)
    now = datetime.now(timezone.utc)

    device.last_seen = now
    device.nfc_ok = req.nfc_ok
    device.scale_ok = req.scale_ok
    device.uptime_s = req.uptime_s
    if req.firmware_version:
        device.firmware_version = req.firmware_version
    if req.ip_address:
        device.ip_address = req.ip_address
    if req.nfc_reader_type:
        device.nfc_reader_type = req.nfc_reader_type
    if req.nfc_connection:
        device.nfc_connection = req.nfc_connection
    if req.backend_url:
        device.backend_url = req.backend_url
    if req.system_stats is not None:
        device.system_stats = json.dumps(req.system_stats)

    # Return and clear pending command
    pending = device.pending_command
    pending_write = None
    pending_system = None
    if pending == "write_tag" and device.pending_write_payload:
        # Parse the stored JSON payload to include in response
        try:
            pending_write = json.loads(device.pending_write_payload)
        except (json.JSONDecodeError, TypeError):
            pending_write = None
        # Don't clear write_tag command — it gets cleared by write-result
    elif pending == "apply_system_config" and device.pending_system_payload:
        try:
            pending_system = json.loads(device.pending_system_payload)
        except (json.JSONDecodeError, TypeError):
            pending_system = None
        # Don't clear config command — it gets cleared by daemon command-result callback
    elif pending and pending.startswith("run_") and pending.endswith("_diag"):
        # Don't clear diagnostic commands — they get cleared by the device reporting results
        pass
    else:
        device.pending_command = None

    await db.commit()

    # Emit online presence on offline->online transitions immediately, and
    # periodically while online so newly connected UIs can bootstrap state.
    if _should_broadcast_online(device.device_id, force=was_offline):
        await ws_manager.broadcast(
            {
                "type": "spoolbuddy_online",
                "device_id": device.device_id,
                "hostname": device.hostname,
            }
        )
    if was_offline:
        logger.info("SpoolBuddy device back online: %s", device.device_id)

    # Include current SSH public key so the daemon can re-deploy it whenever
    # Bambuddy's keypair rotates (data dir wiped, container recreated, etc.) —
    # otherwise SSH updates fail until the daemon restarts.
    ssh_public_key: str | None = None
    try:
        from backend.app.services.spoolbuddy_ssh import get_public_key

        ssh_public_key = await get_public_key()
    except Exception:
        pass

    return HeartbeatResponse(
        pending_command=pending,
        pending_write_payload=pending_write,
        pending_system_payload=pending_system,
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
        display_brightness=device.display_brightness,
        display_blank_timeout=device.display_blank_timeout,
        ssh_public_key=ssh_public_key,
    )


# --- NFC endpoints ---


@router.post("/nfc/tag-scanned")
async def nfc_tag_scanned(
    req: TagScannedRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """RPi reports NFC tag detected — lookup spool and broadcast.

    Routes the lookup to the inventory backend Bambuddy is configured for:
    Spoolman exclusively when ``spoolman_enabled`` is true, local DB
    exclusively otherwise. The previous implementation always tried local
    first and only consulted Spoolman as a fallback on local-DB miss, which
    meant a stale local copy of a tag would silently win over the
    authoritative Spoolman row, and deleting the local copy was the only way
    to surface the Spoolman match. Operators expect the SpoolBuddy lookup to
    follow the inventory mode they selected in Bambuddy settings.
    """
    from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

    # _get_spoolman_client_or_none returns a usable client when spoolman_enabled
    # is true (and the URL passes the SSRF guard), None otherwise — so its
    # return value doubles as the mode discriminator.
    client = await _get_spoolman_client_or_none(db)

    if client is not None:
        # Spoolman mode — exclusive lookup, no local-DB fallback.
        try:
            cached_spools = await client.get_spools()
            sm_spool: dict | None = None
            if req.tray_uuid:
                sm_spool = await client.find_spool_by_tag(req.tray_uuid, cached_spools=cached_spools)
            if sm_spool is None and req.tag_uid:
                sm_spool = await client.find_spool_by_tag(req.tag_uid, cached_spools=cached_spools)

            if sm_spool is not None:
                mapped = _map_spoolman_spool(sm_spool)
                await ws_manager.broadcast(
                    {
                        "type": "spoolbuddy_tag_matched",
                        "device_id": req.device_id,
                        "tag_uid": req.tag_uid,
                        "tray_uuid": req.tray_uuid,
                        "spool": {
                            "id": mapped["id"],
                            "material": mapped["material"],
                            "subtype": mapped["subtype"],
                            "color_name": mapped["color_name"],
                            "rgba": mapped["rgba"],
                            "brand": mapped["brand"],
                            "label_weight": mapped["label_weight"],
                            "core_weight": mapped["core_weight"],
                            "weight_used": mapped["weight_used"],
                        },
                    }
                )
                logger.info("SpoolBuddy tag matched (Spoolman): %s -> spool %d", req.tag_uid, mapped["id"])
                return {"status": "ok", "matched": True, "spool_id": mapped["id"]}
        except ValueError as exc:
            logger.error(
                "Spoolman returned malformed spool data during tag lookup for %s: %s",
                req.tag_uid,
                exc,
            )
            return {"status": "ok", "matched": False, "spool_id": None}
        except (httpx.RequestError, httpx.HTTPStatusError, SpoolmanUnavailableError):
            logger.warning(
                "Spoolman unreachable during tag lookup for %s",
                req.tag_uid,
            )
            # Broadcast a diagnostic event so the UI can surface "Spoolman down" to the user.
            # Use a distinct type from spoolbuddy_unknown_tag — Spoolman outage != unregistered spool.
            await ws_manager.broadcast(
                {
                    "type": "spoolman_unavailable",
                    "device_id": req.device_id,
                    "context": "nfc_tag_scanned",
                }
            )
            return {"status": "ok", "matched": False, "spool_id": None}
        except Exception as exc:
            logger.error(
                "Spoolman tag lookup failed unexpectedly for %s: %s",
                req.tag_uid,
                exc,
            )
            # Broadcast a distinct error event so operators can distinguish
            # "unexpected backend error" from "unregistered tag".
            await ws_manager.broadcast(
                {
                    "type": "spoolbuddy_lookup_error",
                    "device_id": req.device_id,
                }
            )
            # Same silent-return policy: an unexpected error must not break device operation
            # or trigger spurious duplicate-registration flows in the UI.
            return {"status": "ok", "matched": False, "spool_id": None}
    else:
        # Local mode — exclusive lookup, no Spoolman fallback.
        spool = await get_spool_by_tag(db, req.tag_uid, req.tray_uuid or "")
        if spool:
            await ws_manager.broadcast(
                {
                    "type": "spoolbuddy_tag_matched",
                    "device_id": req.device_id,
                    "tag_uid": req.tag_uid,
                    "tray_uuid": req.tray_uuid,
                    "spool": {
                        "id": spool.id,
                        "material": spool.material,
                        "subtype": spool.subtype,
                        "color_name": spool.color_name,
                        "rgba": spool.rgba,
                        "brand": spool.brand,
                        "label_weight": spool.label_weight,
                        "core_weight": spool.core_weight,
                        "weight_used": spool.weight_used,
                    },
                }
            )
            logger.info("SpoolBuddy tag matched (local): %s -> spool %d", req.tag_uid, spool.id)
            return {"status": "ok", "matched": True, "spool_id": spool.id}

    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_unknown_tag",
            "device_id": req.device_id,
            "tag_uid": req.tag_uid,
            "tray_uuid": req.tray_uuid,
            "sak": req.sak,
            "tag_type": req.tag_type,
        }
    )
    logger.info(
        "SpoolBuddy unknown tag: uid=%s (len=%d), tray_uuid=%s (len=%d), type=%s, sak=%s",
        req.tag_uid,
        len(req.tag_uid or ""),
        req.tray_uuid,
        len(req.tray_uuid or ""),
        req.tag_type,
        req.sak,
    )
    return {"status": "ok", "matched": False, "spool_id": None}


@router.post("/nfc/tag-removed")
async def nfc_tag_removed(
    req: TagRemovedRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """RPi reports NFC tag removed — broadcast event."""
    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_tag_removed",
            "device_id": req.device_id,
            "tag_uid": req.tag_uid,
        }
    )
    return {"status": "ok"}


@router.post("/nfc/write-tag")
async def nfc_write_tag(
    req: WriteTagRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Queue an NFC tag write command for a SpoolBuddy device."""
    from backend.app.models.spool import Spool
    from backend.app.services.opentag3d import encode_opentag3d, encode_opentag3d_from_mapped

    # Find the device first (required regardless of spool source)
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == req.device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    # Try local DB first
    result = await db.execute(select(Spool).where(Spool.id == req.spool_id))
    spool = result.scalar_one_or_none()

    nfc_warnings: list[str] = []
    if spool:
        ndef_data = encode_opentag3d(spool)
        data_origin = "local"
    else:
        # Local DB miss — fall back to Spoolman when enabled
        from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

        sm_client = await _get_spoolman_client_or_none(db)
        if sm_client is None:
            raise HTTPException(status_code=404, detail="Spool not found")

        async with _translate_spoolbuddy_errors():
            sm_spool = await sm_client.get_spool(req.spool_id)

        try:
            mapped = _map_spoolman_spool(sm_spool)
        except ValueError as exc:
            logger.warning("Spoolman returned invalid spool for write-tag: %s", exc)
            raise HTTPException(status_code=502, detail="Spoolman returned malformed spool data")

        if not mapped.get("material"):
            raise HTTPException(
                status_code=400,
                detail="Spoolman spool has no material set — cannot encode NFC tag",
            )

        ndef_data = encode_opentag3d_from_mapped(mapped)
        data_origin = "spoolman"

        # Warn when fields that drive NFC content are absent in Spoolman.
        # color_name specifically must check the raw filament field, not the
        # mapped value — _map_spoolman_spool falls back to the filament's
        # subtype when color_name is unset (so LinkSpoolModal stops showing
        # "Unknown color"), but the NFC tag should still warn when Spoolman
        # has no genuine color_name on file. Without this, the fallback
        # silently masks a real missing-data condition.
        raw_filament: dict = sm_spool.get("filament") or {}
        if not raw_filament.get("color_name"):
            nfc_warnings.append("color_name not set in Spoolman — tag encodes empty color name")
        if not mapped.get("nozzle_temp_min"):
            nfc_warnings.append("nozzle_temp_min not set in Spoolman — tag encodes 0 °C")
        if not mapped.get("subtype"):
            nfc_warnings.append("subtype not set in Spoolman — tag encodes empty subtype")
        if not mapped.get("brand"):
            nfc_warnings.append("brand/vendor not set in Spoolman — tag encodes empty brand")
        if not mapped.get("rgba"):
            nfc_warnings.append("rgba not set in Spoolman — tag encodes default colour")
        if not mapped.get("label_weight"):
            nfc_warnings.append("label_weight not set in Spoolman — tag encodes 0 g")
        if nfc_warnings:
            logger.warning(
                "NFC encode for Spoolman spool %d has incomplete data: %s",
                req.spool_id,
                "; ".join(nfc_warnings),
            )

    # Store write payload and set pending command
    device.pending_write_payload = json.dumps(
        {
            "spool_id": req.spool_id,
            "ndef_data_hex": ndef_data.hex(),
            "data_origin": data_origin,
        }
    )
    device.pending_command = "write_tag"
    await db.commit()

    logger.info(
        "Write tag queued for device %s, spool %d (%s, %d bytes)",
        req.device_id,
        req.spool_id,
        data_origin,
        len(ndef_data),
    )
    result: dict = {"status": "queued"}
    if nfc_warnings:
        result["warnings"] = nfc_warnings
    return result


@router.post("/nfc/write-result")
async def nfc_write_result(
    req: WriteTagResultRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Handle NFC tag write result from SpoolBuddy daemon."""
    # Find the device
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == req.device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    # Capture data_origin before clearing the payload
    try:
        payload_dict = json.loads(device.pending_write_payload or "{}")
    except (json.JSONDecodeError, TypeError):
        payload_dict = {}
        logger.warning("Malformed pending_write_payload for device %s — treating as local", req.device_id)
    data_origin = payload_dict.get("data_origin", "local")

    device.pending_command = None
    device.pending_write_payload = None

    if req.success:
        if data_origin == "spoolman":
            # Update Spoolman extra.tag with the written NFC UID using a safe merge
            # (fetches current extra first to avoid overwriting other custom fields).
            sm_client = await _get_spoolman_client_or_none(db)
            if sm_client is None:
                logger.warning("Spoolman not configured; cannot persist tag link for spool %d", req.spool_id)
                await db.commit()
                await ws_manager.broadcast(
                    {
                        "type": "spoolbuddy_tag_link_failed",
                        "device_id": req.device_id,
                        "spool_id": req.spool_id,
                        "tag_uid": req.tag_uid,
                        "message": "Spoolman not configured",
                    }
                )
                raise HTTPException(
                    status_code=502,
                    detail="Tag written to NFC but Spoolman is not configured; link not persisted",
                )

            _tag_link_ok = False
            try:
                tag_value = json.dumps(req.tag_uid.upper())
                # Tag uniqueness: a single physical NFC UID must map to at most
                # one Spoolman spool, otherwise find_spool_by_tag returns
                # whichever spool comes first in the cached list (usually the
                # older one) and the dashboard shows the wrong spool when the
                # tag is scanned. Before binding the new owner, clear the tag
                # from any other spool that currently has it. Best-effort:
                # cleanup failure does not block the write itself, but the
                # warning surfaces in logs so a stale duplicate can be tracked
                # down manually.
                try:
                    cached_spools = await sm_client.get_spools()
                    duplicate = await sm_client.find_spool_by_tag(req.tag_uid, cached_spools=cached_spools)
                    if duplicate is not None and duplicate.get("id") != req.spool_id:
                        await sm_client.merge_spool_extra(int(duplicate["id"]), {"tag": ""})
                        logger.info(
                            "Spoolman: cleared tag %s from previous holder spool %d before binding to spool %d",
                            req.tag_uid,
                            duplicate["id"],
                            req.spool_id,
                        )
                except (SpoolmanNotFoundError, SpoolmanUnavailableError, SpoolmanClientError) as cleanup_exc:
                    logger.warning(
                        "Spoolman: failed to clear duplicate tag %s before binding to spool %d (proceeding anyway): %s",
                        req.tag_uid,
                        req.spool_id,
                        cleanup_exc,
                    )
                except Exception:
                    logger.exception(
                        "Spoolman: unexpected error clearing duplicate tag %s before binding to spool %d (proceeding anyway)",
                        req.tag_uid,
                        req.spool_id,
                    )

                await sm_client.merge_spool_extra(req.spool_id, {"tag": tag_value})
                logger.info(
                    "Spoolman tag written and linked: spool %d -> tag %s",
                    req.spool_id,
                    req.tag_uid,
                )
                _tag_link_ok = True
            except (SpoolmanNotFoundError, SpoolmanUnavailableError, SpoolmanClientError) as exc:
                logger.error(
                    "Spoolman error during tag write-back for spool %d (type=%s, status=%s): %s",
                    req.spool_id,
                    type(exc).__name__,
                    getattr(exc, "status_code", "N/A"),
                    exc,
                )
                # fall through to broadcast + raise 502 below
            except Exception:
                logger.exception(
                    "Unexpected error during Spoolman tag write-back for spool %d",
                    req.spool_id,
                )
                # fall through to broadcast + raise 502 below

            await db.commit()
            if _tag_link_ok:
                await ws_manager.broadcast(
                    {
                        "type": "spoolbuddy_tag_written",
                        "device_id": req.device_id,
                        "spool_id": req.spool_id,
                        "tag_uid": req.tag_uid,
                    }
                )
            else:
                await ws_manager.broadcast(
                    {
                        "type": "spoolbuddy_tag_link_failed",
                        "device_id": req.device_id,
                        "spool_id": req.spool_id,
                        "tag_uid": req.tag_uid,
                        # Generic message — full exception (may contain internal URLs/hostnames)
                        # is logged server-side only to prevent information leakage via WebSocket.
                        "message": "Spoolman link failed",
                    }
                )
                raise HTTPException(
                    status_code=502,
                    detail="Tag written to NFC but Spoolman link failed",
                )
        else:
            # Link the tag to the local DB spool
            from backend.app.models.spool import Spool

            result = await db.execute(select(Spool).where(Spool.id == req.spool_id))
            spool = result.scalar_one_or_none()
            if spool is None:
                logger.warning(
                    "NFC tag written for spool %d but it no longer exists in local DB; tag is orphaned",
                    req.spool_id,
                )
                await db.commit()
                await ws_manager.broadcast(
                    {
                        "type": "spoolbuddy_tag_link_failed",
                        "device_id": req.device_id,
                        "spool_id": req.spool_id,
                        "message": "Spool not found",
                    }
                )
                return {"status": "ok", "linked": False, "message": "Spool not found"}

            spool.tag_uid = req.tag_uid.upper()
            spool.tag_type = "ntag"
            spool.data_origin = "opentag3d"
            spool.encode_time = datetime.now(timezone.utc)
            logger.info("Tag written and linked: spool %d -> tag %s", spool.id, req.tag_uid)

            await db.commit()
            await ws_manager.broadcast(
                {
                    "type": "spoolbuddy_tag_written",
                    "device_id": req.device_id,
                    "spool_id": req.spool_id,
                    "tag_uid": req.tag_uid,
                }
            )
    else:
        await db.commit()
        await ws_manager.broadcast(
            {
                "type": "spoolbuddy_tag_write_failed",
                "device_id": req.device_id,
                "spool_id": req.spool_id,
                "message": req.message,
            }
        )
        logger.warning("Tag write failed for device %s: %s", req.device_id, req.message)

    return {"status": "ok"}


@router.post("/devices/{device_id}/cancel-write")
async def cancel_write(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Cancel a pending write-tag command."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    if device.pending_command == "write_tag":
        device.pending_command = None
        device.pending_write_payload = None
        await db.commit()
        logger.info("Write tag cancelled for device %s", device_id)

    return {"status": "ok"}


# --- Scale endpoints ---


@router.post("/scale/reading")
async def scale_reading(
    req: ScaleReadingRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """RPi reports scale weight — broadcast to all clients."""
    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_weight",
            "device_id": req.device_id,
            "weight_grams": req.weight_grams,
            "stable": req.stable,
            "raw_adc": req.raw_adc,
        }
    )
    return {"status": "ok"}


@router.post("/scale/update-spool-weight")
async def update_spool_weight(
    req: UpdateSpoolWeightRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Update spool's used weight from scale reading."""
    from backend.app.api.routes._spoolman_helpers import _safe_float
    from backend.app.models.spool import Spool

    # Try local DB first — local spool IDs must not be forwarded to Spoolman.
    db_result = await db.execute(select(Spool).where(Spool.id == req.spool_id))
    spool = db_result.scalar_one_or_none()

    if spool:
        net_filament = max(0, req.weight_grams - spool.core_weight)
        spool.weight_used = max(0, spool.label_weight - net_filament)
        spool.last_scale_weight = req.weight_grams
        spool.last_weighed_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(
            "SpoolBuddy updated spool %d weight: %.1fg on scale, %.1fg used",
            spool.id,
            req.weight_grams,
            spool.weight_used,
        )
        return {"status": "ok", "weight_used": spool.weight_used}

    # Local miss — fall back to Spoolman when enabled.
    sm_client = await _get_spoolman_client_or_none(db)
    if sm_client is None:
        raise HTTPException(status_code=404, detail="Spool not found")

    async with _translate_spoolbuddy_errors():
        sm_spool = await sm_client.get_spool(req.spool_id)

    filament = sm_spool.get("filament") or {}
    spool_tare = sm_spool.get("spool_weight")
    raw_tare = spool_tare if spool_tare is not None else filament.get("spool_weight")
    spool_weight_warning: str | None = None
    if raw_tare is None:
        logger.warning(
            "Spoolman spool %d has no spool_weight set; using 250g fallback for tare",
            req.spool_id,
        )
        spool_weight_warning = (
            "spool_weight_not_set: Spoolman filament has no spool_weight configured; weight estimate uses 250g fallback"
        )
    core_weight = _safe_float(raw_tare, 250.0)
    label_weight = _safe_float(filament.get("weight"), 1000.0)
    remaining_weight = max(0.0, req.weight_grams - core_weight)

    async with _translate_spoolbuddy_errors():
        await sm_client.update_spool(spool_id=req.spool_id, remaining_weight=remaining_weight)

    weight_used = max(0.0, label_weight - remaining_weight)
    logger.info(
        "SpoolBuddy updated Spoolman spool %d: %.1fg on scale, core=%.1fg → %.1fg remaining",
        req.spool_id,
        req.weight_grams,
        core_weight,
        remaining_weight,
    )
    result: dict = {"status": "ok", "weight_used": weight_used}
    if spool_weight_warning:
        result["warnings"] = [spool_weight_warning]
    return result


# --- Calibration endpoints ---


@router.post("/devices/{device_id}/calibration/tare")
async def tare_scale(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Set pending tare command for the device to pick up."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    device.pending_command = "tare"
    await db.commit()
    return {"status": "ok", "message": "Tare command queued"}


@router.post("/devices/{device_id}/calibration/set-tare")
async def set_tare_offset(
    device_id: str,
    req: SetTareRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Store tare offset reported by the daemon after executing a tare."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    device.tare_offset = req.tare_offset
    device.last_calibrated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("SpoolBuddy %s tare offset set to %d", device_id, req.tare_offset)
    return CalibrationResponse(
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
    )


@router.post("/devices/{device_id}/calibration/set-factor")
async def set_calibration_factor(
    device_id: str,
    req: SetCalibrationFactorRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Calculate and store calibration factor from a known weight."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    tare = req.tare_raw_adc if req.tare_raw_adc is not None else device.tare_offset
    raw_delta = req.raw_adc - tare
    if raw_delta == 0:
        raise HTTPException(status_code=400, detail="Raw ADC value equals tare offset — place weight on scale")

    device.calibration_factor = req.known_weight_grams / raw_delta
    if req.tare_raw_adc is not None:
        device.tare_offset = tare
    device.last_calibrated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
        "SpoolBuddy %s calibration factor set to %.6f (known=%.1fg, raw=%d, tare=%d)",
        device_id,
        device.calibration_factor,
        req.known_weight_grams,
        req.raw_adc,
        tare,
    )
    return CalibrationResponse(
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
    )


@router.get("/devices/{device_id}/calibration", response_model=CalibrationResponse)
async def get_calibration(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get current calibration values for a device."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    return CalibrationResponse(
        tare_offset=device.tare_offset,
        calibration_factor=device.calibration_factor,
    )


# --- Display settings ---


@router.get("/devices/{device_id}/display")
async def get_display_settings(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Read current display brightness and screen blank timeout for a device.

    Used by the SpoolBuddy kiosk idle watchdog on autostart to configure
    swayidle with the same timeout the user picked in the UI, without having
    to wait for the daemon heartbeat to arrive first.
    """
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")
    return {
        "brightness": device.display_brightness,
        "blank_timeout": device.display_blank_timeout,
    }


@router.put("/devices/{device_id}/display")
async def update_display_settings(
    device_id: str,
    req: DisplaySettingsRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Update display brightness and screen blank timeout for a device."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    device.display_brightness = req.brightness
    device.display_blank_timeout = req.blank_timeout
    await db.commit()

    logger.info(
        "SpoolBuddy %s display updated: brightness=%d%%, blank_timeout=%ds",
        device_id,
        req.brightness,
        req.blank_timeout,
    )
    return {"status": "ok", "brightness": req.brightness, "blank_timeout": req.blank_timeout}


@router.post("/devices/{device_id}/system/config")
async def queue_system_config_update(
    device_id: str,
    req: SystemConfigRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Queue update of SpoolBuddy .env config on the device."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    parsed = urlparse(req.backend_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="backend_url must be a full URL with scheme, e.g. http://192.168.1.100:5000 or http://bambuddy.local",
        )

    payload = {
        "backend_url": req.backend_url.strip(),
    }
    if req.api_key is not None and req.api_key.strip():
        payload["api_key"] = req.api_key.strip()

    device.pending_system_payload = json.dumps(payload)
    device.pending_command = "apply_system_config"
    await db.commit()

    logger.info("Queued system config update for device %s", device_id)
    return {"status": "queued", "message": "System config update queued"}


VALID_SYSTEM_COMMANDS = {"reboot", "shutdown", "restart_daemon", "restart_browser"}


@router.post("/devices/{device_id}/system/command")
async def queue_system_command(
    device_id: str,
    req: SystemCommandRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Queue a system command (reboot, shutdown, restart_daemon, restart_browser) for the SpoolBuddy device."""
    if req.command not in VALID_SYSTEM_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid command. Must be one of: {', '.join(sorted(VALID_SYSTEM_COMMANDS))}",
        )

    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    if not _is_online(device):
        raise HTTPException(status_code=409, detail="Device is offline")

    device.pending_command = req.command
    await db.commit()

    logger.info("System command queued for device %s: %s", device_id, req.command)
    return {"status": "queued", "command": req.command}


@router.post("/devices/{device_id}/system/command-result")
async def system_command_result(
    device_id: str,
    req: SystemCommandResultRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Receive completion status for queued system command from daemon."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    if not device.pending_command:
        logger.info("System command result from %s with no pending command: %s", device_id, req.command)
        return {"status": "ok", "message": "No pending command"}

    if req.command != device.pending_command:
        raise HTTPException(
            status_code=409,
            detail=f"Command mismatch: pending '{device.pending_command}', got '{req.command}'",
        )

    if req.command == "apply_system_config":
        device.pending_system_payload = None
    device.pending_command = None
    await db.commit()

    logger.info(
        "System command result from %s: %s success=%s message=%s",
        device_id,
        req.command,
        req.success,
        req.message,
    )
    return {"status": "ok"}


# --- Diagnostics ---


@router.post("/diagnostics/{device_id}/run")
async def queue_diagnostic(
    device_id: str,
    diagnostic: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Queue a hardware diagnostic to run on the SpoolBuddy device.

    Args:
        device_id: The device ID
        diagnostic: 'scale' or 'nfc' to select which diagnostic to run

    Returns:
        Status message indicating diagnostic was queued
    """
    if diagnostic not in ("scale", "nfc", "read_tag"):
        raise HTTPException(status_code=400, detail="Unknown diagnostic. Must be 'scale', 'nfc', or 'read_tag'")

    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    device.pending_command = f"run_{diagnostic}_diag"
    _diagnostic_results.pop((device_id, diagnostic), None)
    await db.commit()

    logger.info("Diagnostic queued for device %s: %s", device_id, diagnostic)
    return {"status": "queued", "diagnostic": diagnostic, "message": f"Diagnostic '{diagnostic}' queued for device"}


@router.get("/diagnostics/{device_id}/result")
async def get_diagnostic_result(
    device_id: str,
    diagnostic: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Get the latest diagnostic result for a device.

    Args:
        device_id: The device ID
        diagnostic: 'scale' or 'nfc'

    Returns:
        Diagnostic result or 404 if not found
    """
    if diagnostic not in ("scale", "nfc", "read_tag"):
        raise HTTPException(status_code=400, detail="Unknown diagnostic. Must be 'scale', 'nfc', or 'read_tag'")

    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    diag_result = _diagnostic_results.get((device_id, diagnostic))
    if not diag_result:
        raise HTTPException(status_code=404, detail=f"No {diagnostic} diagnostic results available yet")
    return diag_result


@router.post("/diagnostics/{device_id}/result")
async def report_diagnostic_result(
    device_id: str,
    req: DiagnosticResultRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Report diagnostic result from SpoolBuddy device."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    if req.diagnostic not in ("nfc", "scale", "read_tag"):
        raise HTTPException(status_code=400, detail="Unknown diagnostic. Must be 'scale', 'nfc', or 'read_tag'")

    _diagnostic_results[(device_id, req.diagnostic)] = {
        "diagnostic": req.diagnostic,
        "success": req.success,
        "output": req.output,
        "exit_code": req.exit_code,
    }

    device.pending_command = None
    await db.commit()

    logger.info("Diagnostic result received for device %s: %s (success=%s)", device_id, req.diagnostic, req.success)
    return {"status": "ok", "message": "Diagnostic result recorded"}


# --- Update check ---


@router.get("/devices/{device_id}/update-check")
async def check_daemon_update(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Check if the SpoolBuddy daemon needs updating to match the Bambuddy backend version."""
    from backend.app.api.routes.updates import is_newer_version
    from backend.app.core.config import APP_VERSION

    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    current = device.firmware_version or "0.0.0"

    return {
        "current_version": current,
        "latest_version": APP_VERSION,
        "update_available": is_newer_version(APP_VERSION, current),
    }


@router.post("/devices/{device_id}/update")
async def trigger_daemon_update(
    device_id: str,
    req: dict | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Trigger a SpoolBuddy update over SSH.

    Bambuddy SSHes into the device, pulls the matching branch, installs deps,
    and restarts the daemon. Progress is broadcast via WebSocket.
    """
    from backend.app.services.spoolbuddy_ssh import perform_ssh_update

    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    if not _is_online(device):
        raise HTTPException(status_code=409, detail="Device is offline")

    if device.update_status == "updating":
        return {"status": "already_updating", "message": "Update already in progress"}

    device.update_status = "pending"
    device.update_message = "Starting SSH update..."
    await db.commit()

    logger.info("SpoolBuddy %s: SSH update triggered (ip=%s)", device_id, device.ip_address)
    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_update",
            "device_id": device_id,
            "update_status": "pending",
        }
    )

    # Run the SSH update in the background — hold reference to prevent GC cancellation
    _ssh_update_task = asyncio.create_task(perform_ssh_update(device_id, device.ip_address))
    _ssh_update_task.add_done_callback(
        lambda t: logger.error(
            "SSH update task for device %s ended unexpectedly (cancelled=%s)",
            device_id,
            t.cancelled(),
        )
        if (t.cancelled() or t.exception() is not None)
        else None
    )

    return {"status": "ok", "message": "SSH update started"}


@router.get("/ssh/public-key")
async def get_ssh_public_key(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Return the SSH public key for SpoolBuddy pairing."""
    from backend.app.services.spoolbuddy_ssh import get_public_key

    try:
        key = await get_public_key()
        return {"public_key": key}
    except Exception as e:
        logger.error("Failed to get SSH public key: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve SSH public key") from e


@router.post("/devices/{device_id}/update-status")
async def report_update_status(
    device_id: str,
    req: UpdateStatusRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
):
    """Daemon reports update progress back to the backend."""
    result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    device.update_status = req.status
    device.update_message = req.message
    # Only "complete" clears pending_command here. "error" leaves it set so the user can retry
    # via the UI. The SSH service's own _update_progress clears on both "complete" and "error"
    # because it owns the full update lifecycle end-to-end.
    if req.status == "complete":
        device.pending_command = None
    await db.commit()

    logger.info("SpoolBuddy %s: update status=%s msg=%s", device_id, req.status, req.message)
    await ws_manager.broadcast(
        {
            "type": "spoolbuddy_update",
            "device_id": device_id,
            "update_status": req.status,
            "update_message": req.message,
        }
    )

    return {"status": "ok"}


# --- Background watchdog ---


async def spoolbuddy_watchdog():
    """Check for devices that have gone offline (no heartbeat for 30s).

    Called periodically from the main app's background task loop.
    """
    from backend.app.core.database import async_session

    async with async_session() as db:
        result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.last_seen.isnot(None)))
        devices = list(result.scalars().all())

        threshold = datetime.now(timezone.utc) - timedelta(seconds=OFFLINE_THRESHOLD_SECONDS)
        for device in devices:
            last_seen = device.last_seen.replace(tzinfo=timezone.utc) if device.last_seen else None
            if last_seen and last_seen < threshold:
                # Only broadcast once — clear last_seen after marking offline
                await ws_manager.broadcast(
                    {
                        "type": "spoolbuddy_offline",
                        "device_id": device.device_id,
                    }
                )
                device.last_seen = None
                logger.info("SpoolBuddy device offline: %s", device.device_id)

        await db.commit()
