"""Library models for file manager functionality."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Select, String, Text, func, select
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

    # Real on-disk modification time of the directory this folder mirrors (#2680).
    # For external folders this is captured from ``os.stat().st_mtime`` on scan so
    # the tree's "sort by recent activity" matches ``ls -t`` instead of ordering by
    # the DB row's ``updated_at`` (which is the scan instant, identical for every
    # row of a bulk scan). Null for managed (internal) folders, which have no
    # meaningful directory mtime — callers fall back to ``updated_at``/``created_at``.
    fs_modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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


class FileVariantGroup(Base):
    """A set of library files that are the same job sliced for different printers.

    Members are peers, not a source/output hierarchy. The group answers one
    question — "which of these files goes to an H2S, and which to an H2C" — and
    both open features need that answer from opposite ends: the print queue
    picks the printer and needs the matching file (#671), the File Manager's
    print action has the printer already and needs the same match (#2570).

    The group deliberately stores no model information of its own. Each
    member's target model comes from its own ``file_metadata['sliced_for_model']``,
    parsed out of the 3MF, so a group can never disagree with the files it
    contains. It also carries no pointer to an unsliced source file: that is a
    display concern for the grouped File Manager listing, which is not built.

    Deleting a group ungroups its files rather than deleting them (the member
    side is ON DELETE SET NULL) — every member is independently printable.
    """

    __tablename__ = "file_variant_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    files: Mapped[list["LibraryFile"]] = relationship(
        back_populates="variant_group",
        order_by="LibraryFile.variant_position",
    )
    created_by: Mapped["User | None"] = relationship()


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

    # Provenance — when the file was imported from an external source (e.g.
    # MakerWorld), ``source_type`` identifies the source and ``source_url`` is
    # the canonical public URL. Used for "already imported" detection and
    # "re-open on MakerWorld" affordances. Index on source_url so the
    # dedupe lookup is O(log N).
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)

    # Variant grouping (#671 / #2570). A file belongs to at most one group of
    # "same job, sliced for a different printer" siblings. SET NULL on group
    # delete: ungrouping must never take the files with it. ``variant_position``
    # is the user's priority order within the group — when two printers are idle
    # at the same scheduler tick, the lowest position wins, so the pick is
    # reproducible instead of depending on which match the scheduler found first.
    variant_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("file_variant_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    variant_position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # User's answer to "which printer is this for", for a file that does not say.
    # Files imported before Bambuddy parsed ``sliced_for_model`` — and raw .gcode —
    # declare nothing, and without this they could never be grouped. Deliberately
    # NOT written into ``file_metadata``: that holds what was parsed out of the
    # file, and a user's assertion must not become indistinguishable from it.
    variant_target_model: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # User tracking (Issue #206)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Soft-delete / trash bin (Issue #1008). When non-null, the file is in the
    # trash and should not appear in normal listings. A background sweeper
    # hard-deletes rows whose deleted_at is older than the retention window.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Real on-disk modification time of the file (#2680). Captured from
    # ``os.stat().st_mtime`` for external files on scan so the file pane's date
    # sort and the folder tree's recursive "recent activity" bubble reflect the
    # actual filesystem mtime (``ls -t``) rather than the DB ``updated_at`` (the
    # scan instant, identical across a bulk scan). Null for managed uploads —
    # callers fall back to ``created_at``.
    fs_modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    folder: Mapped["LibraryFolder | None"] = relationship(back_populates="files")
    project: Mapped["Project | None"] = relationship()
    created_by: Mapped["User | None"] = relationship()
    variant_group: Mapped["FileVariantGroup | None"] = relationship(back_populates="files")
    # Tags (#1268). M2M via library_file_tags. Loaded explicitly via
    # ``selectinload`` in list_files so each row in the listing carries its
    # chip set without N+1 fetches.
    tags: Mapped[list["LibraryTag"]] = relationship(
        secondary="library_file_tags",
        back_populates="files",
    )

    @classmethod
    def active(cls) -> "Select[tuple[LibraryFile]]":
        """Select statement that excludes trashed (soft-deleted) files.

        Use this in place of ``select(LibraryFile)`` for any user-facing listing
        or lookup so trashed files don't leak into normal flows. Endpoints that
        specifically operate on trashed rows (trash list, restore, sweeper)
        must use ``select(LibraryFile)`` directly.
        """
        return select(cls).where(cls.deleted_at.is_(None))


class LibraryTag(Base):
    """User-authored cross-cutting label for library files (#1268).

    Folders express hierarchy; tags express orthogonal attributes ("toy",
    "kid-safe", "petg-only"). Catalog is global (one tag set per install)
    — the multi-user "private tags" case is not in v1 scope. ``name_key``
    is ``LOWER(TRIM(name))`` so "Toys" / "toys" / "  TOYS  " all collide
    on the UNIQUE index and the route returns 409 instead of silently
    creating a duplicate.
    """

    __tablename__ = "library_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    name_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    files: Mapped[list["LibraryFile"]] = relationship(
        secondary="library_file_tags",
        back_populates="tags",
    )


class LibraryFileTag(Base):
    """Association between library files and tags (#1268).

    Composite PK so the same (file, tag) pair can't be inserted twice. Both
    sides ON DELETE CASCADE: deleting a tag drops every association row,
    deleting a file drops its tag links, and the catalog row survives so
    other files keep their chip.
    """

    __tablename__ = "library_file_tags"

    file_id: Mapped[int] = mapped_column(ForeignKey("library_files.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("library_tags.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


from backend.app.models.archive import PrintArchive  # noqa: E402, F811
from backend.app.models.project import Project  # noqa: E402, F811
from backend.app.models.user import User  # noqa: E402, F811
