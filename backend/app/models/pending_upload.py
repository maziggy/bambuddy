"""Pending upload model for virtual printer queue mode."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PendingUpload(Base):
    """Pending upload from virtual printer awaiting user review."""

    __tablename__ = "pending_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)

    # File info
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))  # Temp storage path
    file_size: Mapped[int] = mapped_column(Integer)

    # Embedded 3MF Title metadata, captured at FTP-receive time so the review
    # card and the eventual archive's print_name agree on which name to show
    # (#1152 follow-up). NULL when the 3MF has no title or the metadata read
    # failed — the response model falls back to the filename stem in that case.
    metadata_print_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Source info
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Status: pending, archived, discarded
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # User additions (before archiving)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)

    # After archiving - link to created archive
    archived_id: Mapped[int | None] = mapped_column(ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    project: Mapped["Project | None"] = relationship()
    archive: Mapped["PrintArchive | None"] = relationship()


from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.project import Project  # noqa: E402
