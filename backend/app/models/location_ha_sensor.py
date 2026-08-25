from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

# Width of the last_state column. The poller truncates what it persists to
# this, because a numeric entity can start reporting free text (an enum, an
# error string) longer than the column — SQLite stores it anyway, but
# PostgreSQL rejects the row and takes the whole poll batch's commit with it.
LAST_STATE_MAX_LENGTH = 64


class LocationHASensor(Base):
    """A read-only Home Assistant entity bound to a storage location (#2824).

    Mirrors ``PrinterHASensor`` for dryboxes, bins and shelves instead of
    printers — same read-only binding, alert rule and notification, but no
    print-blocking: holding a print queue doesn't mean anything for a
    storage bin.
    """

    __tablename__ = "location_ha_sensors"
    # The API rejects a duplicate (location, entity) binding, but that check is
    # read-then-insert — two concurrent creates can both pass it. This index is
    # the backstop that turns the loser into an IntegrityError instead of a
    # second row silently shadowing the first. create_all() only covers fresh
    # installs; upgraded databases get it from
    # _migrate_location_ha_sensor_unique_binding in core/database.py, which
    # must create the same index under the same name.
    __table_args__ = (Index("uq_location_ha_sensors_location_entity", "location_id", "entity_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(255))

    # "binary" for binary_sensor.*, "numeric" for sensor.*. Decides how the
    # state is rendered and which alert fields apply.
    kind: Mapped[str] = mapped_column(String(16), default="binary")

    # HA's own device_class, snapshotted when the entity is bound. Drives the
    # category a sensor is treated as (temperature/humidity/battery) and the
    # unit shown next to the value.
    device_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Numeric only: "°C", "%", ... shown next to the value.
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # What counts as needing attention. One notion, two consumers: the
    # colorized value on the card/table and the notification. Binary sensors
    # use alert_state ("on"/"off"/None), numeric ones the thresholds. All
    # None means "just show the value".
    alert_state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    alert_above: Mapped[float | None] = mapped_column(Float, nullable=True)
    alert_below: Mapped[float | None] = mapped_column(Float, nullable=True)

    notify_on_alert: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    show_on_card: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Last poll result. Persisted so a restart doesn't blank the card until the
    # first poll lands, and so notifications only fire on a real transition.
    last_state: Mapped[str | None] = mapped_column(String(LAST_STATE_MAX_LENGTH), nullable=True)
    last_changed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    location: Mapped["Location"] = relationship(back_populates="ha_sensors")


from backend.app.models.location import Location  # noqa: E402
