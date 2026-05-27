from __future__ import annotations

import json
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.core.encryption import mfa_decrypt, mfa_encrypt


class UserTOTP(Base):
    """TOTP (Time-based One-Time Password) secret for a user.

    Stores the TOTP secret used by authenticator apps (Google Authenticator,
    Proton Authenticator, Aegis, etc.). One record per user; is_enabled=False
    while the setup is pending confirmation.
    """

    __tablename__ = "user_totp"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    # TOTP secret — encrypted at rest when MFA_ENCRYPTION_KEY is set.
    # Use .secret / .set_secret() rather than accessing _secret_enc directly.
    _secret_enc: Mapped[str] = mapped_column("secret", String(512))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Hashed backup codes stored as JSON array of strings
    # Each entry is a hashed one-time-use recovery code
    backup_codes_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # TOTP replay protection: stores the 30-second time-step counter of the last
    # accepted code so the same code cannot be used twice within one window.
    last_totp_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def secret(self) -> str:
        """Return the decrypted TOTP secret."""
        return mfa_decrypt(self._secret_enc)

    @secret.setter
    def secret(self, value: str) -> None:
        """Store the TOTP secret, encrypting it when MFA_ENCRYPTION_KEY is set."""
        self._secret_enc = mfa_encrypt(value)

    @property
    def backup_code_hashes(self) -> list[str]:
        """T5: Get stored backup-code hashes as a list.

        The name makes clear that these are *hashes*, not plaintext codes,
        so callers know they must verify with a password-hashing library
        rather than compare directly.
        """
        if not self.backup_codes_json:
            return []
        return json.loads(self.backup_codes_json)

    @backup_code_hashes.setter
    def backup_code_hashes(self, hashes: list[str]) -> None:
        """Persist backup-code hashes as a JSON array."""
        self.backup_codes_json = json.dumps(hashes)

    def accept_counter(self, new_counter: int) -> None:
        """T4: Record an accepted TOTP time-step counter, rejecting backward movement.

        Raises ``HTTPException(400)`` if ``new_counter`` is not strictly greater
        than ``last_totp_counter``, preventing counter roll-back attacks (e.g. an
        attacker who replays a previously accepted code after the counter wraps or
        the clock is skewed backward).

        The caller is responsible for flushing/committing the change to the DB.
        """
        if self.last_totp_counter is not None and new_counter <= self.last_totp_counter:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code already used",
            )
        self.last_totp_counter = new_counter

    def __repr__(self) -> str:
        return f"<UserTOTP user_id={self.user_id} enabled={self.is_enabled}>"
