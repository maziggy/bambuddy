from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class Spool(Base):
    """Spool inventory item for tracking filament spools and their properties."""

    __tablename__ = "spool"

    id: Mapped[int] = mapped_column(primary_key=True)
    material: Mapped[str] = mapped_column(String(50))  # PLA, PETG, ABS, etc.
    subtype: Mapped[str | None] = mapped_column(String(50))  # Basic, Matte, Silk, etc.
    color_name: Mapped[str | None] = mapped_column(String(100))  # "Jade White"
    rgba: Mapped[str | None] = mapped_column(String(8))  # RRGGBBAA hex
    brand: Mapped[str | None] = mapped_column(String(100))  # "Polymaker"
    label_weight: Mapped[int] = mapped_column(Integer, default=1000)  # Advertised net weight (g)
    core_weight: Mapped[int] = mapped_column(Integer, default=250)  # Empty spool weight (g)
    core_weight_catalog_id: Mapped[int | None] = mapped_column(
        Integer
    )  # Reference to spool_catalog entry for core weight
    weight_used: Mapped[float] = mapped_column(Float, default=0)  # Consumed grams
    slicer_filament: Mapped[str | None] = mapped_column(String(50))  # Preset ID (e.g. "GFL99")
    slicer_filament_name: Mapped[str | None] = mapped_column(String(100))  # Preset name for slicer
    nozzle_temp_min: Mapped[int | None] = mapped_column()  # Override min temp
    nozzle_temp_max: Mapped[int | None] = mapped_column()  # Override max temp
    note: Mapped[str | None] = mapped_column(String(500))
    added_full: Mapped[bool | None] = mapped_column()  # Whether spool was added as full (unused)
    last_used: Mapped[datetime | None] = mapped_column(DateTime)  # Last time this spool was used in a print
    encode_time: Mapped[datetime | None] = mapped_column(DateTime)  # When spool was encoded/written to tag
    tag_uid: Mapped[str | None] = mapped_column(String(16))  # RFID tag UID (16 hex chars)
    tray_uuid: Mapped[str | None] = mapped_column(String(32))  # Bambu Lab spool UUID (32 hex chars)
    data_origin: Mapped[str | None] = mapped_column(String(20))  # How data was populated: manual, rfid_auto, nfc_link
    tag_type: Mapped[str | None] = mapped_column(String(20))  # Tag vendor: bambulab, generic, etc.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)  # NULL = active
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    k_profiles: Mapped[list["SpoolKProfile"]] = relationship(back_populates="spool", cascade="all, delete-orphan")
    assignments: Mapped[list["SpoolAssignment"]] = relationship(back_populates="spool", cascade="all, delete-orphan")


from backend.app.models.spool_assignment import SpoolAssignment  # noqa: E402
from backend.app.models.spool_k_profile import SpoolKProfile  # noqa: E402
