import io
import json
import logging
import os
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.library import get_library_dir
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project
from backend.app.models.project_bom import ProjectBOMItem
from backend.app.models.user import User
from backend.app.schemas.project import (
    ArchivePreview,
    BatchAddArchives,
    BatchAddQueueItems,
    BOMItemCreate,
    BOMItemResponse,
    BOMItemUpdate,
    ProjectChildPreview,
    ProjectCreate,
    ProjectImport,
    ProjectListResponse,
    ProjectResponse,
    ProjectStats,
    ProjectUpdate,
    TimelineEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


async def compute_project_stats(
    db: AsyncSession, project_id: int, target_count: int | None = None, target_parts_count: int | None = None
) -> ProjectStats:
    """Compute statistics for a project."""
    # Count total archives (distinct print jobs)
    total_result = await db.execute(select(func.count(PrintArchive.id)).where(PrintArchive.project_id == project_id))
    total_archives = total_result.scalar() or 0

    # Sum total items (using quantity field)
    total_items_result = await db.execute(
        select(func.coalesce(func.sum(PrintArchive.quantity), 0)).where(PrintArchive.project_id == project_id)
    )
    total_items = total_items_result.scalar() or 0

    # Count failed archives (number of print jobs) - includes all failure states
    failed_result = await db.execute(
        select(func.count(PrintArchive.id)).where(
            PrintArchive.project_id == project_id,
            PrintArchive.status.in_(["failed", "aborted", "cancelled", "stopped"]),
        )
    )
    failed_prints = failed_result.scalar() or 0

    # Sum print time, filament, and energy
    sums_result = await db.execute(
        select(
            func.coalesce(func.sum(PrintArchive.print_time_seconds), 0).label("total_time"),
            func.coalesce(func.sum(PrintArchive.filament_used_grams), 0).label("total_filament"),
            func.coalesce(func.sum(PrintArchive.cost), 0).label("total_filament_cost"),
            func.coalesce(func.sum(PrintArchive.energy_kwh), 0).label("total_energy"),
            func.coalesce(func.sum(PrintArchive.energy_cost), 0).label("total_energy_cost"),
        ).where(PrintArchive.project_id == project_id)
    )
    sums = sums_result.first()

    # Count queued items
    queued_result = await db.execute(
        select(func.count(PrintQueueItem.id)).where(
            PrintQueueItem.project_id == project_id, PrintQueueItem.status == "pending"
        )
    )
    queued_prints = queued_result.scalar() or 0

    # Count in-progress items
    in_progress_result = await db.execute(
        select(func.count(PrintQueueItem.id)).where(
            PrintQueueItem.project_id == project_id, PrintQueueItem.status == "printing"
        )
    )
    in_progress_prints = in_progress_result.scalar() or 0

    # Sum completed items (parts) - sum of quantities for successful prints
    completed_items_result = await db.execute(
        select(func.coalesce(func.sum(PrintArchive.quantity), 0)).where(
            PrintArchive.project_id == project_id,
            PrintArchive.status.in_(["completed", "archived"]),
        )
    )
    completed_items = int(completed_items_result.scalar() or 0)

    # Calculate progress for plates (target_count vs total_archives)
    progress_percent = None
    remaining_prints = None
    if target_count and target_count > 0:
        progress_percent = round((total_archives / target_count) * 100, 1)
        remaining_prints = max(0, target_count - total_archives)

    # Calculate progress for parts (target_parts_count vs completed_items)
    parts_progress_percent = None
    remaining_parts = None
    if target_parts_count and target_parts_count > 0:
        parts_progress_percent = round((completed_items / target_parts_count) * 100, 1)
        remaining_parts = max(0, target_parts_count - completed_items)

    # BOM stats
    bom_result = await db.execute(
        select(
            func.count(ProjectBOMItem.id).label("total"),
            func.sum(case((ProjectBOMItem.quantity_acquired >= ProjectBOMItem.quantity_needed, 1), else_=0)).label(
                "completed"
            ),
        ).where(ProjectBOMItem.project_id == project_id)
    )
    bom_stats = bom_result.first()

    return ProjectStats(
        total_archives=total_archives,
        total_items=int(total_items),
        completed_prints=completed_items,  # Now reflects sum of quantities for completed prints
        failed_prints=int(failed_prints),
        queued_prints=queued_prints,
        in_progress_prints=in_progress_prints,
        total_print_time_hours=round((sums.total_time or 0) / 3600, 2),
        total_filament_grams=round(sums.total_filament or 0, 2),
        progress_percent=progress_percent,
        parts_progress_percent=parts_progress_percent,
        estimated_cost=round((sums.total_filament_cost or 0), 2),
        total_energy_kwh=round((sums.total_energy or 0), 3),
        total_energy_cost=round((sums.total_energy_cost or 0), 3),
        remaining_prints=remaining_prints,
        remaining_parts=remaining_parts,
        bom_total_items=bom_stats.total or 0,
        bom_completed_items=int(bom_stats.completed or 0),
    )


@router.get("", response_model=list[ProjectListResponse])
@router.get("/", response_model=list[ProjectListResponse])
async def list_projects(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """List all projects with basic stats."""
    query = select(Project)
    if status:
        query = query.where(Project.status == status)
    query = query.order_by(Project.updated_at.desc())

    result = await db.execute(query)
    projects = result.scalars().all()

    # Compute quick stats for each project
    response = []
    for project in projects:
        # Get archive count (number of print jobs)
        archive_count_result = await db.execute(
            select(func.count(PrintArchive.id)).where(PrintArchive.project_id == project.id)
        )
        archive_count = archive_count_result.scalar() or 0

        # Get total items (sum of quantities)
        total_items_result = await db.execute(
            select(func.coalesce(func.sum(PrintArchive.quantity), 0)).where(PrintArchive.project_id == project.id)
        )
        total_items = int(total_items_result.scalar() or 0)

        # Get queue count
        queue_count_result = await db.execute(
            select(func.count(PrintQueueItem.id)).where(
                PrintQueueItem.project_id == project.id,
                PrintQueueItem.status.in_(["pending", "printing"]),
            )
        )
        queue_count = queue_count_result.scalar() or 0

        # Sum completed parts (quantities) - includes "archived" as successful
        completed_result = await db.execute(
            select(func.coalesce(func.sum(PrintArchive.quantity), 0)).where(
                PrintArchive.project_id == project.id,
                PrintArchive.status.in_(["completed", "archived"]),
            )
        )
        completed_count = int(completed_result.scalar() or 0)

        # Sum failed parts (quantities) - includes all failure states
        failed_result = await db.execute(
            select(func.coalesce(func.sum(PrintArchive.quantity), 0)).where(
                PrintArchive.project_id == project.id,
                PrintArchive.status.in_(["failed", "aborted", "cancelled", "stopped"]),
            )
        )
        failed_count = int(failed_result.scalar() or 0)

        # Plates progress: archive_count / target_count
        progress_percent = None
        if project.target_count and project.target_count > 0:
            progress_percent = round((archive_count / project.target_count) * 100, 1)

        # Get archive previews (up to 6 most recent)
        archives_result = await db.execute(
            select(PrintArchive)
            .where(PrintArchive.project_id == project.id)
            .order_by(PrintArchive.created_at.desc())
            .limit(6)
        )
        archives = archives_result.scalars().all()
        archive_previews = [
            ArchivePreview(
                id=a.id,
                print_name=a.print_name,
                thumbnail_path=a.thumbnail_path,
                status=a.status,
                filament_type=a.filament_type,
                filament_color=a.filament_color,
            )
            for a in archives
        ]

        response.append(
            ProjectListResponse(
                id=project.id,
                name=project.name,
                description=project.description,
                color=project.color,
                status=project.status,
                target_count=project.target_count,
                target_parts_count=project.target_parts_count,
                created_at=project.created_at,
                archive_count=archive_count,
                total_items=total_items,
                completed_count=completed_count,
                failed_count=failed_count,
                queue_count=queue_count,
                progress_percent=progress_percent,
                archives=archive_previews,
            )
        )

    return response


@router.post("/", response_model=ProjectResponse)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Create a new project."""
    # Verify parent exists if specified
    parent_name = None
    if data.parent_id:
        parent_result = await db.execute(select(Project).where(Project.id == data.parent_id))
        parent = parent_result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=400, detail="Parent project not found")
        parent_name = parent.name

    project = Project(
        name=data.name,
        description=data.description,
        color=data.color,
        target_count=data.target_count,
        target_parts_count=data.target_parts_count,
        notes=data.notes,
        tags=data.tags,
        due_date=data.due_date,
        priority=data.priority,
        budget=data.budget,
        parent_id=data.parent_id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        notes=project.notes,
        attachments=project.attachments,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=parent_name,
        children=[],
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )


# ============ Phase 8: Template Endpoints (Static routes BEFORE dynamic {project_id}) ============


@router.get("/templates", response_model=list[ProjectListResponse])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """List all project templates."""
    result = await db.execute(select(Project).where(Project.is_template.is_(True)).order_by(Project.name))
    templates = result.scalars().all()

    response = []
    for project in templates:
        # Get archive count
        archive_count_result = await db.execute(
            select(func.count(PrintArchive.id)).where(PrintArchive.project_id == project.id)
        )
        archive_count = archive_count_result.scalar() or 0

        response.append(
            ProjectListResponse(
                id=project.id,
                name=project.name,
                description=project.description,
                color=project.color,
                status=project.status,
                target_count=project.target_count,
                created_at=project.created_at,
                archive_count=archive_count,
                queue_count=0,
                progress_percent=None,
                archives=[],
            )
        )

    return response


@router.post("/from-template/{template_id}", response_model=ProjectResponse)
async def create_project_from_template(
    template_id: int,
    name: str = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Create a new project from a template."""
    result = await db.execute(select(Project).where(Project.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if not template.is_template:
        raise HTTPException(status_code=400, detail="Project is not a template")

    # Create new project
    project = Project(
        name=name or template.name.replace(" (Template)", ""),
        description=template.description,
        color=template.color,
        target_count=template.target_count,
        target_parts_count=template.target_parts_count,
        notes=template.notes,
        tags=template.tags,
        priority=template.priority,
        budget=template.budget,
        is_template=False,
        template_source_id=template.id,
    )
    db.add(project)
    await db.flush()

    # Copy BOM items
    bom_result = await db.execute(select(ProjectBOMItem).where(ProjectBOMItem.project_id == template_id))
    bom_items = bom_result.scalars().all()

    for item in bom_items:
        new_item = ProjectBOMItem(
            project_id=project.id,
            name=item.name,
            quantity_needed=item.quantity_needed,
            quantity_acquired=0,
            unit_price=item.unit_price,
            sourcing_url=item.sourcing_url,
            stl_filename=item.stl_filename,
            remarks=item.remarks,
            sort_order=item.sort_order,
        )
        db.add(new_item)

    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        notes=project.notes,
        attachments=project.attachments,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=None,
        children=[],
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )


# ============ Dynamic {project_id} Routes ============


async def get_child_previews(db: AsyncSession, parent_id: int) -> list[ProjectChildPreview]:
    """Get preview info for child projects."""
    result = await db.execute(select(Project).where(Project.parent_id == parent_id).order_by(Project.name))
    children = result.scalars().all()

    previews = []
    for child in children:
        # Get completed count for progress (sum of quantities)
        completed_result = await db.execute(
            select(func.coalesce(func.sum(PrintArchive.quantity), 0)).where(
                PrintArchive.project_id == child.id,
                PrintArchive.status == "completed",
            )
        )
        completed_count = completed_result.scalar() or 0
        progress = None
        if child.target_count and child.target_count > 0:
            progress = round((int(completed_count) / child.target_count) * 100, 1)

        previews.append(
            ProjectChildPreview(
                id=child.id,
                name=child.name,
                color=child.color,
                status=child.status,
                progress_percent=progress,
            )
        )
    return previews


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """Get a project by ID with detailed stats."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get parent name
    parent_name = None
    if project.parent_id:
        parent_result = await db.execute(select(Project.name).where(Project.id == project.parent_id))
        parent_name = parent_result.scalar()

    # Get children
    children = await get_child_previews(db, project.id)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        notes=project.notes,
        attachments=project.attachments,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=parent_name,
        children=children,
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Update a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Update fields if provided
    if data.name is not None:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    if data.color is not None:
        project.color = data.color
    if data.status is not None:
        if data.status not in ["active", "completed", "archived"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        project.status = data.status
    if data.target_count is not None:
        project.target_count = data.target_count
    if data.target_parts_count is not None:
        project.target_parts_count = data.target_parts_count
    if data.notes is not None:
        project.notes = data.notes
    if data.tags is not None:
        project.tags = data.tags
    if data.due_date is not None:
        project.due_date = data.due_date
    if data.priority is not None:
        if data.priority not in ["low", "normal", "high", "urgent"]:
            raise HTTPException(status_code=400, detail="Invalid priority")
        project.priority = data.priority
    if data.budget is not None:
        project.budget = data.budget
    if data.parent_id is not None:
        # Verify parent exists and prevent circular reference
        if data.parent_id == project_id:
            raise HTTPException(status_code=400, detail="Project cannot be its own parent")
        if data.parent_id != 0:  # 0 means remove parent
            parent_result = await db.execute(select(Project).where(Project.id == data.parent_id))
            if not parent_result.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Parent project not found")
            project.parent_id = data.parent_id
        else:
            project.parent_id = None

    await db.flush()
    await db.refresh(project)

    # Get parent name
    parent_name = None
    if project.parent_id:
        parent_result = await db.execute(select(Project.name).where(Project.id == project.parent_id))
        parent_name = parent_result.scalar()

    # Get children
    children = await get_child_previews(db, project.id)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        notes=project.notes,
        attachments=project.attachments,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=parent_name,
        children=children,
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_DELETE),
):
    """Delete a project. Archives and queue items will have project_id set to NULL."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await db.delete(project)

    return {"message": "Project deleted"}


@router.get("/{project_id}/archives")
async def list_project_archives(
    project_id: int,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """List archives in a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Get archives with project relationship eagerly loaded
    query = (
        select(PrintArchive)
        .options(selectinload(PrintArchive.project))
        .where(PrintArchive.project_id == project_id)
        .order_by(PrintArchive.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    archives = result.scalars().all()

    # Import the response converter from archives module
    from backend.app.api.routes.archives import archive_to_response

    return [archive_to_response(a) for a in archives]


@router.get("/{project_id}/queue")
async def list_project_queue(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """List queue items in a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Get queue items
    query = select(PrintQueueItem).where(PrintQueueItem.project_id == project_id).order_by(PrintQueueItem.position)
    result = await db.execute(query)
    items = result.scalars().all()

    return items


@router.post("/{project_id}/add-archives")
async def add_archives_to_project(
    project_id: int,
    data: BatchAddArchives,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Batch add archives to a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Update archives
    updated = 0
    for archive_id in data.archive_ids:
        result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
        archive = result.scalar_one_or_none()
        if archive:
            archive.project_id = project_id
            updated += 1

    return {"message": f"Added {updated} archives to project"}


@router.post("/{project_id}/add-queue")
async def add_queue_items_to_project(
    project_id: int,
    data: BatchAddQueueItems,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Batch add queue items to a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Update queue items
    updated = 0
    for item_id in data.queue_item_ids:
        result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))
        item = result.scalar_one_or_none()
        if item:
            item.project_id = project_id
            updated += 1

    return {"message": f"Added {updated} queue items to project"}


@router.post("/{project_id}/remove-archives")
async def remove_archives_from_project(
    project_id: int,
    data: BatchAddArchives,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Remove archives from a project (sets project_id to NULL)."""
    updated = 0
    for archive_id in data.archive_ids:
        result = await db.execute(
            select(PrintArchive).where(
                PrintArchive.id == archive_id,
                PrintArchive.project_id == project_id,
            )
        )
        archive = result.scalar_one_or_none()
        if archive:
            archive.project_id = None
            updated += 1

    return {"message": f"Removed {updated} archives from project"}


def get_project_attachments_dir(project_id: int) -> Path:
    """Get the attachments directory for a project."""
    base_dir = Path(settings.archive_dir)
    return base_dir / "projects" / str(project_id) / "attachments"


# Allowed file extensions for attachments
ALLOWED_ATTACHMENT_EXTENSIONS = {
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".ico",
    # Documents
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".txt",
    ".rtf",
    ".csv",
    ".md",
    # 3D/CAD files
    ".stl",
    ".obj",
    ".3mf",
    ".step",
    ".stp",
    ".iges",
    ".igs",
    ".f3d",
    ".scad",
    # Archives
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    # Code/scripts (for Klipper macros, scripts, etc.)
    ".py",
    ".sh",
    ".cfg",
    ".conf",
    ".gcode",
    ".ini",
    # Other common formats
    ".json",
    ".xml",
    ".yaml",
    ".yml",
}


@router.post("/{project_id}/attachments")
async def upload_attachment(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Upload an attachment to a project."""
    logger.info("=== UPLOAD START: %s for project %s ===", file.filename, project_id)

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate file extension
    original_name = file.filename or "unknown"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not supported. Allowed: images, PDFs, documents, STL, 3MF, archives.",
        )

    # Create attachments directory
    attachments_dir = get_project_attachments_dir(project_id)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = attachments_dir / unique_filename

    # Save file
    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        logger.info("=== FILE SAVED: %s, size: %s ===", file_path, len(content))
    except Exception as e:
        logger.error("Failed to save attachment: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save attachment")

    # Update project attachments JSON
    attachments = list(project.attachments or [])
    new_attachment = {
        "filename": unique_filename,
        "original_name": original_name,
        "size": len(content),
        "uploaded_at": datetime.now().isoformat(),
    }
    attachments.append(new_attachment)

    # Simple ORM update
    project.attachments = attachments
    db.add(project)  # Explicitly add to session

    logger.info("=== BEFORE COMMIT: %s attachments ===", len(attachments))

    await db.flush()
    await db.commit()

    logger.info("=== AFTER COMMIT ===")

    # Verify by re-querying
    result = await db.execute(select(Project).where(Project.id == project_id))
    fresh_project = result.scalar_one()

    logger.info("=== VERIFIED: %s attachments ===", len(fresh_project.attachments or []))

    return {
        "status": "success",
        "filename": unique_filename,
        "original_name": original_name,
        "attachments": fresh_project.attachments,
    }


@router.get("/{project_id}/attachments/{filename}")
async def download_attachment(
    project_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """Download an attachment from a project."""
    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename or not filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Verify attachment exists in project
    attachments = project.attachments or []
    attachment = next((a for a in attachments if a.get("filename") == filename), None)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Check file exists
    file_path = get_project_attachments_dir(project_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file not found")

    return FileResponse(
        file_path,
        filename=attachment.get("original_name", filename),
        media_type="application/octet-stream",
    )


@router.delete("/{project_id}/attachments/{filename}")
async def delete_attachment(
    project_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Delete an attachment from a project."""
    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename or not filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Find and remove attachment from list
    attachments = project.attachments or []
    attachment = next((a for a in attachments if a.get("filename") == filename), None)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Remove from list
    attachments = [a for a in attachments if a.get("filename") != filename]
    project.attachments = attachments if attachments else None

    # Delete file
    file_path = get_project_attachments_dir(project_id) / filename
    if file_path.exists():
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning("Failed to delete attachment file: %s", e)

    await db.flush()
    await db.refresh(project)

    return {
        "status": "success",
        "message": "Attachment deleted",
        "attachments": project.attachments,
    }


# ============ Phase 7: BOM Endpoints ============


@router.get("/{project_id}/bom", response_model=list[BOMItemResponse])
async def list_bom_items(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """List all BOM items for a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Get BOM items
    result = await db.execute(
        select(ProjectBOMItem)
        .where(ProjectBOMItem.project_id == project_id)
        .order_by(ProjectBOMItem.sort_order, ProjectBOMItem.id)
    )
    items = result.scalars().all()

    response = []
    for item in items:
        # Get archive name if linked
        archive_name = None
        if item.archive_id:
            archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == item.archive_id))
            archive_name = archive_result.scalar()

        response.append(
            BOMItemResponse(
                id=item.id,
                project_id=item.project_id,
                name=item.name,
                quantity_needed=item.quantity_needed,
                quantity_acquired=item.quantity_acquired,
                unit_price=item.unit_price,
                sourcing_url=item.sourcing_url,
                archive_id=item.archive_id,
                archive_name=archive_name,
                stl_filename=item.stl_filename,
                remarks=item.remarks,
                sort_order=item.sort_order,
                is_complete=item.quantity_acquired >= item.quantity_needed,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )

    return response


@router.post("/{project_id}/bom", response_model=BOMItemResponse)
async def create_bom_item(
    project_id: int,
    data: BOMItemCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Add a BOM item to a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Get max sort order
    max_order_result = await db.execute(
        select(func.max(ProjectBOMItem.sort_order)).where(ProjectBOMItem.project_id == project_id)
    )
    max_order = max_order_result.scalar() or 0

    item = ProjectBOMItem(
        project_id=project_id,
        name=data.name,
        quantity_needed=data.quantity_needed,
        unit_price=data.unit_price,
        sourcing_url=data.sourcing_url,
        archive_id=data.archive_id,
        stl_filename=data.stl_filename,
        remarks=data.remarks,
        sort_order=max_order + 1,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)

    # Get archive name if linked
    archive_name = None
    if item.archive_id:
        archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == item.archive_id))
        archive_name = archive_result.scalar()

    return BOMItemResponse(
        id=item.id,
        project_id=item.project_id,
        name=item.name,
        quantity_needed=item.quantity_needed,
        quantity_acquired=item.quantity_acquired,
        unit_price=item.unit_price,
        sourcing_url=item.sourcing_url,
        archive_id=item.archive_id,
        archive_name=archive_name,
        stl_filename=item.stl_filename,
        remarks=item.remarks,
        sort_order=item.sort_order,
        is_complete=item.quantity_acquired >= item.quantity_needed,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.patch("/{project_id}/bom/{item_id}", response_model=BOMItemResponse)
async def update_bom_item(
    project_id: int,
    item_id: int,
    data: BOMItemUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Update a BOM item."""
    result = await db.execute(
        select(ProjectBOMItem).where(
            ProjectBOMItem.id == item_id,
            ProjectBOMItem.project_id == project_id,
        )
    )
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="BOM item not found")

    if data.name is not None:
        item.name = data.name
    if data.quantity_needed is not None:
        item.quantity_needed = data.quantity_needed
    if data.quantity_acquired is not None:
        item.quantity_acquired = data.quantity_acquired
    if data.unit_price is not None:
        item.unit_price = data.unit_price if data.unit_price != 0 else None
    if data.sourcing_url is not None:
        item.sourcing_url = data.sourcing_url if data.sourcing_url else None
    if data.archive_id is not None:
        item.archive_id = data.archive_id if data.archive_id != 0 else None
    if data.stl_filename is not None:
        item.stl_filename = data.stl_filename if data.stl_filename else None
    if data.remarks is not None:
        item.remarks = data.remarks if data.remarks else None

    await db.flush()
    await db.refresh(item)

    # Get archive name if linked
    archive_name = None
    if item.archive_id:
        archive_result = await db.execute(select(PrintArchive.print_name).where(PrintArchive.id == item.archive_id))
        archive_name = archive_result.scalar()

    return BOMItemResponse(
        id=item.id,
        project_id=item.project_id,
        name=item.name,
        quantity_needed=item.quantity_needed,
        quantity_acquired=item.quantity_acquired,
        unit_price=item.unit_price,
        sourcing_url=item.sourcing_url,
        archive_id=item.archive_id,
        archive_name=archive_name,
        stl_filename=item.stl_filename,
        remarks=item.remarks,
        sort_order=item.sort_order,
        is_complete=item.quantity_acquired >= item.quantity_needed,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.delete("/{project_id}/bom/{item_id}")
async def delete_bom_item(
    project_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_UPDATE),
):
    """Delete a BOM item."""
    result = await db.execute(
        select(ProjectBOMItem).where(
            ProjectBOMItem.id == item_id,
            ProjectBOMItem.project_id == project_id,
        )
    )
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="BOM item not found")

    await db.delete(item)

    return {"status": "success", "message": "BOM item deleted"}


@router.post("/{project_id}/create-template", response_model=ProjectResponse)
async def create_template_from_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Create a template from an existing project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    source = result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Project not found")

    # Create template
    template = Project(
        name=f"{source.name} (Template)",
        description=source.description,
        color=source.color,
        target_count=source.target_count,
        target_parts_count=source.target_parts_count,
        notes=source.notes,
        tags=source.tags,
        priority=source.priority,
        budget=source.budget,
        is_template=True,
        template_source_id=source.id,
    )
    db.add(template)
    await db.flush()

    # Copy BOM items
    bom_result = await db.execute(select(ProjectBOMItem).where(ProjectBOMItem.project_id == project_id))
    bom_items = bom_result.scalars().all()

    for item in bom_items:
        new_item = ProjectBOMItem(
            project_id=template.id,
            name=item.name,
            quantity_needed=item.quantity_needed,
            quantity_acquired=0,
            unit_price=item.unit_price,
            sourcing_url=item.sourcing_url,
            stl_filename=item.stl_filename,
            remarks=item.remarks,
            sort_order=item.sort_order,
        )
        db.add(new_item)

    await db.flush()
    await db.refresh(template)

    stats = await compute_project_stats(db, template.id, template.target_count, template.target_parts_count)

    return ProjectResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        color=template.color,
        status=template.status,
        target_count=template.target_count,
        target_parts_count=template.target_parts_count,
        notes=template.notes,
        attachments=template.attachments,
        tags=template.tags,
        due_date=template.due_date,
        priority=template.priority,
        budget=template.budget,
        is_template=template.is_template,
        template_source_id=template.template_source_id,
        parent_id=template.parent_id,
        parent_name=None,
        children=[],
        created_at=template.created_at,
        updated_at=template.updated_at,
        stats=stats,
    )


# ============ Phase 9: Timeline Endpoint ============


@router.get("/{project_id}/timeline", response_model=list[TimelineEvent])
async def get_project_timeline(
    project_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """Get timeline of events for a project."""
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    events = []

    # Project creation event
    events.append(
        TimelineEvent(
            event_type="project_created",
            timestamp=project.created_at,
            title="Project created",
            description=f"Project '{project.name}' was created",
        )
    )

    # Get archives and add events
    archives_result = await db.execute(
        select(PrintArchive)
        .where(PrintArchive.project_id == project_id)
        .order_by(PrintArchive.created_at.desc())
        .limit(limit)
    )
    archives = archives_result.scalars().all()

    for archive in archives:
        if archive.status == "completed":
            events.append(
                TimelineEvent(
                    event_type="print_completed",
                    timestamp=archive.completed_at or archive.created_at,
                    title="Print completed",
                    description=archive.print_name,
                    metadata={
                        "archive_id": archive.id,
                        "print_time_hours": round((archive.print_time_seconds or 0) / 3600, 2),
                        "filament_grams": round(archive.filament_used_grams or 0, 1),
                    },
                )
            )
        elif archive.status == "failed":
            events.append(
                TimelineEvent(
                    event_type="print_failed",
                    timestamp=archive.completed_at or archive.created_at,
                    title="Print failed",
                    description=archive.print_name,
                    metadata={"archive_id": archive.id},
                )
            )

    # Get queue items
    queue_result = await db.execute(
        select(PrintQueueItem)
        .where(PrintQueueItem.project_id == project_id)
        .order_by(PrintQueueItem.created_at.desc())
        .limit(limit)
    )
    queue_items = queue_result.scalars().all()

    for item in queue_items:
        if item.status == "printing":
            events.append(
                TimelineEvent(
                    event_type="print_started",
                    timestamp=item.started_at or item.created_at,
                    title="Print started",
                    description=item.print_name,
                    metadata={"queue_item_id": item.id},
                )
            )
        elif item.status == "pending":
            events.append(
                TimelineEvent(
                    event_type="queued",
                    timestamp=item.created_at,
                    title="Added to queue",
                    description=item.print_name,
                    metadata={"queue_item_id": item.id},
                )
            )

    # Sort by timestamp descending
    events.sort(key=lambda e: e.timestamp, reverse=True)

    return events[:limit]


# ============ Phase 10: Import/Export Endpoints ============


@router.get("/{project_id}/export")
async def export_project(
    project_id: int,
    format: str = "zip",  # "zip" (with files) or "json" (metadata only)
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_READ),
):
    """Export a project. Use format=zip (default) for full export with files, or format=json for metadata only."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get BOM items
    bom_result = await db.execute(
        select(ProjectBOMItem).where(ProjectBOMItem.project_id == project_id).order_by(ProjectBOMItem.sort_order)
    )
    bom_items = bom_result.scalars().all()

    bom_export = [
        {
            "name": item.name,
            "quantity_needed": item.quantity_needed,
            "quantity_acquired": item.quantity_acquired,
            "unit_price": item.unit_price,
            "sourcing_url": item.sourcing_url,
            "stl_filename": item.stl_filename,
            "remarks": item.remarks,
        }
        for item in bom_items
    ]

    # Get linked folders and their files
    folders_result = await db.execute(
        select(LibraryFolder).where(LibraryFolder.project_id == project_id).order_by(LibraryFolder.name)
    )
    linked_folders = folders_result.scalars().all()

    folders_export = []
    files_to_include = []  # (archive_path, zip_path)

    for folder in linked_folders:
        # Get files in this folder
        files_result = await db.execute(
            select(LibraryFile).where(LibraryFile.folder_id == folder.id).order_by(LibraryFile.filename)
        )
        files = files_result.scalars().all()

        folder_files = []
        for f in files:
            folder_files.append(
                {
                    "filename": f.filename,
                    "file_type": f.file_type,
                    "notes": f.notes,
                }
            )
            # Add file to include in ZIP
            library_dir = get_library_dir()
            file_path = library_dir / f.file_path
            if file_path.exists():
                zip_path = f"files/{folder.name}/{f.filename}"
                files_to_include.append((file_path, zip_path))
                # Also include thumbnail if exists
                if f.thumbnail_path:
                    thumb_path = library_dir / f.thumbnail_path
                    if thumb_path.exists():
                        thumb_zip_path = f"files/{folder.name}/.thumbnails/{f.filename}.png"
                        files_to_include.append((thumb_path, thumb_zip_path))

        folders_export.append(
            {
                "name": folder.name,
                "files": folder_files,
            }
        )

    # Build project JSON
    project_data = {
        "name": project.name,
        "description": project.description,
        "color": project.color,
        "status": project.status,
        "target_count": project.target_count,
        "target_parts_count": project.target_parts_count,
        "notes": project.notes,
        "tags": project.tags,
        "due_date": project.due_date.isoformat() if project.due_date else None,
        "priority": project.priority,
        "budget": project.budget,
        "bom_items": bom_export,
        "linked_folders": folders_export,
    }

    # Return JSON if requested (for bulk export)
    if format == "json":
        return project_data

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add project.json
        zf.writestr("project.json", json.dumps(project_data, indent=2))

        # Add files
        for file_path, zip_path in files_to_include:
            zf.write(file_path, zip_path)

    zip_buffer.seek(0)

    # Generate filename
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in project.name)
    filename = f"{safe_name}_{datetime.now().strftime('%Y-%m-%d')}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=ProjectResponse)
async def import_project(
    data: ProjectImport,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Import a project with optional BOM items and linked folders."""
    # Create the project
    project = Project(
        name=data.name,
        description=data.description,
        color=data.color,
        status=data.status,
        target_count=data.target_count,
        target_parts_count=data.target_parts_count,
        notes=data.notes,
        tags=data.tags,
        due_date=data.due_date,
        priority=data.priority,
        budget=data.budget,
    )
    db.add(project)
    await db.flush()

    # Create BOM items
    for idx, bom_data in enumerate(data.bom_items):
        bom_item = ProjectBOMItem(
            project_id=project.id,
            name=bom_data.name,
            quantity_needed=bom_data.quantity_needed,
            quantity_acquired=bom_data.quantity_acquired,
            unit_price=bom_data.unit_price,
            sourcing_url=bom_data.sourcing_url,
            stl_filename=bom_data.stl_filename,
            remarks=bom_data.remarks,
            sort_order=idx,
        )
        db.add(bom_item)

    # Create linked folders in library
    for folder_data in data.linked_folders:
        # Check if folder with this name already exists at root level
        existing_result = await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.name == folder_data.name,
                LibraryFolder.parent_id.is_(None),
            )
        )
        existing_folder = existing_result.scalar_one_or_none()

        if existing_folder:
            # Link existing folder to project
            existing_folder.project_id = project.id
        else:
            # Create new folder linked to project
            new_folder = LibraryFolder(
                name=folder_data.name,
                project_id=project.id,
                is_external=False,
                external_readonly=False,
                external_show_hidden=False,
            )
            db.add(new_folder)

    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        notes=project.notes,
        attachments=project.attachments,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=None,
        children=[],
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )


@router.post("/import/file", response_model=ProjectResponse)
async def import_project_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PROJECTS_CREATE),
):
    """Import a project from a ZIP or JSON file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Determine file type
    filename_lower = file.filename.lower()
    content = await file.read()

    if filename_lower.endswith(".zip"):
        # Extract project.json from ZIP
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if "project.json" not in zf.namelist():
                    raise HTTPException(status_code=400, detail="ZIP must contain project.json")
                project_json = zf.read("project.json")
                data = json.loads(project_json)

                # Get list of files in the ZIP
                zip_files = {name: zf.read(name) for name in zf.namelist() if name.startswith("files/")}
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid ZIP file")
    elif filename_lower.endswith(".json"):
        try:
            data = json.loads(content)
            zip_files = {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON file")
    else:
        raise HTTPException(status_code=400, detail="File must be .zip or .json")

    # Create the project
    project = Project(
        name=data.get("name", "Imported Project"),
        description=data.get("description"),
        color=data.get("color"),
        status=data.get("status", "active"),
        target_count=data.get("target_count"),
        target_parts_count=data.get("target_parts_count"),
        notes=data.get("notes"),
        tags=data.get("tags"),
        due_date=datetime.fromisoformat(data["due_date"]) if data.get("due_date") else None,
        priority=data.get("priority", 0),
        budget=data.get("budget"),
    )
    db.add(project)
    await db.flush()

    # Create BOM items
    for idx, bom_data in enumerate(data.get("bom_items", [])):
        bom_item = ProjectBOMItem(
            project_id=project.id,
            name=bom_data.get("name", "Unnamed"),
            quantity_needed=bom_data.get("quantity_needed", 1),
            quantity_acquired=bom_data.get("quantity_acquired", 0),
            unit_price=bom_data.get("unit_price"),
            sourcing_url=bom_data.get("sourcing_url"),
            stl_filename=bom_data.get("stl_filename"),
            remarks=bom_data.get("remarks"),
            sort_order=idx,
        )
        db.add(bom_item)

    # Create linked folders and files
    library_dir = get_library_dir()
    for folder_data in data.get("linked_folders", []):
        folder_name = folder_data.get("name")
        if not folder_name:
            continue

        # Check if folder exists
        existing_result = await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.name == folder_name,
                LibraryFolder.parent_id.is_(None),
            )
        )
        existing_folder = existing_result.scalar_one_or_none()

        if existing_folder:
            # Link existing folder to project
            existing_folder.project_id = project.id
            folder = existing_folder
        else:
            # Create new folder
            folder = LibraryFolder(
                name=folder_name,
                project_id=project.id,
                is_external=False,
                external_readonly=False,
                external_show_hidden=False,
            )
            db.add(folder)
            await db.flush()

            # Create folder on disk
            folder_path = library_dir / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)

        # Import files for this folder from ZIP
        folder_prefix = f"files/{folder_name}/"
        for zip_path, file_content in zip_files.items():
            if not zip_path.startswith(folder_prefix):
                continue
            if "/.thumbnails/" in zip_path:
                continue  # Skip thumbnails, we'll regenerate them

            relative_path = zip_path[len(folder_prefix) :]
            if not relative_path:
                continue

            # Write file to disk
            file_disk_path = library_dir / folder_name / relative_path
            file_disk_path.parent.mkdir(parents=True, exist_ok=True)
            file_disk_path.write_bytes(file_content)

            # Determine file type
            ext = Path(relative_path).suffix.lower()
            if ext in [".stl", ".3mf", ".obj"]:
                file_type = "model"
            elif ext in [".gcode"]:
                file_type = "gcode"
            elif ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                file_type = "image"
            else:
                file_type = "other"

            # Create library file record
            lib_file = LibraryFile(
                folder_id=folder.id,
                filename=relative_path,
                file_path=f"{folder_name}/{relative_path}",
                file_type=file_type,
                file_size=len(file_content),
                is_external=False,
            )
            db.add(lib_file)

    await db.flush()
    await db.refresh(project)

    stats = await compute_project_stats(db, project.id, project.target_count, project.target_parts_count)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        color=project.color,
        status=project.status,
        target_count=project.target_count,
        target_parts_count=project.target_parts_count,
        notes=project.notes,
        attachments=project.attachments,
        tags=project.tags,
        due_date=project.due_date,
        priority=project.priority,
        budget=project.budget,
        is_template=project.is_template,
        template_source_id=project.template_source_id,
        parent_id=project.parent_id,
        parent_name=None,
        children=[],
        created_at=project.created_at,
        updated_at=project.updated_at,
        stats=stats,
    )
