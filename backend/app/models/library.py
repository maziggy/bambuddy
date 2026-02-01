"""Library models for file manager functionality."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class LibraryFolder(Base):
    """Folder for organizing library files."""

    __tablename__ = "library_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("library_folders.id", ondelete="CASCADE"), nullable=True)

    # External folder flags (for folders that point to external paths)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    external_readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    external_show_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    external_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Link to project or archive
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    archive_id: Mapped[int | None] = mapped_column(ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    parent: Mapped["LibraryFolder | None"] = relationship(
        "LibraryFolder",
        back_populates="children",
        remote_side="LibraryFolder.id",
        foreign_keys="LibraryFolder.parent_id",
    )
    children: Mapped[list["LibraryFolder"]] = relationship(
        "LibraryFolder",
        back_populates="parent",
        foreign_keys="LibraryFolder.parent_id",
        cascade="all, delete-orphan",
    )
    files: Mapped[list["LibraryFile"]] = relationship(
        back_populates="folder",
        cascade="all, delete-orphan",
    )
    project: Mapped["Project | None"] = relationship()
    archive: Mapped["PrintArchive | None"] = relationship()


class LibraryFile(Base):
    """File stored in the library."""

    __tablename__ = "library_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("library_folders.id", ondelete="CASCADE"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)

    # External file flag
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)

    # File info
    filename: Mapped[str] = mapped_column(String(255))  # Original filename
    file_path: Mapped[str] = mapped_column(String(500))  # Storage path
    file_type: Mapped[str] = mapped_column(String(10))  # "3mf" or "gcode"
    file_size: Mapped[int] = mapped_column(Integer)
    file_hash: Mapped[str | None] = mapped_column(String(64))  # SHA256 for duplicate detection
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))

    # Extracted metadata (from 3MF parser)
    file_metadata: Mapped[dict | None] = mapped_column(JSON)

    # Usage tracking
    print_count: Mapped[int] = mapped_column(Integer, default=0)
    last_printed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # User notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User tracking (Issue #206)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    folder: Mapped["LibraryFolder | None"] = relationship(back_populates="files")
    project: Mapped["Project | None"] = relationship()
    created_by: Mapped["User | None"] = relationship()


from backend.app.models.archive import PrintArchive  # noqa: E402, F811
from backend.app.models.project import Project  # noqa: E402, F811
from backend.app.models.user import User  # noqa: E402, F811
