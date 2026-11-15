from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class BuildPlate(Base):
    """A physical build plate the user owns (#1306).

    Every plate belongs to exactly one slicer base type (``base_type``, a key of
    ``utils.bed_types.BED_TYPES``) — that is what decides which jobs it can take.
    ``pattern`` only tells apart plates of the same type, e.g. the Bambu 3D
    Effect Carbon Fiber / Starry / Diamond / Galaxy sheets, which are all
    Smooth PEI plates as far as the slicer is concerned.

    Built-in rows are seeded on startup and can be switched off (``enabled``)
    but not deleted; the user adds their own alongside them.
    """

    __tablename__ = "build_plates"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identifier for seeded rows so re-seeding is idempotent and renames
    # don't duplicate them. NULL for user-created plates.
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_type: Mapped[str] = mapped_column(String(32), nullable=False)
    pattern: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Path or URL of a photo shown in the settings picker.
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# Seeded plates: (builtin_key, name, base_type, pattern, image, enabled, sort_order).
# The stock plates are on by default. The 3D Effect sheets are optional — the
# user ticks the ones they own in Settings. Bambu's own instructions for every
# 3D Effect variant are to slice with "Smooth PEI / High Temp Plate", so they
# are Smooth PEI plates with a pattern. Images under /img/plates/ are supplied
# separately; the UI falls back to the base type's icon until they exist.
DEFAULT_BUILD_PLATES: list[tuple[str, str, str, str | None, str | None, bool, int]] = [
    ("cool_plate", "Cool Plate", "cool_plate", None, "/img/bed/bed_cool.png", True, 10),
    ("supertack", "Cool Plate SuperTack", "supertack", None, "/img/bed/bed_cool_supertack.png", True, 20),
    ("textured_cool_plate", "Textured Cool Plate", "textured_cool_plate", None, "/img/bed/bed_cool.png", False, 30),
    ("engineering", "Engineering Plate", "engineering", None, "/img/bed/bed_engineering.png", True, 40),
    ("smooth_pei", "Smooth PEI Plate", "smooth_pei", None, "/img/bed/bed_pei_cool.png", True, 50),
    ("textured_pei", "Textured PEI Plate", "textured_pei", None, "/img/bed/bed_pei.png", True, 60),
    (
        "3d_effect_carbon_fiber",
        "3D Effect – Carbon Fiber",
        "smooth_pei",
        "Carbon Fiber",
        "/img/plates/3d_effect_carbon_fiber.jpg",
        False,
        100,
    ),
    ("3d_effect_starry", "3D Effect – Starry", "smooth_pei", "Starry", "/img/plates/3d_effect_starry.jpg", False, 110),
    (
        "3d_effect_diamond",
        "3D Effect – Diamond",
        "smooth_pei",
        "Diamond",
        "/img/plates/3d_effect_diamond.jpg",
        False,
        120,
    ),
    ("3d_effect_galaxy", "3D Effect – Galaxy", "smooth_pei", "Galaxy", "/img/plates/3d_effect_galaxy.jpg", False, 130),
]
