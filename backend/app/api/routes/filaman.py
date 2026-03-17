"""FilaMan integration API routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.filaman import (
    FilaManClient,
    close_filaman_client,
    get_filaman_client,
    init_filaman_client,
)
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/filaman", tags=["filaman"])


class FilaManStatus(BaseModel):
    """FilaMan connection status."""

    enabled: bool
    connected: bool
    url: str | None


class SyncResult(BaseModel):
    """Result of a FilaMan sync operation."""

    success: bool
    synced_count: int
    skipped_count: int = 0
    errors: list[str]


async def get_filaman_settings(db: AsyncSession) -> dict:
    """Get FilaMan settings from database.

    Returns:
        Dict with keys: enabled, url, api_key, sync_mode, disable_weight_sync
    """
    fm = {
        "enabled": False,
        "url": "",
        "api_key": "",
        "sync_mode": "auto",
        "disable_weight_sync": False,
    }

    result = await db.execute(select(Settings))
    for setting in result.scalars().all():
        if setting.key == "filaman_enabled":
            fm["enabled"] = setting.value.lower() == "true"
        elif setting.key == "filaman_url":
            fm["url"] = setting.value
        elif setting.key == "filaman_api_key":
            fm["api_key"] = setting.value
        elif setting.key == "filaman_sync_mode":
            fm["sync_mode"] = setting.value
        elif setting.key == "filaman_disable_weight_sync":
            fm["disable_weight_sync"] = setting.value.lower() == "true"

    return fm


async def _upsert_setting(db: AsyncSession, key: str, value: str):
    """Insert or update a setting in the database."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        db.add(Settings(key=key, value=value))


async def _get_or_init_client(fm: dict) -> FilaManClient:
    """Get the global FilaMan client, initializing it if needed."""
    client = get_filaman_client()
    if not client:
        url = fm.get("url", "")
        api_key = fm.get("api_key", "")
        if url and api_key:
            client = init_filaman_client(url, api_key)
        else:
            raise HTTPException(status_code=400, detail="FilaMan URL or API key is not configured")
    return client


@router.get("/status", response_model=FilaManStatus)
async def get_filaman_status(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get FilaMan integration status."""
    fm = await get_filaman_settings(db)
    enabled, url = fm["enabled"], fm["url"]

    client = get_filaman_client()
    connected = False
    if client:
        connected = await client.health_check()

    return FilaManStatus(
        enabled=enabled,
        connected=connected,
        url=url if url else None,
    )


class ConnectRequest(BaseModel):
    """Request to connect to a FilaMan server."""

    url: str
    api_key: str


@router.post("/connect")
async def connect_filaman(
    request: ConnectRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Connect to FilaMan server, saving URL and API key."""
    url = request.url.rstrip("/")
    api_key = request.api_key.strip()

    if not url:
        raise HTTPException(status_code=400, detail="FilaMan URL is required")
    if not api_key:
        raise HTTPException(status_code=400, detail="FilaMan API key is required")
    if not api_key.startswith("uak."):
        raise HTTPException(
            status_code=400,
            detail="API key must be a FilaMan user API key (format: uak.xxx.xxx). Create one in FilaMan under Settings > API Keys.",
        )

    try:
        client = init_filaman_client(url, api_key)
        connected = await client.health_check()

        if not connected:
            raise HTTPException(
                status_code=503,
                detail=f"Could not connect to FilaMan at {url}",
            )

        # Persist settings to database
        await _upsert_setting(db, "filaman_url", url)
        await _upsert_setting(db, "filaman_api_key", api_key)
        await _upsert_setting(db, "filaman_enabled", "true")
        await db.commit()

        logger.info("Connected to FilaMan at %s", url)
        return {"success": True, "message": f"Connected to FilaMan at {url}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to connect to FilaMan: %s", e)
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/disconnect")
async def disconnect_filaman(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Disconnect from FilaMan server."""
    await close_filaman_client()
    await _upsert_setting(db, "filaman_enabled", "false")
    await db.commit()
    return {"success": True, "message": "Disconnected from FilaMan"}


@router.get("/spools")
async def get_spools(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get all spools from FilaMan in Bambuddy-compatible format."""
    fm = await get_filaman_settings(db)
    if not fm["enabled"]:
        raise HTTPException(status_code=400, detail="FilaMan integration is not enabled")

    client = await _get_or_init_client(fm)
    if not await client.health_check():
        raise HTTPException(status_code=503, detail="FilaMan is not reachable")

    spools = await client.get_spools()
    mapped = [client.map_spool_to_bambuddy(s) for s in spools]
    return {"spools": mapped}


@router.get("/filaments")
async def get_filaments(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get all filaments from FilaMan."""
    fm = await get_filaman_settings(db)
    if not fm["enabled"]:
        raise HTTPException(status_code=400, detail="FilaMan integration is not enabled")

    client = await _get_or_init_client(fm)
    if not await client.health_check():
        raise HTTPException(status_code=503, detail="FilaMan is not reachable")

    filaments = await client.get_filaments()
    return {"filaments": filaments}


class UnlinkedSpool(BaseModel):
    """A FilaMan spool that is not linked to any AMS tray."""

    id: int
    filament_name: str | None
    filament_material: str | None
    filament_color_hex: str | None
    remaining_weight: float | None
    brand: str | None


@router.get("/spools/unlinked", response_model=list[UnlinkedSpool])
async def get_unlinked_spools(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get all FilaMan spools that don't have a tray linked (rfid_uid is empty)."""
    fm = await get_filaman_settings(db)
    if not fm["enabled"]:
        raise HTTPException(status_code=400, detail="FilaMan integration is not enabled")

    client = await _get_or_init_client(fm)
    if not await client.health_check():
        raise HTTPException(status_code=503, detail="FilaMan is not reachable")

    spools = await client.get_spools()
    unlinked = []

    for spool in spools:
        rfid_uid = spool.get("rfid_uid") or ""
        if rfid_uid.strip():
            continue  # Already linked

        mapped = client.map_spool_to_bambuddy(spool)
        unlinked.append(
            UnlinkedSpool(
                id=spool["id"],
                filament_name=mapped.get("name"),
                filament_material=mapped.get("material"),
                filament_color_hex=mapped.get("color_hex"),
                remaining_weight=mapped.get("remaining_weight"),
                brand=mapped.get("brand"),
            )
        )

    return unlinked


@router.get("/spools/linked")
async def get_linked_spools(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """Get a map of rfid_uid (tray_uuid) -> spool_id for all linked FilaMan spools."""
    fm = await get_filaman_settings(db)
    if not fm["enabled"]:
        raise HTTPException(status_code=400, detail="FilaMan integration is not enabled")

    client = await _get_or_init_client(fm)
    if not await client.health_check():
        raise HTTPException(status_code=503, detail="FilaMan is not reachable")

    spools = await client.get_spools()
    linked: dict[str, dict] = {}

    for spool in spools:
        rfid_uid = spool.get("rfid_uid") or ""
        if not rfid_uid.strip():
            continue
        clean_uid = rfid_uid.strip().upper()
        linked[clean_uid] = {
            "id": spool["id"],
            "remaining_weight": spool.get("remaining_weight_g"),
            "initial_weight": spool.get("initial_total_weight_g") or spool.get("label_weight"),
        }

    return {"linked": linked}


class LinkSpoolRequest(BaseModel):
    """Request to link a FilaMan spool to an AMS tray."""

    tray_uuid: str


@router.post("/spools/{spool_id}/link")
async def link_spool(
    spool_id: int,
    request: LinkSpoolRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_UPDATE),
):
    """Link a FilaMan spool to an AMS tray by storing the tray_uuid as rfid_uid."""
    fm = await get_filaman_settings(db)
    if not fm["enabled"]:
        raise HTTPException(status_code=400, detail="FilaMan integration is not enabled")

    client = await _get_or_init_client(fm)
    if not await client.health_check():
        raise HTTPException(status_code=503, detail="FilaMan is not reachable")

    tray_uuid = request.tray_uuid.strip()
    if not tray_uuid:
        raise HTTPException(status_code=400, detail="tray_uuid is required")

    result = await client.update_spool_rfid(spool_id, tray_uuid)

    if result:
        logger.info("Linked FilaMan spool %s to tray_uuid %s", spool_id, tray_uuid)
        return {"success": True, "message": f"Spool {spool_id} linked to AMS tray"}
    else:
        raise HTTPException(status_code=500, detail="Failed to update spool in FilaMan")


@router.post("/sync/{printer_id}", response_model=SyncResult)
async def sync_printer_ams(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_UPDATE),
):
    """Sync AMS data from a specific printer to FilaMan (report remaining weight as consumption)."""
    fm = await get_filaman_settings(db)
    if not fm["enabled"]:
        raise HTTPException(status_code=400, detail="FilaMan integration is not enabled")

    client = await _get_or_init_client(fm)
    if not await client.health_check():
        raise HTTPException(status_code=503, detail="FilaMan is not reachable")

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    state = printer_manager.get_status(printer_id)
    if not state:
        raise HTTPException(status_code=404, detail="Printer not connected")

    if not state.raw_data:
        raise HTTPException(status_code=400, detail="No AMS data available")

    ams_data = state.raw_data.get("ams")
    if not ams_data:
        raise HTTPException(
            status_code=400,
            detail="No AMS data in printer state. Try triggering a slot re-read on the printer.",
        )

    synced = 0
    skipped = 0
    errors = []

    # Fetch all spools once for batch processing
    try:
        all_spools = await client.get_spools()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch spools from FilaMan: {e}")

    # Build rfid_uid → spool lookup for fast access
    spool_by_rfid = {
        (s.get("rfid_uid") or "").strip().upper(): s for s in all_spools if (s.get("rfid_uid") or "").strip()
    }

    ams_units = _extract_ams_units(ams_data)
    if not ams_units:
        raise HTTPException(status_code=400, detail="AMS data format not supported")

    disable_weight_sync = fm["disable_weight_sync"]

    for ams_unit in ams_units:
        if not isinstance(ams_unit, dict):
            continue
        for tray_data in ams_unit.get("tray", []):
            if not isinstance(tray_data, dict):
                continue
            if not tray_data.get("tray_type", "").strip():
                continue  # Empty tray

            tray_uuid = tray_data.get("tray_uuid", "") or ""
            if not tray_uuid or tray_uuid == "00000000000000000000000000000000":
                skipped += 1
                continue

            spool = spool_by_rfid.get(tray_uuid.strip().upper())
            if not spool:
                skipped += 1
                continue

            if disable_weight_sync:
                synced += 1
                continue

            # Calculate consumption from remaining percentage
            try:
                remain = int(tray_data.get("remain", -1))
                tray_weight = int(tray_data.get("tray_weight", 0))
                if remain >= 0 and tray_weight > 0:
                    remaining_g = (remain / 100.0) * tray_weight
                    initial_weight = spool.get("initial_total_weight_g") or spool.get("label_weight") or 0
                    current_remaining = spool.get("remaining_weight_g") or initial_weight
                    delta = current_remaining - remaining_g
                    if delta > 0.5:  # Only report meaningful consumption (>0.5g)
                        await client.report_consumption(spool["id"], round(delta, 2))
                synced += 1
            except Exception as e:
                ams_id = ams_unit.get("id", "?")
                tray_id = tray_data.get("id", "?")
                errors.append(f"Error syncing AMS {ams_id} tray {tray_id}: {e}")

    return SyncResult(
        success=len(errors) == 0,
        synced_count=synced,
        skipped_count=skipped,
        errors=errors,
    )


@router.post("/sync-all", response_model=SyncResult)
async def sync_all_printers(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_UPDATE),
):
    """Sync AMS data from all connected printers to FilaMan."""
    fm = await get_filaman_settings(db)
    if not fm["enabled"]:
        raise HTTPException(status_code=400, detail="FilaMan integration is not enabled")

    client = await _get_or_init_client(fm)
    if not await client.health_check():
        raise HTTPException(status_code=503, detail="FilaMan is not reachable")

    result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
    printers = result.scalars().all()

    total_synced = 0
    total_skipped = 0
    all_errors = []

    try:
        all_spools = await client.get_spools()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch spools from FilaMan: {e}")

    spool_by_rfid = {
        (s.get("rfid_uid") or "").strip().upper(): s for s in all_spools if (s.get("rfid_uid") or "").strip()
    }

    disable_weight_sync = fm["disable_weight_sync"]

    for printer in printers:
        state = printer_manager.get_status(printer.id)
        if not state or not state.raw_data:
            continue

        ams_data = state.raw_data.get("ams")
        if not ams_data:
            continue

        ams_units = _extract_ams_units(ams_data)
        if not ams_units:
            continue

        for ams_unit in ams_units:
            if not isinstance(ams_unit, dict):
                continue
            for tray_data in ams_unit.get("tray", []):
                if not isinstance(tray_data, dict):
                    continue
                if not tray_data.get("tray_type", "").strip():
                    continue

                tray_uuid = tray_data.get("tray_uuid", "") or ""
                if not tray_uuid or tray_uuid == "00000000000000000000000000000000":
                    total_skipped += 1
                    continue

                spool = spool_by_rfid.get(tray_uuid.strip().upper())
                if not spool:
                    total_skipped += 1
                    continue

                if disable_weight_sync:
                    total_synced += 1
                    continue

                try:
                    remain = int(tray_data.get("remain", -1))
                    tray_weight = int(tray_data.get("tray_weight", 0))
                    if remain >= 0 and tray_weight > 0:
                        remaining_g = (remain / 100.0) * tray_weight
                        initial_weight = spool.get("initial_total_weight_g") or spool.get("label_weight") or 0
                        current_remaining = spool.get("remaining_weight_g") or initial_weight
                        delta = current_remaining - remaining_g
                        if delta > 0.5:
                            await client.report_consumption(spool["id"], round(delta, 2))
                    total_synced += 1
                except Exception as e:
                    all_errors.append(
                        f"{printer.name} AMS {ams_unit.get('id', '?')} tray {tray_data.get('id', '?')}: {e}"
                    )

    return SyncResult(
        success=len(all_errors) == 0,
        synced_count=total_synced,
        skipped_count=total_skipped,
        errors=all_errors,
    )


def _extract_ams_units(ams_data) -> list[dict]:
    """Extract AMS unit list from various Bambu printer AMS data formats."""
    if isinstance(ams_data, list):
        return ams_data
    if isinstance(ams_data, dict):
        if "ams" in ams_data and isinstance(ams_data["ams"], list):
            return ams_data["ams"]
        if "tray" in ams_data:
            return [{"id": 0, "tray": ams_data.get("tray", [])}]
    return []
