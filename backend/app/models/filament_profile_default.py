from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class FilamentProfileDefault(Base):
    """The profiles a new spool of one product starts with (#1326).

    A spool's slicer preset, per-model preset overrides and K profiles describe
    the filament, not the roll, so the replacement for an empty spool wants the
    same ones. A default holds them once per product, keyed on the
    (brand, material, subtype, color_name) strings the spool carries, and every
    create path copies them onto the new spool -- a copy, not a link: editing a
    default later leaves existing spools alone.

    ``color_name`` "" is the default for every colour of the product; a row
    with a colour wins over it, per preset and per hotend (see
    ``services.filament_profile_default.resolve_profile_default``), so the
    first spool of a new colour still gets the product's K profiles and a
    colour calibrated on its own keeps its values. Empty strings rather than
    NULL for the same reason as ``spool_filament_preset.nozzle_diameter``:
    NULLs compare distinct in a UNIQUE constraint on both databases.

    Shared by both inventory modes: the key is the product, which a Spoolman
    spool carries as well as an internal one.
    """

    __tablename__ = "filament_profile_default"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The four fields folded in Python (services.filament_profile_default.match_key),
    # not in SQL: SQLite's lower() folds ASCII only, so "Красный" would never
    # match its own row there -- the same reason Supplier.name_key exists.
    match_key: Mapped[str] = mapped_column(String(320), unique=True)
    brand: Mapped[str] = mapped_column(String(100), default="")
    material: Mapped[str] = mapped_column(String(50))
    subtype: Mapped[str] = mapped_column(String(50), default="")
    color_name: Mapped[str] = mapped_column(String(100), default="")
    # Lengths of the Spoolman write schema, the wider of the two inventories.
    slicer_filament: Mapped[str | None] = mapped_column(String(128))
    slicer_filament_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    presets: Mapped[list["FilamentProfileDefaultPreset"]] = relationship(
        back_populates="profile_default", cascade="all, delete-orphan"
    )
    k_profiles: Mapped[list["FilamentProfileDefaultKProfile"]] = relationship(
        back_populates="profile_default", cascade="all, delete-orphan"
    )


class FilamentProfileDefaultPreset(Base):
    """A default's preset for one printer model and nozzle; the same row as
    ``SpoolFilamentPreset``."""

    __tablename__ = "filament_profile_default_preset"

    __table_args__ = (UniqueConstraint("profile_default_id", "printer_model", "nozzle_diameter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_default_id: Mapped[int] = mapped_column(
        ForeignKey("filament_profile_default.id", ondelete="CASCADE"), index=True
    )
    printer_model: Mapped[str] = mapped_column(String(50))
    nozzle_diameter: Mapped[str] = mapped_column(String(10), default="")
    slicer_filament: Mapped[str | None] = mapped_column(String(128))
    slicer_filament_name: Mapped[str | None] = mapped_column(String(255))

    profile_default: Mapped[FilamentProfileDefault] = relationship(back_populates="presets")


class FilamentProfileDefaultKProfile(Base):
    """A default's K profile for one hotend; the same row as ``SpoolKProfile``.

    Unique per hotend like ``spoolman_k_profile``, so a default copies onto a
    Spoolman spool without tripping that table's constraint. Goes with its
    printer, as a spool's K profiles do.
    """

    __tablename__ = "filament_profile_default_k_profile"

    __table_args__ = (UniqueConstraint("profile_default_id", "printer_id", "extruder", "nozzle_diameter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_default_id: Mapped[int] = mapped_column(
        ForeignKey("filament_profile_default.id", ondelete="CASCADE"), index=True
    )
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), index=True)
    extruder: Mapped[int] = mapped_column(Integer, default=0)
    nozzle_diameter: Mapped[str] = mapped_column(String(10), default="0.4")
    nozzle_type: Mapped[str | None] = mapped_column(String(50))
    k_value: Mapped[float] = mapped_column(Float)
    name: Mapped[str | None] = mapped_column(String(100))
    cali_idx: Mapped[int | None] = mapped_column(Integer)
    setting_id: Mapped[str | None] = mapped_column(String(50))

    profile_default: Mapped[FilamentProfileDefault] = relationship(back_populates="k_profiles")
