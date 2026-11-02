from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class LabelTemplate(Base):
    """A custom label sheet saved under a name (#3296). Shared by everyone on
    the install, like storage locations. Lengths are in millimetres."""

    __tablename__ = "label_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Uniqueness ignoring case and Unicode composition: "Avery 3490" and
    # "avery 3490" are one template. Casefolding can lengthen a name up to
    # three times, hence the wider column (schemas.label_template).
    name_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True, index=True)
    page: Mapped[str] = mapped_column(String(10), nullable=False)
    label_w_mm: Mapped[float] = mapped_column(Float, nullable=False)
    label_h_mm: Mapped[float] = mapped_column(Float, nullable=False)
    cols: Mapped[int] = mapped_column(Integer, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    top_mm: Mapped[float] = mapped_column(Float, nullable=False)
    left_mm: Mapped[float] = mapped_column(Float, nullable=False)
    col_gap_mm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    row_gap_mm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
