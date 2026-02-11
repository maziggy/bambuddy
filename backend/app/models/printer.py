from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    serial_number: Mapped[str] = mapped_column(String(50), unique=True)
    ip_address: Mapped[str] = mapped_column(String(253))
    access_code: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(100))  # Group/location name
    nozzle_count: Mapped[int] = mapped_column(default=1)  # 1 or 2, auto-detected from MQTT
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_archive: Mapped[bool] = mapped_column(Boolean, default=True)
    print_hours_offset: Mapped[float] = mapped_column(Float, default=0.0)  # Baseline hours to add
    runtime_seconds: Mapped[int] = mapped_column(default=0)  # Accumulated active runtime (RUNNING/PAUSE states)
    last_runtime_update: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # Last time runtime was updated
    # External camera configuration
    external_camera_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_camera_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # mjpeg, rtsp, snapshot
    external_camera_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Plate detection - check if build plate is empty before starting print
    plate_detection_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # ROI for plate detection (percentages: 0.0-1.0)
    plate_detection_roi_x: Mapped[float | None] = mapped_column(Float, nullable=True)  # X start %
    plate_detection_roi_y: Mapped[float | None] = mapped_column(Float, nullable=True)  # Y start %
    plate_detection_roi_w: Mapped[float | None] = mapped_column(Float, nullable=True)  # Width %
    plate_detection_roi_h: Mapped[float | None] = mapped_column(Float, nullable=True)  # Height %
    # Part removal confirmation - require confirmation that build plate is clear before next job
    part_removal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    part_removal_required: Mapped[bool] = mapped_column(Boolean, default=False)  # Active when job needs collection
    last_job_name: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Last completed job name
    last_job_user: Mapped[str | None] = mapped_column(String(100), nullable=True)  # User who submitted last job
    last_job_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Job start time
    last_job_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Job end time
    last_job_queue_item_id: Mapped[int | None] = mapped_column(nullable=True)  # Queue item ID that needs collection
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    archives: Mapped[list["PrintArchive"]] = relationship(back_populates="printer", cascade="all, delete-orphan")
    smart_plugs: Mapped[list["SmartPlug"]] = relationship(back_populates="printer")
    notification_providers: Mapped[list["NotificationProvider"]] = relationship(back_populates="printer")
    maintenance_items: Mapped[list["PrinterMaintenance"]] = relationship(
        back_populates="printer", cascade="all, delete-orphan"
    )
    kprofile_notes: Mapped[list["KProfileNote"]] = relationship(back_populates="printer", cascade="all, delete-orphan")
    ams_history: Mapped[list["AMSSensorHistory"]] = relationship(back_populates="printer", cascade="all, delete-orphan")


from backend.app.models.ams_history import AMSSensorHistory  # noqa: E402
from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.kprofile_note import KProfileNote  # noqa: E402
from backend.app.models.maintenance import PrinterMaintenance  # noqa: E402
from backend.app.models.notification import NotificationProvider  # noqa: E402
from backend.app.models.smart_plug import SmartPlug  # noqa: E402
