"""Pydantic schemas for library (File Manager) functionality."""

from datetime import datetime

from pydantic import BaseModel, Field

# ============ Folder Schemas ============


class FolderCreate(BaseModel):
    """Schema for creating a new folder."""

    name: str = Field(..., min_length=1, max_length=255)
    parent_id: int | None = None
    project_id: int | None = None
    archive_id: int | None = None


class FolderUpdate(BaseModel):
    """Schema for updating a folder."""

    name: str | None = Field(None, min_length=1, max_length=255)
    parent_id: int | None = None
    project_id: int | None = None  # 0 to unlink
    archive_id: int | None = None  # 0 to unlink


class FolderResponse(BaseModel):
    """Schema for folder response."""

    id: int
    name: str
    parent_id: int | None
    project_id: int | None = None
    archive_id: int | None = None
    project_name: str | None = None
    archive_name: str | None = None
    file_count: int = 0  # Computed field

    # External folder fields
    is_external: bool = False
    external_path: str | None = None
    external_readonly: bool = True
    external_show_hidden: bool = False
    external_extensions: str | None = None
    external_last_scan: datetime | None = None
    external_accessible: bool = True  # Computed: whether path exists

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FolderTreeItem(BaseModel):
    """Schema for folder tree item (includes children)."""

    id: int
    name: str
    parent_id: int | None
    project_id: int | None = None
    archive_id: int | None = None
    project_name: str | None = None
    archive_name: str | None = None
    file_count: int = 0

    # External folder fields
    is_external: bool = False
    external_path: str | None = None
    external_readonly: bool = True
    external_show_hidden: bool = False
    external_extensions: str | None = None
    external_last_scan: datetime | None = None
    external_accessible: bool = True

    children: list["FolderTreeItem"] = []

    class Config:
        from_attributes = True


# ============ File Schemas ============


class FileCreate(BaseModel):
    """Schema for creating a file entry (internal use after upload)."""

    filename: str
    file_path: str
    file_type: str
    file_size: int
    file_hash: str | None = None
    thumbnail_path: str | None = None
    metadata: dict | None = None
    folder_id: int | None = None
    project_id: int | None = None


class FileUpdate(BaseModel):
    """Schema for updating a file."""

    filename: str | None = Field(None, min_length=1, max_length=255)
    folder_id: int | None = None
    project_id: int | None = None
    notes: str | None = None


class FileDuplicate(BaseModel):
    """Reference to a duplicate file."""

    id: int
    filename: str
    folder_id: int | None
    folder_name: str | None
    created_at: datetime


class FileResponse(BaseModel):
    """Schema for file response."""

    id: int
    folder_id: int | None
    folder_name: str | None = None
    project_id: int | None
    project_name: str | None = None

    filename: str
    file_path: str
    file_type: str
    file_size: int
    file_hash: str | None
    thumbnail_path: str | None

    metadata: dict | None

    print_count: int
    last_printed_at: datetime | None

    notes: str | None

    # External file support
    is_external: bool = False
    external_mtime: int | None = None

    # Duplicate detection
    duplicates: list[FileDuplicate] | None = None
    duplicate_count: int = 0

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FileListResponse(BaseModel):
    """Schema for file list item (lighter than full response)."""

    id: int
    folder_id: int | None
    filename: str
    file_type: str
    file_size: int
    thumbnail_path: str | None
    print_count: int
    duplicate_count: int = 0
    created_at: datetime

    # Key metadata fields for display
    print_name: str | None = None
    print_time_seconds: int | None = None
    filament_used_grams: float | None = None

    class Config:
        from_attributes = True


class FileMoveRequest(BaseModel):
    """Schema for moving files to a folder."""

    file_ids: list[int]
    folder_id: int | None = None  # None = move to root


class FilePrintRequest(BaseModel):
    """Schema for printing a file from the library.

    Note: printer_id is passed as a query parameter, not in the body.
    """

    # Print options (same as archive reprint)
    plate_id: int | None = None
    ams_mapping: list[int] | None = None
    bed_levelling: bool = True
    flow_cali: bool = False
    vibration_cali: bool = True
    layer_inspect: bool = False
    timelapse: bool = False
    use_ams: bool = True


class FileUploadResponse(BaseModel):
    """Schema for file upload response."""

    id: int
    filename: str
    file_type: str
    file_size: int
    thumbnail_path: str | None
    duplicate_of: int | None = None  # ID of existing file with same hash
    metadata: dict | None = None


# ============ Bulk Operations ============


class BulkDeleteRequest(BaseModel):
    """Schema for bulk delete operations."""

    file_ids: list[int] = []
    folder_ids: list[int] = []


class BulkDeleteResponse(BaseModel):
    """Schema for bulk delete response."""

    deleted_files: int
    deleted_folders: int


# ============ Queue Operations ============


class AddToQueueRequest(BaseModel):
    """Schema for adding library files to the print queue."""

    file_ids: list[int] = Field(..., min_length=1)


class AddToQueueResult(BaseModel):
    """Result for a single file added to queue."""

    file_id: int
    filename: str
    queue_item_id: int


class AddToQueueError(BaseModel):
    """Error for a file that couldn't be added to queue."""

    file_id: int
    filename: str
    error: str


class AddToQueueResponse(BaseModel):
    """Schema for add-to-queue response."""

    added: list[AddToQueueResult]
    errors: list[AddToQueueError]


# ============ External Folder Schemas ============


class ExternalFolderCreate(BaseModel):
    """Create external folder mount."""

    name: str = Field(..., min_length=1, max_length=255)
    external_path: str = Field(..., description="Absolute container path to external directory")
    parent_id: int | None = None
    external_readonly: bool = True
    external_show_hidden: bool = False
    external_extensions: str | None = Field(
        None,
        description="Comma-separated extensions (e.g., '.3mf,.stl,.gcode'). None = all files"
    )


class ExternalFolderUpdate(BaseModel):
    """Update external folder settings (not path)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    external_readonly: bool | None = None
    external_show_hidden: bool | None = None
    external_extensions: str | None = None


class ExternalFolderValidateRequest(BaseModel):
    """Request to validate external path."""

    path: str


class ExternalFolderValidateResponse(BaseModel):
    """Path validation result."""

    valid: bool
    error: str | None = None
    file_count: int | None = None
    accessible: bool = True
    directory_size_mb: float | None = None


class ExternalFolderScanResponse(BaseModel):
    """Folder scan results."""

    added: int
    updated: int
    removed: int
    scan_duration_seconds: float
    last_scan: datetime


# ============ ZIP Extraction ============


class ZipExtractResult(BaseModel):
    """Result for a single file extracted from ZIP."""

    filename: str
    file_id: int
    folder_id: int | None = None


class ZipExtractError(BaseModel):
    """Error for a file that couldn't be extracted."""

    filename: str
    error: str


class ZipExtractResponse(BaseModel):
    """Schema for ZIP extraction response."""

    extracted: int
    folders_created: int
    files: list[ZipExtractResult]
    errors: list[ZipExtractError]
