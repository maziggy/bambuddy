"""Connected apps: external applications that sign users in with Bambuddy.

A connected app is registered by an admin with one exact callback URL. It
signs a user in through a minimal OAuth 2.0 authorization-code flow with PKCE
(see ``api/routes/connected_apps.py``): Bambuddy hands the app a single-use,
60-second code, and the app's server swaps it for the user's identity and
permissions. The app never sees the user's Bambuddy login token.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ConnectedApp(Base):
    __tablename__ = "connected_apps"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # Public identifier the app sends on every request.
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # bcrypt hash; the secret itself is shown once, at creation or rotation.
    client_secret_hash: Mapped[str] = mapped_column(String(255))
    # Codes are only ever delivered here. Compared exactly, never by prefix.
    redirect_uri: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConnectedAppGrant(Base):
    """A user's consent for one app, so the consent screen is shown only once.

    Deleting the app or the user removes the grant, and the next sign-in asks
    again.
    """

    __tablename__ = "connected_app_grants"
    __table_args__ = (Index("uq_connected_app_grants_app_user", "app_id", "user_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("connected_apps.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
