from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base
from backend.app.core.encryption import mfa_decrypt, mfa_encrypt


class OIDCProvider(Base):
    """OpenID Connect provider configuration.

    Supports any standards-compliant OIDC provider such as PocketID,
    Authentik, Keycloak, Authelia, Google, etc.

    The issuer_url must point to the root issuer (e.g. ``https://id.example.com``).
    The OIDC discovery document is fetched from
    ``{issuer_url}/.well-known/openid-configuration`` at runtime.
    """

    __tablename__ = "oidc_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-readable name shown on the login button (e.g. "PocketID", "Google")
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Full OIDC issuer URL (e.g. "https://id.example.com")
    issuer_url: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    # Encrypted at rest when MFA_ENCRYPTION_KEY is set.
    # Use .client_secret / .client_secret setter rather than _client_secret_enc directly.
    _client_secret_enc: Mapped[str] = mapped_column("client_secret", String(512))

    @property
    def client_secret(self) -> str:
        return mfa_decrypt(self._client_secret_enc)

    @client_secret.setter
    def client_secret(self, value: str) -> None:
        self._client_secret_enc = mfa_encrypt(value)

    # Space-separated scopes; must include "openid"
    scopes: Mapped[str] = mapped_column(String(500), default="openid email profile")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # When True, a new local user is created automatically on first OIDC login
    auto_create_users: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional icon URL (SVG/PNG) shown on the login button
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship to linked user accounts
    user_links: Mapped[list[UserOIDCLink]] = relationship(
        "UserOIDCLink",
        back_populates="provider",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<OIDCProvider {self.name!r}>"


class UserOIDCLink(Base):
    """Links a local Bambuddy user account to an identity at an OIDC provider."""

    __tablename__ = "user_oidc_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int] = mapped_column(Integer, ForeignKey("oidc_providers.id", ondelete="CASCADE"), index=True)
    # The "sub" claim from the OIDC ID token — stable identifier for the user
    provider_user_id: Mapped[str] = mapped_column(String(500))
    # Email returned by the provider (informational; may differ from local email)
    provider_email: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    provider: Mapped[OIDCProvider] = relationship("OIDCProvider", back_populates="user_links")

    def __repr__(self) -> str:
        return f"<UserOIDCLink user_id={self.user_id} provider_id={self.provider_id}>"
