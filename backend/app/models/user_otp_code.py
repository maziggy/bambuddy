from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class UserOTPCode(Base):
    """Temporary email OTP (One-Time Password) code for 2FA verification.

    Each record represents a single sent OTP code.  Codes expire after
    OTP_TTL_MINUTES and are invalidated after MAX_ATTEMPTS failed attempts
    or after successful verification.
    """

    __tablename__ = "user_otp_codes"

    OTP_TTL_MINUTES = 10
    MAX_ATTEMPTS = 5

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # pbkdf2_sha256 hash of the 6-digit code
    code_hash: Mapped[str] = mapped_column(String(255))
    # Number of failed verification attempts for this code
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # True once the code has been successfully used or explicitly invalidated
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def consume(self) -> None:
        """T4: Mark this OTP as used, enforcing preconditions.

        Raises ``ValueError`` if the code is already used or expired so callers
        cannot silently re-use an invalidated code.  The caller is responsible
        for flushing/committing the change to the DB.
        """
        now = datetime.now(timezone.utc)
        exp = self.expires_at
        if exp.tzinfo is None:
            from datetime import timezone as _tz

            exp = exp.replace(tzinfo=_tz.utc)
        if self.used:
            raise ValueError("OTP code has already been used")
        if exp < now:
            raise ValueError("OTP code has expired")
        self.used = True

    def __repr__(self) -> str:
        return f"<UserOTPCode user_id={self.user_id} used={self.used}>"
