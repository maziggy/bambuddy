from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.macro import Macro


class MacroVar(Base):
    """Persistent key-value store for macro scripts.

    Keys are scoped per macro (macro_id set) or global (macro_id NULL).
    Values are JSON-encoded so any serialisable type is supported.
    expires_at is optional; expired rows are ignored at read time and
    pruned by a periodic background task.
    """

    __tablename__ = "macro_vars"
    __table_args__ = (UniqueConstraint("key", "macro_id", name="uq_macro_vars_key_macro_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    value_json: Mapped[str] = mapped_column(Text)
    macro_id: Mapped[int | None] = mapped_column(ForeignKey("macros.id", ondelete="CASCADE"), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    macro: Mapped[Macro | None] = relationship(back_populates="vars")
