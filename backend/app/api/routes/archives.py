import io
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models.archive import PrintArchive
from backend.app.models.filament import Filament
from backend.app.schemas.archive import ArchiveResponse, ArchiveStats, ArchiveUpdate
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
        "status": archive.status,
        "started_at": archive.started_at,
        "completed_at": archive.completed_at,
        "extra_data": archive.extra_data,
        "makerworld_url": archive.makerworld_url,
        "designer": archive.designer,
        "is_favorite": archive.is_favorite,
        "tags": archive.tags,
        "notes": archive.notes,
        "cost": archive.cost,
        "photos": archive.photos,
        "failure_reason": archive.failure_reason,
        "created_at": archive.created_at,
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

    # Totals
    time_result = await db.execute(select(func.sum(PrintArchive.print_time_seconds)))
    total_time = (time_result.scalar() or 0) / 3600  # Convert to hours

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
        from backend.app.services.tasmota import tasmota_service

        plugs_result = await db.execute(select(SmartPlug))
        plugs = list(plugs_result.scalars().all())

        total_energy_kwh = 0.0
        for plug in plugs:
            energy = await tasmota_service.get_energy(plug)
            if energy and energy.get("total") is not None:
                total_energy_kwh += energy["total"]

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
):
    """Update archive metadata (tags, notes, cost, is_favorite, project_id)."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(PrintArchive).options(selectinload(PrintArchive.project)).where(PrintArchive.id == archive_id)
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    for field, value in update_data.model_dump(exclude_unset=True).items():
        setattr(archive, field, value)

    await db.commit()

    # Re-fetch with project relationship loaded after commit
    result = await db.execute(
        select(PrintArchive).options(selectinload(PrintArchive.project)).where(PrintArchive.id == archive_id)
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
            archive.cost = round((archive.filament_used_grams / 1000) * 25.0, 2)

    await db.commit()
    await db.refresh(archive)
    return archive


@router.post("/recalculate-costs")
async def recalculate_all_costs(db: AsyncSession = Depends(get_db)):
    """Recalculate costs for all archives based on filament usage and prices."""
    result = await db.execute(select(PrintArchive))
    archives = list(result.scalars().all())

    # Load all filaments for lookup
    filament_result = await db.execute(select(Filament))
    filaments = {f.type: f.cost_per_kg for f in filament_result.scalars().all()}
    default_cost_per_kg = 25.0

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
async def delete_archive(archive_id: int, db: AsyncSession = Depends(get_db)):
    """Delete an archive."""
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

    return FileResponse(path=thumb_path, media_type="image/png")


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

    return FileResponse(
        path=timelapse_path,
        media_type="video/mp4",
        filename=f"{archive.print_name or 'timelapse'}.mp4",
    )


@router.post("/{archive_id}/timelapse/scan")
async def scan_timelapse(
    archive_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Scan printer for timelapse matching this archive and attach it."""
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import download_file_bytes_async, list_files_async

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
    timelapse_data = await download_file_bytes_async(printer.ip_address, printer.access_code, remote_path)

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
    from backend.app.services.bambu_ftp import download_file_bytes_async, list_files_async

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
    timelapse_data = await download_file_bytes_async(printer.ip_address, printer.access_code, remote_path)
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
    import qrcode

    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(404, "Archive not found")

    # Build URL to archive detail page
    base_url = str(request.base_url).rstrip("/")
    archive_url = f"{base_url}/archives?id={archive_id}"

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

    # Resize if needed
    if size != 200:
        img = img.resize((size, size))

    # Convert to bytes
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
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
    build_volume = {"x": 256, "y": 256, "z": 256}  # Default to X1/P1 size
    filament_colors: list[str] = []

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            names = zf.namelist()

            # Check for G-code
            has_gcode = any(n.startswith("Metadata/") and n.endswith(".gcode") for n in names)

            # Check for 3D model - need to look for actual mesh data
            for name in names:
                if name.endswith(".model"):
                    try:
                        content = zf.read(name).decode("utf-8")
                        # Check if this model file contains actual mesh vertices
                        if "<vertex" in content or "<mesh" in content:
                            has_model = True
                            break
                    except Exception:
                        pass

            # Extract filament colors from slice_info.config
            # These are the actual filaments used in the print, indexed by tool/extruder
            if "Metadata/slice_info.config" in names:
                try:
                    slice_content = zf.read("Metadata/slice_info.config").decode("utf-8")
                    root = ET.fromstring(slice_content)

                    # Get all filaments with their IDs and colors
                    # <filament id="1" type="PLA" color="#FFFFFF" used_g="100" />
                    # ID corresponds to the tool number in G-code (T0, T1, etc.)
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

                        # Include all filaments, but mark unused ones
                        if fid is not None and fcolor:
                            try:
                                # IDs are 1-based in slice_info, tools are 0-based
                                tool_id = int(fid) - 1
                                if tool_id >= 0 and used_amount > 0:
                                    filament_map[tool_id] = fcolor
                            except ValueError:
                                pass

                    # Convert to ordered list (tool 0, tool 1, etc.)
                    if filament_map:
                        max_tool = max(filament_map.keys())
                        for i in range(max_tool + 1):
                            filament_colors.append(filament_map.get(i, "#00AE42"))
                except Exception:
                    pass

            # Extract build volume from project settings
            if "Metadata/project_settings.config" in names:
                try:
                    config_content = zf.read("Metadata/project_settings.config").decode("utf-8")
                    config_data = json.loads(config_content)

                    # Parse printable_area: ['0x0', '256x0', '256x256', '0x256']
                    printable_area = config_data.get("printable_area", [])
                    if printable_area and len(printable_area) >= 3:
                        # Get max X and Y from the corner coordinates
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

                    # Parse printable_height
                    printable_height = config_data.get("printable_height")
                    if printable_height:
                        try:
                            build_volume["z"] = int(printable_height)
                        except (ValueError, TypeError):
                            pass
                except Exception:
                    pass

    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid 3MF file")

    return {
        "has_model": has_model,
        "has_gcode": has_gcode,
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


@router.post("/upload")
async def upload_archive(
    file: UploadFile = File(...),
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
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


@router.post("/{archive_id}/reprint")
async def reprint_archive(
    archive_id: int,
    printer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Send an archived 3MF file to a printer and start printing."""
    from backend.app.main import register_expected_print
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_ftp import upload_file_async
    from backend.app.services.printer_manager import printer_manager

    # Get archive
    service = ArchiveService(db)
    archive = await service.get_archive(archive_id)
    if not archive:
        raise HTTPException(404, "Archive not found")

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

    # Delete existing file if present (avoids 553 error)
    await delete_file_async(
        printer.ip_address,
        printer.access_code,
        remote_path,
    )

    uploaded = await upload_file_async(
        printer.ip_address,
        printer.access_code,
        file_path,
        remote_path,
    )

    if not uploaded:
        raise HTTPException(500, "Failed to upload file to printer")

    # Register this as an expected print so we don't create a duplicate archive
    register_expected_print(printer_id, remote_filename, archive_id)

    # Detect plate ID from 3MF file
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

    logger.info(f"Reprint archive {archive_id}: using plate_id={plate_id}")

    # Start the print
    started = printer_manager.start_print(printer_id, remote_filename, plate_id)

    if not started:
        raise HTTPException(500, "Failed to start print")

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
