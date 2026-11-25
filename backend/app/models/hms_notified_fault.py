"""HMS faults a notification has already gone out for, per printer (#3486).

The printer keeps faults in its ``hms[]`` list long after they happened, and
the de-duplication that stops them being sent again lived only in memory: every
Bambuddy restart sent each still-listed fault once more. Kept here so a restart
picks up where it left off. Only the set of fault keys is stored; when each was
last seen stays in memory.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class HmsNotifiedFault(Base):
    __tablename__ = "hms_notified_faults"
    __table_args__ = (UniqueConstraint("printer_id", "fault_key", name="uq_hms_notified_faults_printer_fault"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fault_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
