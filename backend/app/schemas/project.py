from datetime import datetime

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    """Schema for creating a new project."""

    name: str
    description: str | None = None
    color: str | None = None
    target_count: int | None = None
    target_parts_count: int | None = None
    notes: str | None = None
    tags: str | None = None
    due_date: datetime | None = None
    priority: str = "normal"
    budget: float | None = None
    parent_id: int | None = None  # For sub-projects


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""

    name: str | None = None
    description: str | None = None
    color: str | None = None
    status: str | None = None  # active, completed, archived
    target_count: int | None = None
    target_parts_count: int | None = None
    notes: str | None = None
    tags: str | None = None
    due_date: datetime | None = None
    priority: str | None = None
    budget: float | None = None
    parent_id: int | None = None


class ProjectStats(BaseModel):
    """Statistics for a project."""

    total_archives: int = 0  # Number of archive records
    total_items: int = 0  # Sum of quantities (total items printed)
    completed_prints: int = 0  # Sum of quantities for completed prints
    failed_prints: int = 0  # Sum of quantities for failed prints
    queued_prints: int = 0
    in_progress_prints: int = 0
    total_print_time_hours: float = 0.0
    total_filament_grams: float = 0.0
    progress_percent: float | None = None  # Based on target_count (plates)
    parts_progress_percent: float | None = None  # Based on target_parts_count
    # Cost tracking (Phase 6)
    estimated_cost: float = 0.0  # Based on filament cost
    total_energy_kwh: float = 0.0
    total_energy_cost: float = 0.0
    remaining_prints: int | None = None  # target_count - total_archives
    remaining_parts: int | None = None  # target_parts_count - completed_prints
    # BOM stats (Phase 7)
    bom_total_items: int = 0
    bom_completed_items: int = 0


class ProjectChildPreview(BaseModel):
    """Minimal project data for child preview."""

    id: int
    name: str
    color: str | None
    status: str
    progress_percent: float | None = None


class ProjectResponse(BaseModel):
    """Schema for project response."""

    id: int
    name: str
    description: str | None
    color: str | None
    status: str
    target_count: int | None
    target_parts_count: int | None = None
    notes: str | None = None
    attachments: list | None = None
    tags: str | None = None
    due_date: datetime | None = None
    priority: str = "normal"
    budget: float | None = None
    is_template: bool = False
    template_source_id: int | None = None
    parent_id: int | None = None
    parent_name: str | None = None  # For display
    children: list[ProjectChildPreview] = []
    created_at: datetime
    updated_at: datetime
    stats: ProjectStats | None = None

    class Config:
        from_attributes = True


class ArchivePreview(BaseModel):
    """Minimal archive data for project preview."""

    id: int
    print_name: str | None
    thumbnail_path: str | None
    status: str
    filament_type: str | None = None
    filament_color: str | None = None


class ProjectListResponse(BaseModel):
    """Schema for project list item (lighter weight)."""

    id: int
    name: str
    description: str | None
    color: str | None
    status: str
    target_count: int | None
    target_parts_count: int | None = None
    created_at: datetime
    # Quick stats
    archive_count: int = 0  # Number of print jobs
    total_items: int = 0  # Sum of quantities (total items printed, including failed)
    completed_count: int = 0  # Sum of quantities for completed prints only
    failed_count: int = 0  # Sum of quantities for failed prints
    queue_count: int = 0
    progress_percent: float | None = None
    # Preview of archives (up to 5)
    archives: list[ArchivePreview] = []

    class Config:
        from_attributes = True


class BatchAddArchives(BaseModel):
    """Schema for batch adding archives to a project."""

    archive_ids: list[int]


class BatchAddQueueItems(BaseModel):
    """Schema for batch adding queue items to a project."""

    queue_item_ids: list[int]


# Phase 7: BOM Schemas - Tracks sourced/purchased parts
class BOMItemCreate(BaseModel):
    """Schema for creating a BOM item."""

    name: str
    quantity_needed: int = 1
    unit_price: float | None = None
    sourcing_url: str | None = None
    archive_id: int | None = None
    stl_filename: str | None = None
    remarks: str | None = None


class BOMItemUpdate(BaseModel):
    """Schema for updating a BOM item."""

    name: str | None = None
    quantity_needed: int | None = None
    quantity_acquired: int | None = None
    unit_price: float | None = None
    sourcing_url: str | None = None
    archive_id: int | None = None
    stl_filename: str | None = None
    remarks: str | None = None


class BOMItemResponse(BaseModel):
    """Schema for BOM item response."""

    id: int
    project_id: int
    name: str
    quantity_needed: int
    quantity_acquired: int
    unit_price: float | None
    sourcing_url: str | None
    archive_id: int | None
    archive_name: str | None = None
    stl_filename: str | None
    remarks: str | None
    sort_order: int
    is_complete: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Phase 9: Timeline Schemas
class TimelineEvent(BaseModel):
    """Schema for a timeline event."""

    event_type: str  # archive_added, queue_started, queue_completed, status_changed, note_updated
    timestamp: datetime
    title: str
    description: str | None = None
    metadata: dict | None = None  # Additional event-specific data


# Phase 10: Import/Export Schemas
class BOMItemExport(BaseModel):
    """Schema for exporting a BOM item."""

    name: str
    quantity_needed: int
    quantity_acquired: int
    unit_price: float | None
    sourcing_url: str | None
    stl_filename: str | None
    remarks: str | None


class LinkedFolderExport(BaseModel):
    """Schema for exporting a linked library folder."""

    name: str


class ProjectExport(BaseModel):
    """Schema for exporting a project."""

    name: str
    description: str | None
    color: str | None
    status: str
    target_count: int | None
    target_parts_count: int | None
    notes: str | None
    tags: str | None
    due_date: datetime | None
    priority: str
    budget: float | None
    bom_items: list[BOMItemExport] = []
    linked_folders: list[LinkedFolderExport] = []


class ProjectImport(BaseModel):
    """Schema for importing a project."""

    name: str
    description: str | None = None
    color: str | None = None
    status: str = "active"
    target_count: int | None = None
    target_parts_count: int | None = None
    notes: str | None = None
    tags: str | None = None
    due_date: datetime | None = None
    priority: str = "normal"
    budget: float | None = None
    bom_items: list[BOMItemExport] = []
    linked_folders: list[LinkedFolderExport] = []
