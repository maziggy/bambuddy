import io
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_auth_if_enabled, require_ownership_permission
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.filament import Filament
from backend.app.models.user import User
from backend.app.schemas.archive import ArchiveResponse, ArchiveStats, ArchiveUpdate, ReprintRequest
from backend.app.services.archive import ArchiveService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/archives", tags=["archives"])


def compute_time_accuracy(archive: PrintArchive) -> dict:
    """Compute actual print time and accuracy for an archive.

    Returns dict with actual_time_seconds and time_accuracy.
    time_accuracy = (estimated / actual) * 100
    - 100% = perfect estimate
    - >100% = print was faster than estimated
    - <100% = print took longer than estimated
    """
    result = {"actual_time_seconds": None, "time_accuracy": None}

    if archive.started_at and archive.completed_at and archive.status == "completed":
        actual_seconds = int((archive.completed_at - archive.started_at).total_seconds())
        if actual_seconds > 0:
            result["actual_time_seconds"] = actual_seconds

            if archive.print_time_seconds and archive.print_time_seconds > 0:
                # Calculate accuracy as percentage
                accuracy = (archive.print_time_seconds / actual_seconds) * 100
                # Sanity check: skip unreasonable values (e.g., manually changed status)
                # Valid range: 5% to 500% (print took 20x longer to 5x faster than estimated)
                if 5 <= accuracy <= 500:
                    result["time_accuracy"] = round(accuracy, 1)

    return result


def archive_to_response(
    archive: PrintArchive,
    duplicates: list[dict] | None = None,
    duplicate_count: int = 0,
) -> dict:
    """Convert archive model to response dict with computed fields."""
    data = {
        "id": archive.id,
        "printer_id": archive.printer_id,
        "project_id": archive.project_id,
        "project_name": archive.project.name if archive.project else None,
        "filename": archive.filename,
        "file_path": archive.file_path,
        "file_size": archive.file_size,
        "content_hash": archive.content_hash,
        "thumbnail_path": archive.thumbnail_path,
        "timelapse_path": archive.timelapse_path,
        "source_3mf_path": archive.source_3mf_path,
        "f3d_path": archive.f3d_path,
        "duplicates": duplicates,
        "duplicate_count": duplicate_count if duplicates is None else len(duplicates),
        "print_name": archive.print_name,
        "print_time_seconds": archive.print_time_seconds,
        "filament_used_grams": archive.filament_used_grams,
        "filament_type": archive.filament_type,
        "filament_color": archive.filament_color,
        "layer_height": archive.layer_height,
        "total_layers": archive.total_layers,
        "nozzle_diameter": archive.nozzle_diameter,
        "bed_temperature": archive.bed_temperature,
        "nozzle_temperature": archive.nozzle_temperature,
        "sliced_for_model": archive.sliced_for_model,
        "status": archive.status,
        "started_at": archive.started_at,
        "completed_at": archive.completed_at,
        "extra_data": archive.extra_data,
        "makerworld_url": archive.makerworld_url,
        "designer": archive.designer,
        "external_url": archive.external_url,
        "is_favorite": archive.is_favorite,
        "tags": archive.tags,
        "notes": archive.notes,
        "cost": archive.cost,
        "photos": archive.photos,
        "failure_reason": archive.failure_reason,
        "quantity": archive.quantity,
        "energy_kwh": archive.energy_kwh,
        "energy_cost": archive.energy_cost,
        "created_at": archive.created_at,
        # User tracking (Issue #206)
        "created_by_id": archive.created_by_id,
        "created_by_username": archive.created_by.username if archive.created_by else None,
    }

    # Add computed time accuracy fields
    accuracy_data = compute_time_accuracy(archive)
    data.update(accuracy_data)

    return data


@router.get("/", response_model=list[ArchiveResponse])
async def list_archives(
    printer_id: int | None = None,
    project_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List archived prints."""
    service = ArchiveService(db)
    archives = await service.list_archives(
        printer_id=printer_id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )

    # Get set of hashes that have duplicates (efficient single query)
    duplicate_hashes = await service.get_duplicate_hashes()

    # Mark archives that have duplicates
    result = []
    for a in archives:
        has_duplicate = a.content_hash in duplicate_hashes if a.content_hash else False
        result.append(archive_to_response(a, duplicate_count=1 if has_duplicate else 0))
    return result


@router.get("/search", response_model=list[ArchiveResponse])
async def search_archives(
    q: str = Query(..., min_length=2, description="Search query"),
    printer_id: int | None = None,
    project_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Full-text search across archives.

    Searches print_name, filename, tags, notes, designer, and filament_type fields.
    Supports partial matches with wildcards (e.g., 'vor*' matches 'voron').
    """
    from sqlalchemy import text
    from sqlalchemy.orm import selectinload

    # Prepare search query - add wildcard for partial matches
    search_term = q.strip()
    if not search_term.endswith("*"):
        search_term = f"{search_term}*"

    # Build the FTS query
    # Using MATCH for FTS5 full-text search
    fts_query = text("""
        SELECT rowid FROM archive_fts
        WHERE archive_fts MATCH :search_term
        ORDER BY rank
        LIMIT :limit OFFSET :offset
    """)

    try:
        result = await db.execute(fts_query, {"search_term": search_term, "limit": limit + 100, "offset": 0})
        matched_ids = [row[0] for row in result.fetchall()]
    except Exception as e:
        logger.warning(f"FTS search failed, falling back to LIKE search: {e}")
        # Fallback to LIKE search if FTS fails
        like_pattern = f"%{q}%"
        query = (
            select(PrintArchive)
            .options(selectinload(PrintArchive.project))
            .where(
                (PrintArchive.print_name.ilike(like_pattern))
                | (PrintArchive.filename.ilike(like_pattern))
                | (PrintArchive.tags.ilike(like_pattern))
                | (PrintArchive.notes.ilike(like_pattern))
                | (PrintArchive.designer.ilike(like_pattern))
                | (PrintArchive.filament_type.ilike(like_pattern))
            )
            .order_by(PrintArchive.created_at.desc())
        )

        if printer_id:
            query = query.where(PrintArchive.printer_id == printer_id)
        if project_id:
            query = query.where(PrintArchive.project_id == project_id)
        if status:
            query = query.where(PrintArchive.status == status)

        query = query.limit(limit).offset(offset)
        result = await db.execute(query)
        archives = result.scalars().all()
        return [archive_to_response(a) for a in archives]

    if not matched_ids:
        return []

    # Fetch full archive records for matched IDs
    query = select(PrintArchive).options(selectinload(PrintArchive.project)).where(PrintArchive.id.in_(matched_ids))

    # Apply additional filters
    if printer_id:
        query = query.where(PrintArchive.printer_id == printer_id)
    if project_id:
        query = query.where(PrintArchive.project_id == project_id)
    if status:
        query = query.where(PrintArchive.status == status)

    result = await db.execute(query)
    archives_dict = {a.id: a for a in result.scalars().all()}

    # Preserve FTS ranking order and apply pagination
    ordered_archives = [archives_dict[id] for id in matched_ids if id in archives_dict]
    paginated = ordered_archives[offset : offset + limit]

    return [archive_to_response(a) for a in paginated]


@router.post("/search/rebuild-index")
async def rebuild_search_index(db: AsyncSession = Depends(get_db)):
    """Rebuild the full-text search index from existing archives.

    Use this if search results seem incomplete or incorrect.
    """
    from sqlalchemy import text

    try:
        # Clear and rebuild the FTS index
        await db.execute(text("DELETE FROM archive_fts"))

        # Repopulate from print_archives
        await db.execute(
            text("""
            INSERT INTO archive_fts(rowid, print_name, filename, tags, notes, designer, filament_type)
            SELECT id, print_name, filename, tags, notes, designer, filament_type
            FROM print_archives
        """)
        )

        await db.commit()

        # Count entries
        result = await db.execute(text("SELECT COUNT(*) FROM archive_fts"))
        count = result.scalar() or 0

        return {"message": f"Search index rebuilt with {count} entries"}
    except Exception as e:
        logger.error(f"Failed to rebuild search index: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to rebuild index: {str(e)}")


@router.get("/analysis/failures")
async def analyze_failures(
    days: int = 30,
    printer_id: int | None = None,
    project_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Analyze failure patterns across prints.

    Returns failure statistics including:
    - Overall failure rate
    - Failures by reason, filament type, printer
    - Time of day distribution
    - Recent failures
    - Weekly trend
    """
    from backend.app.services.failure_analysis import FailureAnalysisService

    service = FailureAnalysisService(db)
    return await service.analyze_failures(
        days=days,
        printer_id=printer_id,
        project_id=project_id,
    )


@router.get("/compare")
async def compare_archives(
    archive_ids: str = Query(..., description="Comma-separated archive IDs (2-5)"),
    db: AsyncSession = Depends(get_db),
):
    """Compare multiple archives side by side.

    Compares print settings, filament usage, and print times.
    Also analyzes correlation between settings and success/failure.

    Args:
        archive_ids: Comma-separated list of 2-5 archive IDs to compare
    """
    from backend.app.services.archive_comparison import ArchiveComparisonService

    # Parse and validate archive IDs
    try:
        ids = [int(id.strip()) for id in archive_ids.split(",")]
    except ValueError:
        raise HTTPException(400, "Invalid archive IDs format")

    if len(ids) < 2:
        raise HTTPException(400, "At least 2 archives required for comparison")
    if len(ids) > 5:
        raise HTTPException(400, "Maximum 5 archives can be compared at once")

    service = ArchiveComparisonService(db)
    try:
        return await service.compare_archives(ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/export")
async def export_archives(
    format: str = Query("csv", description="Export format: csv or xlsx"),
    fields: str | None = Query(None, description="Comma-separated field names"),
    printer_id: int | None = None,
    project_id: int | None = None,
    status: str | None = None,
    date_from: str | None = Query(None, description="Start date (ISO format)"),
    date_to: str | None = Query(None, description="End date (ISO format)"),
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Export archives to CSV or Excel format.

    Returns a downloadable file with archive data.
    """
    from datetime import datetime

    from fastapi.responses import StreamingResponse

    from backend.app.services.export import ExportService

    if format not in ("csv", "xlsx"):
        raise HTTPException(400, "Format must be 'csv' or 'xlsx'")

    # Parse fields
    field_list = None
    if fields:
        field_list = [f.strip() for f in fields.split(",")]

    # Parse dates
    date_from_dt = None
    date_to_dt = None
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(400, "Invalid date_from format")
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(400, "Invalid date_to format")

    service = ExportService(db)
    try:
        file_bytes, filename, content_type = await service.export_archives(
            format=format,
            fields=field_list,
            printer_id=printer_id,
            project_id=project_id,
            status=status,
            date_from=date_from_dt,
            date_to=date_to_dt,
            search=search,
        )
    except ImportError as e:
        raise HTTPException(500, str(e))

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats/export")
async def export_stats(
    format: str = Query("csv", description="Export format: csv or xlsx"),
    days: int = 30,
    printer_id: int | None = None,
    project_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Export statistics summary to CSV or Excel format."""
    from fastapi.responses import StreamingResponse

    from backend.app.services.export import ExportService

    if format not in ("csv", "xlsx"):
        raise HTTPException(400, "Format must be 'csv' or 'xlsx'")

    service = ExportService(db)
    try:
        file_bytes, filename, content_type = await service.export_stats(
            format=format,
            days=days,
            printer_id=printer_id,
            project_id=project_id,
        )
    except ImportError as e:
        raise HTTPException(500, str(e))

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats", response_model=ArchiveStats)
async def get_archive_stats(db: AsyncSession = Depends(get_db)):
    """Get statistics across all archives."""
    # Total counts
    total_result = await db.execute(select(func.count(PrintArchive.id)))
    total_prints = total_result.scalar() or 0

    successful_result = await db.execute(select(func.count(PrintArchive.id)).where(PrintArchive.status == "completed"))
    successful_prints = successful_result.scalar() or 0

    failed_result = await db.execute(select(func.count(PrintArchive.id)).where(PrintArchive.status == "failed"))
    failed_prints = failed_result.scalar() or 0

    # Totals - use actual print time from timestamps (not slicer estimates)
    # For archives with both started_at and completed_at, calculate actual duration
    # Fall back to print_time_seconds only for archives without timestamps
    archives_for_time = await db.execute(
        select(PrintArchive.started_at, PrintArchive.completed_at, PrintArchive.print_time_seconds)
    )
    total_seconds = 0
    for started_at, completed_at, print_time_seconds in archives_for_time.all():
        if started_at and completed_at:
            # Use actual elapsed time
            actual_seconds = (completed_at - started_at).total_seconds()
            if actual_seconds > 0:
                total_seconds += actual_seconds
        elif print_time_seconds:
            # Fallback to estimate only if no timestamps
            total_seconds += print_time_seconds
    total_time = total_seconds / 3600  # Convert to hours

    filament_result = await db.execute(select(func.sum(PrintArchive.filament_used_grams)))
    total_filament = filament_result.scalar() or 0

    cost_result = await db.execute(select(func.sum(PrintArchive.cost)))
    total_cost = cost_result.scalar() or 0

    # By filament type (split comma-separated values for multi-material prints)
    filament_type_result = await db.execute(
        select(PrintArchive.filament_type).where(PrintArchive.filament_type.isnot(None))
    )
    prints_by_filament: dict[str, int] = {}
    for (filament_types,) in filament_type_result.all():
        # Split by comma and count each type
        for ftype in filament_types.split(","):
            ftype = ftype.strip()
            if ftype:
                prints_by_filament[ftype] = prints_by_filament.get(ftype, 0) + 1

    # By printer
    printer_result = await db.execute(
        select(PrintArchive.printer_id, func.count(PrintArchive.id)).group_by(PrintArchive.printer_id)
    )
    prints_by_printer = {str(k): v for k, v in printer_result.all()}

    # Time accuracy statistics
    # Get all completed archives with both estimated and actual times
    accuracy_result = await db.execute(
        select(PrintArchive)
        .where(PrintArchive.status == "completed")
        .where(PrintArchive.print_time_seconds.isnot(None))
        .where(PrintArchive.started_at.isnot(None))
        .where(PrintArchive.completed_at.isnot(None))
    )
    archives_with_times = list(accuracy_result.scalars().all())

    average_accuracy = None
    accuracy_by_printer: dict[str, float] = {}

    if archives_with_times:
        accuracies = []
        printer_accuracies: dict[str, list[float]] = {}

        for archive in archives_with_times:
            acc_data = compute_time_accuracy(archive)
            if acc_data["time_accuracy"] is not None:
                accuracies.append(acc_data["time_accuracy"])

                # Group by printer
                printer_key = str(archive.printer_id) if archive.printer_id else "unknown"
                if printer_key not in printer_accuracies:
                    printer_accuracies[printer_key] = []
                printer_accuracies[printer_key].append(acc_data["time_accuracy"])

        if accuracies:
            average_accuracy = round(sum(accuracies) / len(accuracies), 1)

        # Calculate per-printer averages
        for printer_key, accs in printer_accuracies.items():
            accuracy_by_printer[printer_key] = round(sum(accs) / len(accs), 1)

    # Energy totals - check which mode to use
    from backend.app.api.routes.settings import get_setting

    energy_tracking_mode = await get_setting(db, "energy_tracking_mode") or "total"
    energy_cost_per_kwh_str = await get_setting(db, "energy_cost_per_kwh")
    energy_cost_per_kwh = float(energy_cost_per_kwh_str) if energy_cost_per_kwh_str else 0.15

    if energy_tracking_mode == "total":
        # Total mode: sum up 'total' counter from all smart plugs (lifetime consumption)
        from backend.app.models.smart_plug import SmartPlug
        from backend.app.services.homeassistant import homeassistant_service
        from backend.app.services.mqtt_relay import mqtt_relay
        from backend.app.services.tasmota import tasmota_service

        plugs_result = await db.execute(select(SmartPlug))
        plugs = list(plugs_result.scalars().all())

        total_energy_kwh = 0.0
        for plug in plugs:
            if plug.plug_type == "tasmota":
                energy = await tasmota_service.get_energy(plug)
                if energy and energy.get("total") is not None:
                    total_energy_kwh += energy["total"]
            elif plug.plug_type == "homeassistant":
                energy = await homeassistant_service.get_energy(plug)
                if energy and energy.get("total") is not None:
                    total_energy_kwh += energy["total"]
            elif plug.plug_type == "mqtt":
                # MQTT plugs report "today" energy, not lifetime total
                mqtt_data = mqtt_relay.smart_plug_service.get_plug_data(plug.id)
                if mqtt_data and mqtt_data.energy is not None:
                    total_energy_kwh += mqtt_data.energy

        total_energy_kwh = round(total_energy_kwh, 3)
        total_energy_cost = round(total_energy_kwh * energy_cost_per_kwh, 2)
    else:
        # Print mode: sum up per-print energy from archives
        energy_kwh_result = await db.execute(select(func.sum(PrintArchive.energy_kwh)))
        total_energy_kwh = energy_kwh_result.scalar() or 0

        energy_cost_result = await db.execute(select(func.sum(PrintArchive.energy_cost)))
        total_energy_cost = energy_cost_result.scalar() or 0

    return ArchiveStats(
        total_prints=total_prints,
        successful_prints=successful_prints,
        failed_prints=failed_prints,
        total_print_time_hours=round(total_time, 1),
        total_filament_grams=round(total_filament, 1),
        total_cost=round(total_cost, 2),
        prints_by_filament_type=prints_by_filament,
        prints_by_printer=prints_by_printer,
        average_time_accuracy=average_accuracy,
        time_accuracy_by_printer=accuracy_by_printer if accuracy_by_printer else None,
        total_energy_kwh=round(total_energy_kwh, 3),
        total_energy_cost=round(total_energy_cost, 2),
    )


@router.get("/tags")
async def get_all_tags(db: AsyncSession = Depends(get_db)):
    """List all unique tags with usage counts.

    Returns a list of tags sorted by count (descending), then by name.
    """
    # Query all archives with non-null tags
    result = await db.execute(select(PrintArchive.tags).where(PrintArchive.tags.isnot(None)))
    all_tags_rows = result.all()

    # Count occurrences of each tag
    tag_counts: dict[str, int] = {}
    for (tags_str,) in all_tags_rows:
        if tags_str:
            for tag in tags_str.split(","):
                tag = tag.strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Convert to list and sort by count (desc), then name (asc)
    tags_list = [{"name": name, "count": count} for name, count in tag_counts.items()]
    tags_list.sort(key=lambda x: (-x["count"], x["name"].lower()))

    return tags_list


@router.put("/tags/{tag_name}")
async def rename_tag(
    tag_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Rename a tag across all archives.

    Request body should contain {"new_name": "new tag name"}.
    Returns the count of affected archives.
    """
    body = await request.json()
    new_name = body.get("new_name", "").strip()

    if not new_name:
        raise HTTPException(400, "new_name is required")

    if new_name == tag_name:
        return {"affected": 0}

    # Find all archives containing the old tag
    result = await db.execute(select(PrintArchive).where(PrintArchive.tags.isnot(None)))
    archives = list(result.scalars().all())

    affected = 0
    for archive in archives:
        if not archive.tags:
            continue
        tags = [t.strip() for t in archive.tags.split(",")]
        if tag_name in tags:
            # Replace old tag with new tag
            new_tags = [new_name if t == tag_name else t for t in tags]
            # Remove duplicates while preserving order
            seen = set()
            unique_tags = []
            for t in new_tags:
                if t not in seen:
                    seen.add(t)
                    unique_tags.append(t)
            archive.tags = ", ".join(unique_tags)
            affected += 1

    await db.commit()
    return {"affected": affected}


@router.delete("/tags/{tag_name}")
async def delete_tag(tag_name: str, db: AsyncSession = Depends(get_db)):
    """Delete a tag from all archives.

    Returns the count of affected archives.
    """
    # Find all archives containing the tag
    result = await db.execute(select(PrintArchive).where(PrintArchive.tags.isnot(None)))
    archives = list(result.scalars().all())

    affected = 0
    for archive in archives:
        if not archive.tags:
            continue
        tags = [t.strip() for t in archive.tags.split(",")]
        if tag_name in tags:
            # Remove the tag
            new_tags = [t for t in tags if t != tag_name]
            archive.tags = ", ".join(new_tags) if new_tags else None
            affected += 1

    await db.commit()
    return {"affected": affected}


@router.get("/{archive_id}", response_model=ArchiveResponse)
async def get_archive(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific archive."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Find duplicates
    makerworld_id = archive.extra_data.get("makerworld_model_id") if archive.extra_data else None
    duplicates = await service.find_duplicates(
        archive_id=archive.id,
        content_hash=archive.content_hash,
        print_name=archive.print_name,
        makerworld_model_id=makerworld_id,
    )
    return archive_to_response(archive, duplicates)


@router.get("/{archive_id}/similar")
async def find_similar_archives(
    archive_id: int,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Find archives with similar settings for comparison.

    Returns archives that match by:
    - Same print name (highest priority)
    - Same file content hash
    - Same filament type
    """
    from backend.app.services.archive_comparison import ArchiveComparisonService

    service = ArchiveComparisonService(db)
    try:
        return await service.find_similar_archives(archive_id, limit=limit)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.patch("/{archive_id}", response_model=ArchiveResponse)
async def update_archive(
    archive_id: int,
    update_data: ArchiveUpdate,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.ARCHIVES_UPDATE_ALL,
            Permission.ARCHIVES_UPDATE_OWN,
        )
    ),
):
    """Update archive metadata (tags, notes, cost, is_favorite, project_id)."""
    from sqlalchemy.orm import selectinload

    user, can_modify_all = auth_result

    result = await db.execute(
        select(PrintArchive)
        .options(selectinload(PrintArchive.project), selectinload(PrintArchive.created_by))
        .where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Ownership check
    if not can_modify_all:
        if archive.created_by_id != user.id:
            raise HTTPException(403, "You can only update your own archives")

    for field, value in update_data.model_dump(exclude_unset=True).items():
        setattr(archive, field, value)

    await db.commit()

    # Re-fetch with relationships loaded after commit
    result = await db.execute(
        select(PrintArchive)
        .options(selectinload(PrintArchive.project), selectinload(PrintArchive.created_by))
        .where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()

    return archive_to_response(archive)


@router.post("/{archive_id}/favorite", response_model=ArchiveResponse)
async def toggle_favorite(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Toggle favorite status for an archive."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    archive.is_favorite = not archive.is_favorite
    await db.commit()
    await db.refresh(archive)
    return archive


@router.post("/{archive_id}/rescan", response_model=ArchiveResponse)
async def rescan_archive(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Rescan the 3MF file and update metadata."""
    from backend.app.api.routes.settings import get_setting
    from backend.app.services.archive import ThreeMFParser

    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    # Parse the 3MF file
    parser = ThreeMFParser(file_path)
    metadata = parser.parse()

    # Update fields from metadata
    if metadata.get("filament_type"):
        archive.filament_type = metadata["filament_type"]
    if metadata.get("filament_color"):
        archive.filament_color = metadata["filament_color"]
    if metadata.get("print_time_seconds"):
        archive.print_time_seconds = metadata["print_time_seconds"]
    if metadata.get("filament_used_grams"):
        archive.filament_used_grams = metadata["filament_used_grams"]
    if metadata.get("layer_height"):
        archive.layer_height = metadata["layer_height"]
    if metadata.get("nozzle_diameter"):
        archive.nozzle_diameter = metadata["nozzle_diameter"]
    if metadata.get("bed_temperature"):
        archive.bed_temperature = metadata["bed_temperature"]
    if metadata.get("nozzle_temperature"):
        archive.nozzle_temperature = metadata["nozzle_temperature"]
    if metadata.get("makerworld_url"):
        archive.makerworld_url = metadata["makerworld_url"]
    if metadata.get("designer"):
        archive.designer = metadata["designer"]

    # Calculate cost based on filament usage and type
    if archive.filament_used_grams and archive.filament_type:
        primary_type = archive.filament_type.split(",")[0].strip()
        filament_result = await db.execute(select(Filament).where(Filament.type == primary_type).limit(1))
        filament = filament_result.scalar_one_or_none()
        if filament:
            archive.cost = round((archive.filament_used_grams / 1000) * filament.cost_per_kg, 2)
        else:
            # Use default filament cost from settings
            default_cost_setting = await get_setting(db, "default_filament_cost")
            default_cost_per_kg = float(default_cost_setting) if default_cost_setting else 25.0
            archive.cost = round((archive.filament_used_grams / 1000) * default_cost_per_kg, 2)

    await db.commit()
    await db.refresh(archive)
    return archive


@router.post("/recalculate-costs")
async def recalculate_all_costs(db: AsyncSession = Depends(get_db)):
    """Recalculate costs for all archives based on filament usage and prices."""
    from backend.app.api.routes.settings import get_setting

    result = await db.execute(select(PrintArchive))
    archives = list(result.scalars().all())

    # Load all filaments for lookup
    filament_result = await db.execute(select(Filament))
    filaments = {f.type: f.cost_per_kg for f in filament_result.scalars().all()}

    # Get default filament cost from settings
    default_cost_setting = await get_setting(db, "default_filament_cost")
    default_cost_per_kg = float(default_cost_setting) if default_cost_setting else 25.0

    updated = 0
    for archive in archives:
        if archive.filament_used_grams and archive.filament_type:
            primary_type = archive.filament_type.split(",")[0].strip()
            cost_per_kg = filaments.get(primary_type, default_cost_per_kg)
            new_cost = round((archive.filament_used_grams / 1000) * cost_per_kg, 2)
            if archive.cost != new_cost:
                archive.cost = new_cost
                updated += 1

    await db.commit()
    return {"message": f"Recalculated costs for {updated} archives", "updated": updated}


@router.post("/rescan-all")
async def rescan_all_archives(db: AsyncSession = Depends(get_db)):
    """Rescan all archives and update their metadata."""
    from backend.app.services.archive import ThreeMFParser

    result = await db.execute(select(PrintArchive))
    archives = list(result.scalars().all())

    updated = 0
    errors = []

    for archive in archives:
        try:
            file_path = settings.base_dir / archive.file_path
            if not file_path.exists():
                errors.append({"id": archive.id, "error": "File not found"})
                continue

            parser = ThreeMFParser(file_path)
            metadata = parser.parse()

            if metadata.get("filament_type"):
                archive.filament_type = metadata["filament_type"]
            if metadata.get("filament_color"):
                archive.filament_color = metadata["filament_color"]
            if metadata.get("print_time_seconds"):
                archive.print_time_seconds = metadata["print_time_seconds"]
            if metadata.get("filament_used_grams"):
                archive.filament_used_grams = metadata["filament_used_grams"]
            if metadata.get("layer_height"):
                archive.layer_height = metadata["layer_height"]
            if metadata.get("nozzle_diameter"):
                archive.nozzle_diameter = metadata["nozzle_diameter"]
            if metadata.get("makerworld_url"):
                archive.makerworld_url = metadata["makerworld_url"]
            if metadata.get("designer"):
                archive.designer = metadata["designer"]

            updated += 1
        except Exception as e:
            errors.append({"id": archive.id, "error": str(e)})

    await db.commit()
    return {"updated": updated, "errors": errors}


@router.get("/{archive_id}/duplicates")
async def get_archive_duplicates(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get duplicates for a specific archive."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    makerworld_id = archive.extra_data.get("makerworld_model_id") if archive.extra_data else None
    duplicates = await service.find_duplicates(
        archive_id=archive.id,
        content_hash=archive.content_hash,
        print_name=archive.print_name,
        makerworld_model_id=makerworld_id,
    )
    return {"duplicates": duplicates, "count": len(duplicates)}


@router.post("/backfill-hashes")
async def backfill_content_hashes(db: AsyncSession = Depends(get_db)):
    """Compute and store content hashes for all archives missing them."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.content_hash.is_(None)))
    archives = list(result.scalars().all())

    updated = 0
    errors = []

    for archive in archives:
        try:
            file_path = settings.base_dir / archive.file_path
            if not file_path.exists():
                errors.append({"id": archive.id, "error": "File not found"})
                continue

            archive.content_hash = ArchiveService.compute_file_hash(file_path)
            updated += 1
        except Exception as e:
            errors.append({"id": archive.id, "error": str(e)})

    await db.commit()
    return {"updated": updated, "errors": errors}


@router.delete("/{archive_id}")
async def delete_archive(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.ARCHIVES_DELETE_ALL,
            Permission.ARCHIVES_DELETE_OWN,
        )
    ),
):
    """Delete an archive."""
    user, can_modify_all = auth_result

    # Get archive first to check ownership
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Ownership check
    if not can_modify_all:
        if archive.created_by_id != user.id:
            raise HTTPException(403, "You can only delete your own archives")

    service = ArchiveService(db)
    if not await service.delete_archive(archive_id):
        raise HTTPException(404, "Archive not found")
    return {"status": "deleted"}


@router.get("/{archive_id}/download")
async def download_archive(
    archive_id: int,
    inline: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Download the 3MF file."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    # Use inline disposition to let browser/OS handle file association
    content_disposition = "inline" if inline else "attachment"

    return FileResponse(
        path=file_path,
        filename=archive.filename,
        media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
        content_disposition_type=content_disposition,
    )


@router.get("/{archive_id}/file/{filename}")
async def download_archive_with_filename(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Download the 3MF file with filename in URL (for Bambu Studio protocol)."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(
        path=file_path,
        filename=archive.filename,
        media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
    )


@router.get("/{archive_id}/thumbnail")
async def get_thumbnail(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get the thumbnail image."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive or not archive.thumbnail_path:
        raise HTTPException(404, "Thumbnail not found")

    thumb_path = settings.base_dir / archive.thumbnail_path
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail file not found")

    # Use file modification time as ETag to bust cache
    mtime = int(thumb_path.stat().st_mtime)

    return FileResponse(
        path=thumb_path,
        media_type="image/png",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "ETag": f'"{mtime}"',
        },
    )


@router.get("/{archive_id}/timelapse")
async def get_timelapse(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get the timelapse video."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive or not archive.timelapse_path:
        raise HTTPException(404, "Timelapse not found")

    timelapse_path = settings.base_dir / archive.timelapse_path
    if not timelapse_path.exists():
        raise HTTPException(404, "Timelapse file not found")

    # Use file modification time as ETag to bust cache after processing
    mtime = int(timelapse_path.stat().st_mtime)

    return FileResponse(
        path=timelapse_path,
        media_type="video/mp4",
        filename=f"{archive.print_name or 'timelapse'}.mp4",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "ETag": f'"{mtime}"',
        },
    )


@router.post("/{archive_id}/timelapse/scan")
async def scan_timelapse(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Scan printer for timelapse matching this archive and attach it."""
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import (
        download_file_bytes_async,
        get_ftp_retry_settings,
        list_files_async,
        with_ftp_retry,
    )

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    if archive.timelapse_path:
        return {"status": "exists", "message": "Timelapse already attached"}

    if not archive.printer_id:
        raise HTTPException(400, "Archive has no associated printer")

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Get base name from archive filename (without .3mf extension)
    base_name = Path(archive.filename).stem

    # Scan timelapse directory on printer
    # Try both /timelapse and /timelapse/video (different printer models use different paths)
    files = []
    for timelapse_path in ["/timelapse", "/timelapse/video"]:
        try:
            files = await list_files_async(printer.ip_address, printer.access_code, timelapse_path)
            if files:
                break
        except Exception:
            continue
    if not files:
        raise HTTPException(500, "Failed to connect to printer or no timelapse directory found")

    # Look for matching timelapse
    matching_file = None
    mp4_files = [f for f in files if not f.get("is_directory") and f.get("name", "").endswith(".mp4")]

    # Strategy 1: Match by print name in filename
    for f in mp4_files:
        fname = f.get("name", "")
        if base_name.lower() in fname.lower():
            matching_file = f
            break

    # Strategy 2: Match by timestamp proximity
    # Bambu timelapse filename uses the print START time (when recording began)
    if not matching_file and (archive.started_at or archive.completed_at or archive.created_at):
        import re
        from datetime import datetime, timedelta

        # Prefer started_at since video filename is the print start time
        # Fall back to completed_at or created_at if started_at is not available
        archive_start = archive.started_at
        archive_end = archive.completed_at or archive.created_at
        best_match = None
        best_diff = timedelta(hours=24)  # Max 24 hour difference

        for f in mp4_files:
            fname = f.get("name", "")
            # Parse timestamp from filename like "video_2025-11-24_03-17-40.mp4"
            match = re.search(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", fname)
            if match:
                try:
                    file_time = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")

                    # Try multiple timezone offsets since printer timezone can vary
                    # Common cases: local time (0), CST/UTC+8 (+8), or UTC (-local offset)
                    for hour_offset in [0, 8, -8, 7, -7, 1, -1]:
                        adjusted_file_time = file_time - timedelta(hours=hour_offset)

                        # Check against start time (video filename = print start)
                        if archive_start:
                            diff = abs(adjusted_file_time - archive_start)
                            if diff < best_diff:
                                best_diff = diff
                                best_match = f
                                logger.debug(
                                    f"Timelapse match candidate: {fname} with offset {hour_offset}h, "
                                    f"diff from start: {diff}"
                                )

                        # Also check against end time with a buffer
                        # (video timestamp should be BEFORE completion time)
                        if archive_end:
                            # The video timestamp should be within the print duration before completion
                            if adjusted_file_time < archive_end:
                                diff = archive_end - adjusted_file_time
                                # Reasonable print duration: up to 48 hours
                                if diff < timedelta(hours=48) and diff < best_diff:
                                    best_diff = diff
                                    best_match = f
                                    logger.debug(
                                        f"Timelapse match candidate (from end): {fname} with offset {hour_offset}h, "
                                        f"diff: {diff}"
                                    )

                except ValueError:
                    continue

        # Accept match within 4 hours (more lenient for timezone issues)
        if best_match and best_diff < timedelta(hours=4):
            matching_file = best_match
            logger.info(f"Matched timelapse by timestamp: {best_match.get('name')} (diff: {best_diff})")

    # Strategy 3: Use file modification time from FTP listing
    # This handles cases where printer's filename timestamp is wrong but file mtime is correct
    if not matching_file and (archive.started_at or archive.completed_at or archive.created_at):
        from datetime import datetime, timedelta

        archive_start = archive.started_at
        archive_end = archive.completed_at or archive.created_at
        best_match = None
        best_diff = timedelta(hours=24)

        for f in mp4_files:
            mtime = f.get("mtime")
            if mtime:
                # Timelapse file should be modified during or shortly after the print
                # The mtime should be close to completion time (video finishes when print ends)
                if archive_end:
                    diff = abs(mtime - archive_end)
                    if diff < best_diff:
                        best_diff = diff
                        best_match = f
                        logger.debug(
                            f"Timelapse mtime match candidate: {f.get('name')}, mtime: {mtime}, diff from end: {diff}"
                        )

        if best_match and best_diff < timedelta(hours=2):
            matching_file = best_match
            logger.info(f"Matched timelapse by file mtime: {best_match.get('name')} (diff: {best_diff})")

    # Strategy 4: If only one timelapse exists and archive was recently completed, use it
    # This handles cases where printer clock is wrong or timezone issues exist
    if not matching_file and len(mp4_files) == 1:
        from datetime import datetime, timedelta

        archive_completed = archive.completed_at or archive.created_at
        if archive_completed:
            time_since_completion = datetime.now() - archive_completed
            # If archive was completed within the last hour, assume the single timelapse is for it
            if time_since_completion < timedelta(hours=1):
                matching_file = mp4_files[0]
                logger.info(f"Using single timelapse file as fallback: {mp4_files[0].get('name')}")

    # Note: We intentionally don't use a "most recent file" fallback because
    # we can't verify if timelapse was actually enabled for this print.
    # Instead, return the list of available files for manual selection.

    if not matching_file:
        # Return available files for manual selection
        available_files = [
            {
                "name": f.get("name"),
                "path": f.get("path"),
                "size": f.get("size"),
                "mtime": f.get("mtime").isoformat() if f.get("mtime") else None,
            }
            for f in mp4_files
        ]
        # Sort by mtime descending (most recent first)
        available_files.sort(key=lambda x: x.get("mtime") or "", reverse=True)
        return {
            "status": "not_found",
            "message": "No matching timelapse found - please select manually",
            "available_files": available_files,
        }

    # Download the timelapse - use the full path from the file listing
    remote_path = matching_file.get("path") or f"/timelapse/{matching_file['name']}"

    # Get FTP retry settings
    ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

    if ftp_retry_enabled:
        timelapse_data = await with_ftp_retry(
            download_file_bytes_async,
            printer.ip_address,
            printer.access_code,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
            max_retries=ftp_retry_count,
            retry_delay=ftp_retry_delay,
            operation_name=f"Download timelapse {matching_file['name']}",
        )
    else:
        timelapse_data = await download_file_bytes_async(
            printer.ip_address,
            printer.access_code,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
        )

    if not timelapse_data:
        raise HTTPException(500, "Failed to download timelapse")

    # Attach timelapse to archive
    success = await service.attach_timelapse(archive_id, timelapse_data, matching_file["name"])

    if not success:
        raise HTTPException(500, "Failed to attach timelapse")

    return {
        "status": "attached",
        "message": f"Timelapse '{matching_file['name']}' attached successfully",
        "filename": matching_file["name"],
    }


@router.post("/{archive_id}/timelapse/select")
async def select_timelapse(
    archive_id: int,
    filename: str = Query(..., description="Timelapse filename to attach"),
    db: AsyncSession = Depends(get_db),
):
    """Manually select a timelapse from the printer to attach."""
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import (
        download_file_bytes_async,
        get_ftp_retry_settings,
        list_files_async,
        with_ftp_retry,
    )

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.printer_id:
        raise HTTPException(400, "Archive has no associated printer")

    result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Find the file on the printer
    files = []
    remote_path = None
    for timelapse_dir in ["/timelapse", "/timelapse/video"]:
        try:
            files = await list_files_async(printer.ip_address, printer.access_code, timelapse_dir)
            for f in files:
                if f.get("name") == filename:
                    remote_path = f.get("path") or f"{timelapse_dir}/{filename}"
                    break
            if remote_path:
                break
        except Exception:
            continue

    if not remote_path:
        raise HTTPException(404, f"Timelapse '{filename}' not found on printer")

    # Download and attach
    ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

    if ftp_retry_enabled:
        timelapse_data = await with_ftp_retry(
            download_file_bytes_async,
            printer.ip_address,
            printer.access_code,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
            max_retries=ftp_retry_count,
            retry_delay=ftp_retry_delay,
            operation_name=f"Download timelapse {filename}",
        )
    else:
        timelapse_data = await download_file_bytes_async(
            printer.ip_address,
            printer.access_code,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
        )

    if not timelapse_data:
        raise HTTPException(500, "Failed to download timelapse")

    success = await service.attach_timelapse(archive_id, timelapse_data, filename)
    if not success:
        raise HTTPException(500, "Failed to attach timelapse")

    return {
        "status": "attached",
        "message": f"Timelapse '{filename}' attached successfully",
        "filename": filename,
    }


@router.post("/{archive_id}/timelapse/upload")
async def upload_timelapse(
    archive_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Manually upload a timelapse video to an archive."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not file.filename or not file.filename.endswith((".mp4", ".avi", ".mkv")):
        raise HTTPException(400, "File must be a video file (.mp4, .avi, .mkv)")

    content = await file.read()
    success = await service.attach_timelapse(archive_id, content, file.filename)

    if not success:
        raise HTTPException(500, "Failed to attach timelapse")

    return {"status": "attached", "filename": file.filename}


@router.get("/{archive_id}/timelapse/info")
async def get_timelapse_info(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get timelapse video metadata for editor."""
    from backend.app.schemas.timelapse import TimelapseInfoResponse
    from backend.app.services.timelapse_processor import TimelapseProcessor

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive or not archive.timelapse_path:
        raise HTTPException(404, "Timelapse not found")

    timelapse_path = settings.base_dir / archive.timelapse_path
    if not timelapse_path.exists():
        raise HTTPException(404, "Timelapse file not found")

    try:
        processor = TimelapseProcessor(timelapse_path)
        info = await processor.get_info()
        return TimelapseInfoResponse(**info)
    except Exception as e:
        logger.error(f"Failed to get timelapse info: {e}")
        raise HTTPException(500, f"Failed to get video info: {str(e)}")


@router.get("/{archive_id}/timelapse/thumbnails")
async def get_timelapse_thumbnails(
    archive_id: int,
    count: int = Query(10, ge=1, le=30),
    width: int = Query(160, ge=80, le=320),
    db: AsyncSession = Depends(get_db),
):
    """Generate timeline thumbnail frames for visual scrubbing."""
    import base64

    from backend.app.schemas.timelapse import ThumbnailResponse
    from backend.app.services.timelapse_processor import TimelapseProcessor

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive or not archive.timelapse_path:
        raise HTTPException(404, "Timelapse not found")

    timelapse_path = settings.base_dir / archive.timelapse_path
    if not timelapse_path.exists():
        raise HTTPException(404, "Timelapse file not found")

    try:
        processor = TimelapseProcessor(timelapse_path)
        thumbnails = await processor.generate_thumbnails(count, width)

        return ThumbnailResponse(
            thumbnails=[base64.b64encode(data).decode() for _, data in thumbnails],
            timestamps=[ts for ts, _ in thumbnails],
        )
    except Exception as e:
        logger.error(f"Failed to generate thumbnails: {e}")
        raise HTTPException(500, f"Failed to generate thumbnails: {str(e)}")


@router.post("/{archive_id}/timelapse/process")
async def process_timelapse(
    archive_id: int,
    trim_start: float = Form(0),
    trim_end: float = Form(None),
    speed: float = Form(1.0),
    save_mode: str = Form("new"),
    output_filename: str = Form(None),
    audio: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Process timelapse with trim, speed, and optional audio overlay."""
    import shutil
    import tempfile

    from backend.app.schemas.timelapse import ProcessResponse
    from backend.app.services.timelapse_processor import TimelapseProcessor

    # Validate speed
    if not 0.25 <= speed <= 4.0:
        raise HTTPException(400, "Speed must be between 0.25 and 4.0")

    if save_mode not in ("replace", "new"):
        raise HTTPException(400, "save_mode must be 'replace' or 'new'")

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive or not archive.timelapse_path:
        raise HTTPException(404, "Timelapse not found")

    timelapse_path = settings.base_dir / archive.timelapse_path
    if not timelapse_path.exists():
        raise HTTPException(404, "Timelapse file not found")

    archive_dir = timelapse_path.parent

    # Handle audio file
    audio_temp_path = None
    if audio and audio.filename:
        # Validate audio file extension
        if not audio.filename.lower().endswith((".mp3", ".wav", ".m4a", ".aac", ".ogg")):
            raise HTTPException(400, "Audio must be .mp3, .wav, .m4a, .aac, or .ogg")

        audio_content = await audio.read()
        suffix = Path(audio.filename).suffix
        audio_temp_path = Path(tempfile.gettempdir()) / f"audio_{archive_id}{suffix}"
        audio_temp_path.write_bytes(audio_content)

    try:
        processor = TimelapseProcessor(timelapse_path)

        # Determine output path
        if save_mode == "replace":
            # Process to temp file first, then replace
            temp_output = Path(tempfile.gettempdir()) / f"processed_{archive_id}.mp4"
            output_path = temp_output
        else:
            # Save as new file alongside original
            filename = output_filename or f"{archive.print_name or 'timelapse'}_edited.mp4"
            # Sanitize filename
            filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
            if not filename.endswith(".mp4"):
                filename += ".mp4"
            output_path = archive_dir / filename

        success = await processor.process(
            output_path=output_path,
            trim_start=trim_start,
            trim_end=trim_end,
            speed=speed,
            audio_path=audio_temp_path,
        )

        if not success:
            raise HTTPException(500, "Video processing failed")

        # Handle save mode
        if save_mode == "replace":
            # Replace original file
            shutil.move(str(output_path), str(timelapse_path))
            final_path = archive.timelapse_path
            message = "Timelapse replaced successfully"
        else:
            final_path = str(output_path.relative_to(settings.base_dir))
            message = f"Saved as {output_path.name}"

        return ProcessResponse(
            status="completed",
            output_path=final_path,
            message=message,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Timelapse processing failed: {e}")
        raise HTTPException(500, f"Processing failed: {str(e)}")
    finally:
        # Cleanup temp audio file
        if audio_temp_path and audio_temp_path.exists():
            audio_temp_path.unlink()


# ============================================
# Photo Endpoints
# ============================================


@router.post("/{archive_id}/photos")
async def upload_photo(
    archive_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a photo of the printed result."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not file.filename or not file.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise HTTPException(400, "File must be an image (.jpg, .jpeg, .png, .webp)")

    # Get archive directory
    file_path = settings.base_dir / archive.file_path
    archive_dir = file_path.parent
    photos_dir = archive_dir / "photos"
    photos_dir.mkdir(exist_ok=True)

    # Generate unique filename
    import uuid

    ext = Path(file.filename).suffix.lower()
    photo_filename = f"{uuid.uuid4().hex[:8]}{ext}"
    photo_path = photos_dir / photo_filename

    # Save file
    content = await file.read()
    photo_path.write_bytes(content)

    # Update archive photos list (create new list to trigger SQLAlchemy change detection)
    photos = list(archive.photos or [])
    photos.append(photo_filename)
    archive.photos = photos

    await db.commit()
    await db.refresh(archive)

    return {"status": "uploaded", "filename": photo_filename, "photos": archive.photos}


@router.get("/{archive_id}/photos/{filename}")
async def get_photo(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific photo."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    photo_path = file_path.parent / "photos" / filename

    if not photo_path.exists():
        raise HTTPException(404, "Photo not found")

    # Determine media type
    ext = Path(filename).suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    media_type = media_types.get(ext, "image/jpeg")

    return FileResponse(path=photo_path, media_type=media_type)


@router.delete("/{archive_id}/photos/{filename}")
async def delete_photo(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a photo."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.photos or filename not in archive.photos:
        raise HTTPException(404, "Photo not found")

    # Delete file
    file_path = settings.base_dir / archive.file_path
    photo_path = file_path.parent / "photos" / filename
    if photo_path.exists():
        photo_path.unlink()

    # Update archive photos list
    photos = [p for p in archive.photos if p != filename]
    archive.photos = photos if photos else None

    await db.commit()

    return {"status": "deleted", "photos": archive.photos}


# ============================================
# QR Code Endpoint
# ============================================


@router.get("/{archive_id}/qrcode")
async def get_qrcode(
    archive_id: int,
    request: Request,
    size: int = 200,
    db: AsyncSession = Depends(get_db),
):
    """Generate a QR code that links to this archive."""
    try:
        import qrcode
        from PIL import Image as PILImage
    except ImportError:
        raise HTTPException(500, "QR code generation not available - qrcode package not installed")

    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Build URL to archive download
    base_url = str(request.base_url).rstrip("/")
    archive_url = f"{base_url}/api/v1/archives/{archive_id}/download"

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(archive_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to PIL Image for resizing
    pil_img = img.get_image()

    # Resize if needed
    if size != 200:
        pil_img = pil_img.resize((size, size), PILImage.Resampling.LANCZOS)

    # Convert to bytes
    buffer = io.BytesIO()
    pil_img.save(buffer, format="PNG")
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="qr_{archive.print_name or archive_id}.png"'},
    )


@router.get("/{archive_id}/capabilities")
async def get_archive_capabilities(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Check what viewing capabilities are available for this 3MF file."""
    import json
    import xml.etree.ElementTree as ET

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    has_model = False
    has_gcode = False
    has_source = False
    build_volume = {"x": 256, "y": 256, "z": 256}  # Default to X1/P1 size
    filament_colors: list[str] = []

    # Check if source 3MF exists - this is where actual mesh data typically lives
    source_path = None
    if archive.source_3mf_path:
        source_path = settings.base_dir / archive.source_3mf_path
        if source_path.exists():
            has_source = True

    # Helper function to check for mesh data and extract colors from a 3MF file
    def extract_3mf_info(zf_path: Path) -> tuple[bool, list[str], dict]:
        """Extract mesh presence, colors, and build volume from a 3MF file."""
        found_mesh = False
        colors: list[str] = []
        volume = {"x": 256, "y": 256, "z": 256}

        try:
            with zipfile.ZipFile(zf_path, "r") as zf:
                names = zf.namelist()

                # Check for 3D model - look for actual mesh data
                for name in names:
                    if name.endswith(".model"):
                        try:
                            content = zf.read(name).decode("utf-8")
                            if "<vertex" in content or "<mesh" in content:
                                found_mesh = True
                                break
                        except Exception:
                            pass

                # Extract filament colors from project_settings.config
                if "Metadata/project_settings.config" in names:
                    try:
                        config_content = zf.read("Metadata/project_settings.config").decode("utf-8")
                        config_data = json.loads(config_content)

                        # Parse printable_area: ['0x0', '256x0', '256x256', '0x256']
                        printable_area = config_data.get("printable_area", [])
                        if printable_area and len(printable_area) >= 3:
                            max_x = 0
                            max_y = 0
                            for coord in printable_area:
                                if "x" in coord:
                                    parts = coord.split("x")
                                    if len(parts) == 2:
                                        try:
                                            x, y = int(parts[0]), int(parts[1])
                                            max_x = max(max_x, x)
                                            max_y = max(max_y, y)
                                        except ValueError:
                                            pass
                            if max_x > 0 and max_y > 0:
                                volume["x"] = max_x
                                volume["y"] = max_y

                        # Parse printable_height
                        printable_height = config_data.get("printable_height")
                        if printable_height:
                            try:
                                volume["z"] = int(printable_height)
                            except (ValueError, TypeError):
                                pass

                        # Extract filament colors
                        raw_colors = config_data.get("filament_colour", [])
                        if raw_colors:
                            for color in raw_colors:
                                if color and isinstance(color, str):
                                    colors.append(color)
                    except Exception:
                        pass
        except zipfile.BadZipFile:
            pass

        return found_mesh, colors, volume

    # First check source 3MF for mesh data and colors (preferred for 3D model viewing)
    if has_source and source_path:
        source_has_mesh, source_colors, source_volume = extract_3mf_info(source_path)
        if source_has_mesh:
            has_model = True
        if source_colors:
            filament_colors = source_colors
        if source_volume["x"] != 256 or source_volume["y"] != 256 or source_volume["z"] != 256:
            build_volume = source_volume

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            names = zf.namelist()

            # Check for G-code in the sliced file
            has_gcode = any(n.startswith("Metadata/") and n.endswith(".gcode") for n in names)

            # Check for 3D model in sliced file (fallback if no source)
            if not has_model:
                for name in names:
                    if name.endswith(".model"):
                        try:
                            content = zf.read(name).decode("utf-8")
                            if "<vertex" in content or "<mesh" in content:
                                has_model = True
                                break
                        except Exception:
                            pass

            # Extract filament colors from slice_info.config (for gcode preview)
            # These are the actual filaments used in the print, indexed by tool/extruder
            slice_colors: list[str] = []
            if "Metadata/slice_info.config" in names:
                try:
                    slice_content = zf.read("Metadata/slice_info.config").decode("utf-8")
                    root = ET.fromstring(slice_content)

                    filaments = root.findall(".//filament")
                    filament_map: dict[int, str] = {}
                    for f in filaments:
                        fid = f.get("id")
                        fcolor = f.get("color")
                        used_g = f.get("used_g", "0")
                        try:
                            used_amount = float(used_g)
                        except (ValueError, TypeError):
                            used_amount = 0

                        if fid is not None and fcolor:
                            try:
                                tool_id = int(fid) - 1
                                if tool_id >= 0 and used_amount > 0:
                                    filament_map[tool_id] = fcolor
                            except ValueError:
                                pass

                    if filament_map:
                        max_tool = max(filament_map.keys())
                        for i in range(max_tool + 1):
                            slice_colors.append(filament_map.get(i, "#00AE42"))
                except Exception:
                    pass

            # Use slice_info colors if we don't have colors from source yet
            if not filament_colors and slice_colors:
                filament_colors = slice_colors

            # Extract build volume from sliced file if not already set from source
            if build_volume["x"] == 256 and build_volume["y"] == 256:
                if "Metadata/project_settings.config" in names:
                    try:
                        config_content = zf.read("Metadata/project_settings.config").decode("utf-8")
                        config_data = json.loads(config_content)

                        printable_area = config_data.get("printable_area", [])
                        if printable_area and len(printable_area) >= 3:
                            max_x = 0
                            max_y = 0
                            for coord in printable_area:
                                if "x" in coord:
                                    parts = coord.split("x")
                                    if len(parts) == 2:
                                        try:
                                            x, y = int(parts[0]), int(parts[1])
                                            max_x = max(max_x, x)
                                            max_y = max(max_y, y)
                                        except ValueError:
                                            pass
                            if max_x > 0 and max_y > 0:
                                build_volume["x"] = max_x
                                build_volume["y"] = max_y

                        printable_height = config_data.get("printable_height")
                        if printable_height:
                            try:
                                build_volume["z"] = int(printable_height)
                            except (ValueError, TypeError):
                                pass

                        # Fallback colors from project_settings if still empty
                        if not filament_colors:
                            raw_colors = config_data.get("filament_colour", [])
                            if raw_colors:
                                for color in raw_colors:
                                    if color and isinstance(color, str):
                                        filament_colors.append(color)
                    except Exception:
                        pass

    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid 3MF file")

    return {
        "has_model": has_model,
        "has_gcode": has_gcode,
        "has_source": has_source,
        "build_volume": build_volume,
        "filament_colors": filament_colors,
    }


@router.get("/{archive_id}/gcode")
async def get_gcode(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Extract and return G-code from the 3MF file."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # Bambu 3MF files store G-code in Metadata/plate_X.gcode
            gcode_files = [n for n in zf.namelist() if n.startswith("Metadata/") and n.endswith(".gcode")]
            if not gcode_files:
                raise HTTPException(
                    404,
                    "No G-code found. This file hasn't been sliced yet - G-code is only available after slicing in Bambu Studio.",
                )

            # Get the first plate's G-code (usually plate_1.gcode)
            gcode_content = zf.read(gcode_files[0]).decode("utf-8")
            return Response(content=gcode_content, media_type="text/plain")
    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid 3MF file")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error extracting G-code: {str(e)}")


@router.get("/{archive_id}/plate-preview")
async def get_plate_preview(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get the plate preview image from the 3MF file.

    Returns the slicer-generated plate thumbnail which shows the model
    with correct colors and positioning.
    """
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            names = zf.namelist()

            # Try to find plate preview images in order of preference
            # First look for the specific plate being printed (check slice_info for plate index)
            plate_num = 1
            if "Metadata/slice_info.config" in names:
                try:
                    import xml.etree.ElementTree as ET

                    slice_content = zf.read("Metadata/slice_info.config").decode("utf-8")
                    root = ET.fromstring(slice_content)
                    plate_elem = root.find(".//plate/metadata[@key='index']")
                    if plate_elem is not None:
                        plate_num = int(plate_elem.get("value", "1"))
                except Exception:
                    pass

            # Try plate-specific image first, then fall back to plate_1
            preview_paths = [
                f"Metadata/plate_{plate_num}.png",
                "Metadata/plate_1.png",
                "Metadata/thumbnail.png",
            ]

            for preview_path in preview_paths:
                if preview_path in names:
                    image_data = zf.read(preview_path)
                    return Response(content=image_data, media_type="image/png")

            # If no plate image, try any PNG in Metadata
            for name in names:
                if name.startswith("Metadata/plate_") and name.endswith(".png") and "_small" not in name:
                    image_data = zf.read(name)
                    return Response(content=image_data, media_type="image/png")

            raise HTTPException(404, "No plate preview found in 3MF file")

    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid 3MF file")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error extracting plate preview: {str(e)}")


@router.post("/upload")
async def upload_archive(
    file: UploadFile = File(...),
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_auth_if_enabled),
):
    """Manually upload a 3MF file to archive."""
    if not file.filename or not file.filename.endswith(".3mf"):
        raise HTTPException(400, "File must be a .3mf file")

    # Save uploaded file temporarily
    temp_path = settings.archive_dir / "temp" / file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        content = await file.read()
        temp_path.write_bytes(content)

        service = ArchiveService(db)
        archive = await service.archive_print(
            printer_id=printer_id,
            source_file=temp_path,
            created_by_id=current_user.id if current_user else None,
        )

        if not archive:
            raise HTTPException(400, "Failed to archive file")

        return ArchiveResponse.model_validate(archive)
    finally:
        if temp_path.exists():
            temp_path.unlink()


@router.post("/upload-bulk")
async def upload_archives_bulk(
    files: list[UploadFile] = File(...),
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_auth_if_enabled),
):
    """Bulk upload multiple 3MF files to archive."""
    results = []
    errors = []

    for file in files:
        if not file.filename or not file.filename.endswith(".3mf"):
            errors.append({"filename": file.filename or "unknown", "error": "Not a .3mf file"})
            continue

        temp_path = settings.archive_dir / "temp" / file.filename
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            content = await file.read()
            temp_path.write_bytes(content)

            service = ArchiveService(db)
            archive = await service.archive_print(
                printer_id=printer_id,
                source_file=temp_path,
                created_by_id=current_user.id if current_user else None,
            )

            if archive:
                results.append(
                    {
                        "filename": file.filename,
                        "id": archive.id,
                        "status": "success",
                    }
                )
            else:
                errors.append({"filename": file.filename, "error": "Failed to process"})
        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})
        finally:
            if temp_path.exists():
                temp_path.unlink()

    return {
        "uploaded": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


@router.get("/{archive_id}/plates")
async def get_archive_plates(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get available plates from a multi-plate 3MF archive.

    Returns a list of plates with their index, name, thumbnail availability,
    and filament requirements. For single-plate exports, returns a single plate.
    """
    import xml.etree.ElementTree as ET

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    plates = []

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = zf.namelist()

            # Find all plate gcode files to determine available plates
            gcode_files = [n for n in namelist if n.startswith("Metadata/plate_") and n.endswith(".gcode")]

            if not gcode_files:
                # No sliced plates found
                return {"archive_id": archive_id, "filename": archive.filename, "plates": []}

            # Extract plate indices from gcode filenames
            plate_indices = []
            for gf in gcode_files:
                # "Metadata/plate_5.gcode" -> 5
                try:
                    plate_str = gf[15:-6]  # Remove "Metadata/plate_" and ".gcode"
                    plate_indices.append(int(plate_str))
                except ValueError:
                    pass

            plate_indices.sort()

            # Parse model_settings.config for plate names
            # Plate names are stored with plater_id and plater_name keys
            plate_names = {}  # plater_id -> name
            if "Metadata/model_settings.config" in namelist:
                try:
                    model_content = zf.read("Metadata/model_settings.config").decode()
                    model_root = ET.fromstring(model_content)
                    for plate_elem in model_root.findall(".//plate"):
                        plater_id = None
                        plater_name = None
                        for meta in plate_elem.findall("metadata"):
                            key = meta.get("key")
                            value = meta.get("value")
                            if key == "plater_id" and value:
                                try:
                                    plater_id = int(value)
                                except ValueError:
                                    pass
                            elif key == "plater_name" and value:
                                plater_name = value.strip()
                        if plater_id is not None and plater_name:
                            plate_names[plater_id] = plater_name
                except Exception:
                    pass  # model_settings.config parsing is optional

            # Parse slice_info.config for plate metadata
            plate_metadata = {}  # plate_index -> {filaments, prediction, weight, name, objects}
            if "Metadata/slice_info.config" in namelist:
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                for plate_elem in root.findall(".//plate"):
                    plate_info = {"filaments": [], "prediction": None, "weight": None, "name": None, "objects": []}

                    # Get plate index from metadata
                    plate_index = None
                    for meta in plate_elem.findall("metadata"):
                        key = meta.get("key")
                        value = meta.get("value")
                        if key == "index" and value:
                            try:
                                plate_index = int(value)
                            except ValueError:
                                pass
                        elif key == "prediction" and value:
                            try:
                                plate_info["prediction"] = int(value)
                            except ValueError:
                                pass
                        elif key == "weight" and value:
                            try:
                                plate_info["weight"] = float(value)
                            except ValueError:
                                pass

                    # Get filaments used in this plate
                    for filament_elem in plate_elem.findall("filament"):
                        filament_id = filament_elem.get("id")
                        filament_type = filament_elem.get("type", "")
                        filament_color = filament_elem.get("color", "")
                        used_g = filament_elem.get("used_g", "0")
                        used_m = filament_elem.get("used_m", "0")

                        try:
                            used_grams = float(used_g)
                        except (ValueError, TypeError):
                            used_grams = 0

                        if used_grams > 0 and filament_id:
                            plate_info["filaments"].append(
                                {
                                    "slot_id": int(filament_id),
                                    "type": filament_type,
                                    "color": filament_color,
                                    "used_grams": round(used_grams, 1),
                                    "used_meters": float(used_m) if used_m else 0,
                                }
                            )

                    # Sort filaments by slot ID
                    plate_info["filaments"].sort(key=lambda x: x["slot_id"])

                    # Collect all object names on this plate
                    for obj_elem in plate_elem.findall("object"):
                        obj_name = obj_elem.get("name")
                        if obj_name and obj_name not in plate_info["objects"]:
                            plate_info["objects"].append(obj_name)

                    # Set plate name: prefer custom name from model_settings.config,
                    # fall back to first object name if no custom name was set
                    if plate_index is not None:
                        custom_name = plate_names.get(plate_index)
                        if custom_name:
                            plate_info["name"] = custom_name
                        else:
                            # Fall back to first object name as hint
                            if plate_info["objects"]:
                                plate_info["name"] = plate_info["objects"][0]

                        plate_metadata[plate_index] = plate_info

            # Build plate list
            for idx in plate_indices:
                meta = plate_metadata.get(idx, {})
                has_thumbnail = f"Metadata/plate_{idx}.png" in namelist

                plates.append(
                    {
                        "index": idx,
                        "name": meta.get("name"),
                        "objects": meta.get("objects", []),
                        "has_thumbnail": has_thumbnail,
                        "thumbnail_url": f"/api/v1/archives/{archive_id}/plate-thumbnail/{idx}"
                        if has_thumbnail
                        else None,
                        "print_time_seconds": meta.get("prediction"),
                        "filament_used_grams": meta.get("weight"),
                        "filaments": meta.get("filaments", []),
                    }
                )

    except Exception as e:
        logger.warning(f"Failed to parse plates from archive {archive_id}: {e}")

    return {
        "archive_id": archive_id,
        "filename": archive.filename,
        "plates": plates,
        "is_multi_plate": len(plates) > 1,
    }


@router.get("/{archive_id}/plate-thumbnail/{plate_index}")
async def get_plate_thumbnail(
    archive_id: int,
    plate_index: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the thumbnail image for a specific plate."""
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            thumb_path = f"Metadata/plate_{plate_index}.png"
            if thumb_path in zf.namelist():
                data = zf.read(thumb_path)
                return Response(content=data, media_type="image/png")
    except Exception:
        pass

    raise HTTPException(404, f"Thumbnail for plate {plate_index} not found")


@router.get("/{archive_id}/filament-requirements")
async def get_filament_requirements(
    archive_id: int,
    plate_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get filament requirements from the archived 3MF file.

    Returns the filaments used in this print with their slot IDs, types, colors,
    and usage amounts. This can be compared with current AMS state before reprinting.

    Args:
        archive_id: The archive ID
        plate_id: Optional plate index to filter filaments for (for multi-plate files)
    """
    import xml.etree.ElementTree as ET

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    filaments = []

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # Parse slice_info.config for filament requirements
            if "Metadata/slice_info.config" in zf.namelist():
                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                # If plate_id is specified, find filaments for that specific plate
                if plate_id is not None:
                    # Find the plate element with matching index
                    for plate_elem in root.findall(".//plate"):
                        plate_index = None
                        for meta in plate_elem.findall("metadata"):
                            if meta.get("key") == "index":
                                try:
                                    plate_index = int(meta.get("value", "0"))
                                except ValueError:
                                    pass
                                break

                        if plate_index == plate_id:
                            # Extract filaments from this plate element
                            for filament_elem in plate_elem.findall("filament"):
                                filament_id = filament_elem.get("id")
                                filament_type = filament_elem.get("type", "")
                                filament_color = filament_elem.get("color", "")
                                used_g = filament_elem.get("used_g", "0")
                                used_m = filament_elem.get("used_m", "0")

                                try:
                                    used_grams = float(used_g)
                                except (ValueError, TypeError):
                                    used_grams = 0

                                if used_grams > 0 and filament_id:
                                    filaments.append(
                                        {
                                            "slot_id": int(filament_id),
                                            "type": filament_type,
                                            "color": filament_color,
                                            "used_grams": round(used_grams, 1),
                                            "used_meters": float(used_m) if used_m else 0,
                                        }
                                    )
                            break
                else:
                    # No plate_id specified - extract all filaments with used_g > 0
                    # This is the legacy behavior for single-plate files
                    for filament_elem in root.findall(".//filament"):
                        filament_id = filament_elem.get("id")
                        filament_type = filament_elem.get("type", "")
                        filament_color = filament_elem.get("color", "")
                        used_g = filament_elem.get("used_g", "0")
                        used_m = filament_elem.get("used_m", "0")

                        # Only include filaments that are actually used
                        try:
                            used_grams = float(used_g)
                        except (ValueError, TypeError):
                            used_grams = 0

                        if used_grams > 0 and filament_id:
                            filaments.append(
                                {
                                    "slot_id": int(filament_id),
                                    "type": filament_type,
                                    "color": filament_color,
                                    "used_grams": round(used_grams, 1),
                                    "used_meters": float(used_m) if used_m else 0,
                                }
                            )

            # Sort by slot ID
            filaments.sort(key=lambda x: x["slot_id"])

    except Exception as e:
        logger.warning(f"Failed to parse filament requirements from archive {archive_id}: {e}")

    return {
        "archive_id": archive_id,
        "filename": archive.filename,
        "plate_id": plate_id,
        "filaments": filaments,
    }


@router.post("/{archive_id}/reprint")
async def reprint_archive(
    archive_id: int,
    printer_id: int,
    body: ReprintRequest | None = None,
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.ARCHIVES_REPRINT_ALL,
            Permission.ARCHIVES_REPRINT_OWN,
        )
    ),
):
    """Send an archived 3MF file to a printer and start printing."""
    from backend.app.main import register_expected_print
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import (
        get_ftp_retry_settings,
        upload_file_async,
        with_ftp_retry,
    )
    from backend.app.services.printer_manager import printer_manager

    user, can_modify_all = auth_result

    # Use defaults if no body provided
    if body is None:
        body = ReprintRequest()

    # Get archive
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Ownership check
    if not can_modify_all:
        if archive.created_by_id != user.id:
            raise HTTPException(403, "You can only reprint your own archives")

    # Get printer
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    # Check printer is connected
    if not printer_manager.is_connected(printer_id):
        raise HTTPException(400, "Printer is not connected")

    # Get the sliced 3MF file path
    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    # Upload file to printer via FTP
    from backend.app.services.bambu_ftp import delete_file_async

    # Use a clean filename to avoid issues with double extensions like .gcode.3mf
    # The printer might reject filenames with unusual extensions
    base_name = archive.filename
    if base_name.endswith(".gcode.3mf"):
        base_name = base_name[:-10]  # Remove .gcode.3mf
    elif base_name.endswith(".3mf"):
        base_name = base_name[:-4]  # Remove .3mf
    remote_filename = f"{base_name}.3mf"
    remote_path = f"/{remote_filename}"

    # Get FTP retry settings
    ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

    logger.info(
        f"Reprint FTP upload starting: printer={printer.name} ({printer.model}), "
        f"ip={printer.ip_address}, file={remote_filename}, local_path={file_path}, "
        f"retry_enabled={ftp_retry_enabled}, retry_count={ftp_retry_count}, timeout={ftp_timeout}"
    )

    # Delete existing file if present (avoids 553 error)
    logger.debug(f"Deleting existing file {remote_path} if present...")
    delete_result = await delete_file_async(
        printer.ip_address,
        printer.access_code,
        remote_path,
        socket_timeout=ftp_timeout,
        printer_model=printer.model,
    )
    logger.debug(f"Delete result: {delete_result}")

    if ftp_retry_enabled:
        uploaded = await with_ftp_retry(
            upload_file_async,
            printer.ip_address,
            printer.access_code,
            file_path,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
            max_retries=ftp_retry_count,
            retry_delay=ftp_retry_delay,
            operation_name=f"Upload for reprint to {printer.name}",
        )
    else:
        uploaded = await upload_file_async(
            printer.ip_address,
            printer.access_code,
            file_path,
            remote_path,
            socket_timeout=ftp_timeout,
            printer_model=printer.model,
        )

    if not uploaded:
        logger.error(
            f"FTP upload failed for reprint: printer={printer.name}, model={printer.model}, "
            f"ip={printer.ip_address}, file={remote_filename}. "
            "Check logs above for storage diagnostics and specific error codes."
        )
        raise HTTPException(
            500,
            "Failed to upload file to printer. Check if SD card is inserted and properly formatted (FAT32/exFAT). "
            "See server logs for detailed diagnostics.",
        )

    # Register this as an expected print so we don't create a duplicate archive
    register_expected_print(printer_id, remote_filename, archive_id)

    # Use plate_id from request if provided, otherwise auto-detect from 3MF file
    if body.plate_id is not None:
        plate_id = body.plate_id
    else:
        # Auto-detect plate ID from 3MF file (legacy behavior for single-plate files)
        plate_id = 1
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in zf.namelist():
                    if name.startswith("Metadata/plate_") and name.endswith(".gcode"):
                        # Extract plate number from "Metadata/plate_X.gcode"
                        plate_str = name[15:-6]  # Remove "Metadata/plate_" and ".gcode"
                        plate_id = int(plate_str)
                        break
        except Exception:
            pass  # Default to plate 1 if detection fails

    logger.info(
        f"Reprint archive {archive_id}: plate_id={plate_id}, "
        f"ams_mapping={body.ams_mapping}, bed_levelling={body.bed_levelling}, "
        f"flow_cali={body.flow_cali}, vibration_cali={body.vibration_cali}, "
        f"layer_inspect={body.layer_inspect}, timelapse={body.timelapse}"
    )

    # Start the print with options
    started = printer_manager.start_print(
        printer_id,
        remote_filename,
        plate_id,
        ams_mapping=body.ams_mapping,
        timelapse=body.timelapse,
        bed_levelling=body.bed_levelling,
        flow_cali=body.flow_cali,
        vibration_cali=body.vibration_cali,
        layer_inspect=body.layer_inspect,
        use_ams=body.use_ams,
    )

    if not started:
        raise HTTPException(500, "Failed to start print")

    # Track who started this print (Issue #206)
    if user:
        printer_manager.set_current_print_user(printer_id, user.id, user.username)
        logger.info(f"Reprint started by user: {user.username}")

    return {
        "status": "printing",
        "printer_id": printer_id,
        "archive_id": archive_id,
        "filename": archive.filename,
    }


# =============================================================================
# Project Page API
# =============================================================================


@router.get("/{archive_id}/project-page")
async def get_project_page(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Get the project page data from the 3MF file."""
    from backend.app.schemas.archive import ProjectPageResponse
    from backend.app.services.archive import ProjectPageParser

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    parser = ProjectPageParser(file_path)
    data = parser.parse(archive_id)

    return ProjectPageResponse(**data)


@router.patch("/{archive_id}/project-page")
async def update_project_page(
    archive_id: int,
    update_data: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update project page metadata in the 3MF file."""
    from backend.app.services.archive import ProjectPageParser

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    parser = ProjectPageParser(file_path)
    success = parser.update_metadata(update_data)

    if not success:
        raise HTTPException(500, "Failed to update project page")

    # Return updated data
    data = parser.parse(archive_id)
    return data


@router.get("/{archive_id}/project-image/{image_path:path}")
async def get_project_image(
    archive_id: int,
    image_path: str,
    db: AsyncSession = Depends(get_db),
):
    """Get an image from the 3MF project page."""
    from backend.app.services.archive import ProjectPageParser

    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

    file_path = settings.base_dir / archive.file_path
    if not file_path.exists():
        raise HTTPException(404, "Archive file not found")

    parser = ProjectPageParser(file_path)
    result = parser.get_image(image_path)

    if not result:
        raise HTTPException(404, "Image not found in 3MF file")

    image_data, content_type = result
    return Response(
        content=image_data,
        media_type=content_type,
        headers={"Cache-Control": "max-age=3600"},
    )


# =============================================================================
# Source 3MF API (Original Project Files)
# =============================================================================


@router.post("/{archive_id}/source")
async def upload_source_3mf(
    archive_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload the original source 3MF project file for an archive."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not file.filename or not file.filename.endswith(".3mf"):
        raise HTTPException(400, "File must be a .3mf file")

    # Get archive directory and create source subdirectory
    file_path = settings.base_dir / archive.file_path
    archive_dir = file_path.parent
    source_dir = archive_dir / "source"
    source_dir.mkdir(exist_ok=True)

    # Delete old source file if exists
    if archive.source_3mf_path:
        old_source_path = settings.base_dir / archive.source_3mf_path
        if old_source_path.exists():
            old_source_path.unlink()

    # Save the source 3MF file - preserve original filename
    source_filename = file.filename
    source_path = source_dir / source_filename

    content = await file.read()
    source_path.write_bytes(content)

    # Update archive with source path (relative to base_dir)
    archive.source_3mf_path = str(source_path.relative_to(settings.base_dir))

    await db.commit()
    await db.refresh(archive)

    return {
        "status": "uploaded",
        "source_3mf_path": archive.source_3mf_path,
        "filename": source_filename,
    }


@router.get("/{archive_id}/source")
async def download_source_3mf(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Download the source 3MF project file."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.source_3mf_path:
        raise HTTPException(404, "No source 3MF attached to this archive")

    source_path = settings.base_dir / archive.source_3mf_path
    if not source_path.exists():
        raise HTTPException(404, "Source 3MF file not found on disk")

    # Use the actual filename from the path
    filename = source_path.name

    return FileResponse(
        path=source_path,
        filename=filename,
        media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
    )


@router.get("/{archive_id}/source/{filename}")
async def download_source_3mf_for_slicer(
    archive_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Download source 3MF with filename in URL (for Bambu Studio compatibility)."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.source_3mf_path:
        raise HTTPException(404, "No source 3MF attached to this archive")

    source_path = settings.base_dir / archive.source_3mf_path
    if not source_path.exists():
        raise HTTPException(404, "Source 3MF file not found on disk")

    return FileResponse(
        path=source_path,
        filename=filename if filename.endswith(".3mf") else f"{filename}.3mf",
        media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
    )


@router.post("/upload-source")
async def upload_source_3mf_by_name(
    file: UploadFile = File(...),
    print_name: str = Query(None, description="Match archive by print name"),
    db: AsyncSession = Depends(get_db),
):
    """Upload source 3MF and match to archive by print name.

    This endpoint is designed for slicer post-processing scripts.
    It finds the most recent archive matching the print name and attaches the source.
    """
    if not file.filename or not file.filename.endswith(".3mf"):
        raise HTTPException(400, "File must be a .3mf file")

    # Derive print name from filename if not provided
    if not print_name:
        # Remove .3mf extension and common suffixes
        print_name = file.filename.rsplit(".3mf", 1)[0]
        # Remove _source suffix if present
        if print_name.endswith("_source"):
            print_name = print_name[:-7]

    # Find matching archive - try exact match first, then fuzzy
    result = await db.execute(
        select(PrintArchive)
        .where(PrintArchive.print_name == print_name)
        .order_by(PrintArchive.created_at.desc())
        .limit(1)
    )
    archive = result.scalar_one_or_none()

    if not archive:
        # Try matching filename without .gcode.3mf
        result = await db.execute(
            select(PrintArchive)
            .where(PrintArchive.filename.like(f"{print_name}%"))
            .order_by(PrintArchive.created_at.desc())
            .limit(1)
        )
        archive = result.scalar_one_or_none()

    if not archive:
        # Try case-insensitive partial match on print_name
        result = await db.execute(
            select(PrintArchive)
            .where(PrintArchive.print_name.ilike(f"%{print_name}%"))
            .order_by(PrintArchive.created_at.desc())
            .limit(1)
        )
        archive = result.scalar_one_or_none()

    if not archive:
        raise HTTPException(404, f"No archive found matching '{print_name}'")

    # Get archive directory and create source subdirectory
    file_path = settings.base_dir / archive.file_path
    archive_dir = file_path.parent
    source_dir = archive_dir / "source"
    source_dir.mkdir(exist_ok=True)

    # Delete old source file if exists
    if archive.source_3mf_path:
        old_source_path = settings.base_dir / archive.source_3mf_path
        if old_source_path.exists():
            old_source_path.unlink()

    # Save the source 3MF file - preserve original filename
    source_filename = file.filename
    source_path = source_dir / source_filename

    content = await file.read()
    source_path.write_bytes(content)

    # Update archive with source path
    archive.source_3mf_path = str(source_path.relative_to(settings.base_dir))
    await db.commit()
    await db.refresh(archive)

    return {
        "status": "uploaded",
        "archive_id": archive.id,
        "archive_name": archive.print_name or archive.filename,
        "source_3mf_path": archive.source_3mf_path,
        "filename": source_filename,
    }


@router.delete("/{archive_id}/source")
async def delete_source_3mf(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete the source 3MF project file from an archive."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.source_3mf_path:
        raise HTTPException(404, "No source 3MF attached to this archive")

    # Delete the file
    source_path = settings.base_dir / archive.source_3mf_path
    if source_path.exists():
        source_path.unlink()

    # Clear the path in database
    archive.source_3mf_path = None
    await db.commit()

    return {"status": "deleted"}


# =============================================================================
# F3D API (Fusion 360 Design Files)
# =============================================================================


@router.post("/{archive_id}/f3d")
async def upload_f3d(
    archive_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a Fusion 360 design file for an archive."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not file.filename or not file.filename.endswith(".f3d"):
        raise HTTPException(400, "File must be a .f3d file")

    # Get archive directory and create f3d subdirectory
    file_path = settings.base_dir / archive.file_path
    archive_dir = file_path.parent
    f3d_dir = archive_dir / "f3d"
    f3d_dir.mkdir(exist_ok=True)

    # Delete old F3D file if exists
    if archive.f3d_path:
        old_f3d_path = settings.base_dir / archive.f3d_path
        if old_f3d_path.exists():
            old_f3d_path.unlink()

    # Save the F3D file - preserve original filename
    f3d_filename = file.filename
    f3d_path = f3d_dir / f3d_filename

    content = await file.read()
    f3d_path.write_bytes(content)

    # Update archive with F3D path (relative to base_dir)
    archive.f3d_path = str(f3d_path.relative_to(settings.base_dir))

    await db.commit()
    await db.refresh(archive)

    return {
        "status": "uploaded",
        "f3d_path": archive.f3d_path,
        "filename": f3d_filename,
    }


@router.get("/{archive_id}/f3d")
async def download_f3d(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Download the Fusion 360 design file."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.f3d_path:
        raise HTTPException(404, "No F3D file attached to this archive")

    f3d_path = settings.base_dir / archive.f3d_path
    if not f3d_path.exists():
        raise HTTPException(404, "F3D file not found on disk")

    # Use the actual filename from the path
    filename = f3d_path.name

    return FileResponse(
        path=f3d_path,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/{archive_id}/f3d")
async def delete_f3d(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete the Fusion 360 design file from an archive."""
    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    if not archive.f3d_path:
        raise HTTPException(404, "No F3D file attached to this archive")

    # Delete the file
    f3d_path = settings.base_dir / archive.f3d_path
    if f3d_path.exists():
        f3d_path.unlink()

    # Clear the path in database
    archive.f3d_path = None
    await db.commit()

    return {"status": "deleted"}
