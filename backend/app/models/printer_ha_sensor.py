from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrinterHASensor(Base):
    """A read-only Home Assistant entity bound to a printer (#1148, #448).

    Deliberately *not* a ``SmartPlug`` row with a wider entity pattern. A plug
    carries auto-on/auto-off, schedules, power alerts, energy snapshots and
    ``controls_printer_power``; none of that means anything for a door contact,
    and ``get_smart_plug_by_printer`` would hand the card's power button a
    sensor to switch. Sensors get their own table and their own read-only
    routes instead.

    Not to be confused with ``PrinterSensorHistory``, which stores the
    printer's *own* heater readings.
    """

    __tablename__ = "printer_ha_sensors"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(255))

    # "binary" for binary_sensor.*, "numeric" for sensor.*. Decides how the
    # state is rendered and which alert fields apply.
    kind: Mapped[str] = mapped_column(String(16), default="binary")

    # HA's own device_class, snapshotted when the entity is bound. Drives the
    # on/off wording (door -> Open/Closed, motion -> Detected/Clear) and the
    # icon, so the card doesn't have to say "On" for an open door.
    device_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Numeric only: "°C", "%", "ppm", ... shown next to the value.
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # What counts as needing attention. One notion, three consumers: the pill
    # colour on the card, the notification, and the print interlock.
    # Binary sensors use alert_state ("on"/"off"/None), numeric ones the
    # thresholds. All None means "just show the value".
    alert_state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    alert_above: Mapped[float | None] = mapped_column(Float, nullable=True)
    alert_below: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Hold queued prints for this printer while the sensor is in its alert
    # state — the enclosure-door case this feature was asked for. Opt-in, and
    # only ever a *hold*: the item stays pending with a waiting_reason and
    # dispatches by itself once the door closes.
    block_print: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    notify_on_alert: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    show_on_printer_card: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Last poll result. Persisted so a restart doesn't blank the card until the
    # first poll lands, and so notifications only fire on a real transition.
    last_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_changed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    printer: Mapped["Printer"] = relationship(back_populates="ha_sensors")


from backend.app.models.printer import Printer  # noqa: E402
