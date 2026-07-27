"""Model for a Slicing/Printing Pipeline definition (#1425).

A pipeline bundles the four slot picks a user normally makes in the SliceModal
(printer / process / filament(s) / bed type) under a named, reusable preset.
This is PR A — bundle definitions only. Run state and dispatch live in
``pipeline_runs`` / ``pipeline_jobs`` (PR B + PR C).

The target_* and fanout_strategy columns are materialised now to avoid a
second migration when PR B / PR C land; PR A's API accepts the defaults and
the UI doesn't expose them yet.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class SlicerPipeline(Base):
    """A named slicer preset bundle (printer + process + filament[s] + bed)."""

    __tablename__ = "slicer_pipelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(1000))

    # Preset slots. ``*_source`` mirrors PresetRef.source semantics
    # (orca_cloud / cloud / local / standard); ``*_id`` is the opaque
    # source-specific id the slicer pipeline uses to resolve content.
    printer_preset_source: Mapped[str] = mapped_column(String(20))
    printer_preset_id: Mapped[str] = mapped_column(String(200))
    process_preset_source: Mapped[str] = mapped_column(String(20))
    process_preset_id: Mapped[str] = mapped_column(String(200))
    # JSON array of {"source": ..., "id": ...} entries — one per AMS slot the
    # source plate is expected to use. Stored as JSON text per Bambuddy's
    # convention (see LocalPreset.compatible_printers).
    filament_presets_json: Mapped[str] = mapped_column(Text)

    bed_type: Mapped[str | None] = mapped_column(String(64))

    # Target — PR B+ wiring; PR A treats every pipeline as a bundle without
    # an active target. Kept materialised so PR B is code-only, not a
    # migration. ``target_kind`` ∈ {"specific_printer", "printer_class"}.
    target_kind: Mapped[str] = mapped_column(String(20), default="printer_class")
    target_printer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("printers.id", ondelete="SET NULL"))
    target_model_class: Mapped[str | None] = mapped_column(String(20))

    # Fanout strategy for PR C multi-copy runs. PR A defaults it; the UI
    # doesn't expose it yet. Values: max_parallel / fill_one_first / round_robin.
    fanout_strategy: Mapped[str] = mapped_column(String(20), default="max_parallel")

    # Audit fields. created_by is nullable so pipelines survive user deletes
    # and so installs without auth enabled (current_user is None) still work.
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
