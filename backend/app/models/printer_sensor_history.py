from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrinterSensorHistory(Base):
    """Historical heater readings (nozzle / nozzle_2 / bed / chamber).

    Parallel to AMSSensorHistory, but per-(printer, sensor_kind) rather
    than per-(printer, ams_id). Sensor counts vary by model (single vs
    dual nozzle, presence of chamber heater), so a long-format row per
    sensor reads cleanly and leaves room for future kinds (cpu, motor)
    without another migration.
    """

    __tablename__ = "printer_sensor_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"))
    sensor_kind: Mapped[str] = mapped_column(String(32))  # nozzle | nozzle_2 | bed | chamber
    value: Mapped[float | None] = mapped_column(Float)  # current temperature, Celsius
    target: Mapped[float | None] = mapped_column(Float)  # target temperature when set, Celsius
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index(
            "ix_printer_sensor_history_printer_kind_time",
            "printer_id",
            "sensor_kind",
            "recorded_at",
        ),
    )

    printer: Mapped["Printer"] = relationship(back_populates="sensor_history")


from backend.app.models.printer import Printer  # noqa: E402
