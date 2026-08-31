from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SpoolFilamentPreset(Base):
    """Per-printer-model override of a spool's slicer filament preset.

    ``Spool.slicer_filament`` holds ONE preset, and that is deliberate -- the
    spool form is printer-agnostic and the user picks the variant they want
    (see ``spool-form/utils.ts``). It stops being enough as soon as the same
    spool is used on two different printer models: a cloud or Orca preset is
    bound to a model (``@BBL X1C``), so the spool that carries an X1C variant
    configures an AMS slot on an H2C with a preset that machine has no profile
    for.

    Keyed on the printer MODEL, not the printer: ``@BBL X1C`` is the same
    preset on every X1C the user owns, and keying per machine would make them
    pick the identical value once per printer. (K profiles are the opposite --
    a K value is measured on one individual hotend -- which is why
    ``spool_k_profile`` keys on ``printer_id`` and this does not.)

    ``nozzle_diameter`` is part of the key because the preset lands on an AMS
    slot, and a slot feeds exactly one nozzle: on a dual-nozzle machine with
    two different diameters fitted, one preset per model cannot be right for
    both hotends, and diameter-specific presets genuinely exist
    (``Bambu PLA Basic @BBL A1M 0.2 nozzle``). Empty string means "any nozzle
    of this model". The spool form does not write that row -- it offers one row
    per nozzle size and nothing above them, because a preset lands on an AMS
    slot and a slot feeds exactly one nozzle -- but the level is kept in the
    cascade for API clients that want one value to cover a whole model.
    Resolution order is

        exact (model, diameter) -> (model, "") -> ``Spool.slicer_filament``

    which is what ``services.spool_filament_preset.resolve_spool_preset``
    implements. Empty string rather than NULL because NULLs compare distinct
    in a UNIQUE constraint on both SQLite and PostgreSQL, so a nullable
    column would happily store the same "any nozzle" row twice.
    """

    __tablename__ = "spool_filament_preset"

    __table_args__ = (UniqueConstraint("spool_id", "printer_model", "nozzle_diameter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(ForeignKey("spool.id", ondelete="CASCADE"), index=True)
    # Matches ``printers.model`` ("X1C", "H2D", "A1 mini"), not a display name.
    printer_model: Mapped[str] = mapped_column(String(50))
    # "" = any nozzle of this model; otherwise the bare decimal the printer
    # reports ("0.4", "0.2"), the same form ``spool_k_profile`` stores.
    nozzle_diameter: Mapped[str] = mapped_column(String(10), default="")
    # Wider than ``Spool.slicer_filament`` (String(50)) on purpose: the same
    # values reach the Spoolman path, whose write schema already allows 128 /
    # 255, and a preset id that fits there must not truncate here.
    slicer_filament: Mapped[str | None] = mapped_column(String(128))
    slicer_filament_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    spool: Mapped["Spool"] = relationship(back_populates="filament_presets")


class SpoolmanFilamentPreset(Base):
    """``SpoolFilamentPreset`` for a Spoolman-managed spool.

    Mirrors ``SpoolmanKProfile``: Spoolman owns the spool, Bambuddy owns this
    override, so the row is local and keyed by the remote spool id with no
    foreign key to enforce it. Kept in a Bambuddy table rather than in the
    spool's Spoolman ``extra`` dict for the same reason the K profiles are --
    it is Bambu-specific data that no other Spoolman client can use, and the
    extra dict cannot express a per-model list without hand-rolled JSON.
    """

    __tablename__ = "spoolman_filament_preset"

    __table_args__ = (UniqueConstraint("spoolman_spool_id", "printer_model", "nozzle_diameter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    spoolman_spool_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    printer_model: Mapped[str] = mapped_column(String(50))
    nozzle_diameter: Mapped[str] = mapped_column(String(10), default="")
    slicer_filament: Mapped[str | None] = mapped_column(String(128))
    slicer_filament_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


from backend.app.models.spool import Spool  # noqa: E402, F401
