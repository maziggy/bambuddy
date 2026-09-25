"""Uploads sent by the slicer plugin, and where each one came from."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SlicerPluginUpload(Base):
    """One upload from the slicer plugin, keyed by the id the plugin chose for it.

    Two jobs. It makes the upload idempotent: the plugin retries a send whose
    response it never saw, and a retry that queued the file a second time would
    print it twice. And it keeps what the file cannot say about itself -- which
    slicer and presets produced it, and a fingerprint of the model -- which is
    what lets a later slice of the same model find this one.
    """

    __tablename__ = "slicer_plugin_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Chosen by the plugin, one per send. Unique, so two requests carrying the
    # same id cannot both get past the claim in slicer_plugin.py.
    upload_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Who sent it: "user:<id>", "apikey:<id>", or "anonymous" with auth off. A
    # replay from anyone else is refused rather than answered with this
    # caller's result.
    caller: Mapped[str] = mapped_column(String(40))

    # "processing" while the request that claimed the id is still working,
    # "done" once `response` holds what it returned.
    status: Mapped[str] = mapped_column(String(20), default="processing")

    library_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_files.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Provenance, as the plugin reported it.
    slicer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slicer_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plugin_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    presets: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    # The JSON body the first request returned, sent back verbatim on a replay.
    response: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
