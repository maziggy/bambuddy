from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class APIKey(Base):
    """API key for external webhook access."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))  # User-friendly name
    key_hash: Mapped[str] = mapped_column(String(255))  # bcrypt hash of the key
    key_prefix: Mapped[str] = mapped_column(String(20))  # First 8 chars + "..." for display

    # Owner — required for new keys, NULL only on legacy rows that predate per-user
    # ownership. Cloud routes reject calls from keys without an owner so callers are
    # forced to recreate them. CASCADE so deleting a user removes their keys.
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Permissions
    can_queue: Mapped[bool] = mapped_column(Boolean, default=True)  # Add to queue
    can_control_printer: Mapped[bool] = mapped_column(Boolean, default=False)  # Start/stop/cancel
    can_read_status: Mapped[bool] = mapped_column(Boolean, default=True)  # Query status
    can_access_cloud: Mapped[bool] = mapped_column(Boolean, default=False)  # Read /cloud/* on the owner's behalf

    # Optional scope limits
    printer_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)  # null = all printers

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Optional expiry
