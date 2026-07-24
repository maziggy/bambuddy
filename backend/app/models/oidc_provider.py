from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    __table_args__ = (
        # DB-level enforcement of SEC-1: blocks only Fall B (email_claim='email' + require_ev=False).
        # Fall C (custom claim) is safe — no email_verified gate on that path.
        # Enforced on new installations; existing tables updated via _migrate_update_auto_link_constraint.
        CheckConstraint(
            "auto_link_existing_accounts = FALSE OR email_claim != 'email' OR require_email_verified = TRUE",
            name="ck_auto_link_requires_verified_email_claim",
        ),
        # All-or-nothing icon-cache record (#1333). The application keeps the
        # triplet consistent via _fetch_icon_or_400 + DELETE /icon, but a CHECK
        # constraint at the DB layer prevents drift from raw SQL maintenance
        # scripts, manual UPDATEs during incident recovery, etc.
        # Fresh installs (SQLite + PostgreSQL) get this via metadata.create_all.
        # Stale PostgreSQL installs get it via ALTER TABLE ADD CONSTRAINT in
        # run_migrations. SQLite cannot ADD CONSTRAINT to an existing table —
        # stale SQLite installs rely on the application layer, the same
        # trade-off documented for the default_group_id FK ON DELETE SET NULL.
        CheckConstraint(
            "(icon_data IS NULL) = (icon_content_type IS NULL) AND (icon_content_type IS NULL) = (icon_etag IS NULL)",
            name="ck_oidc_icon_triplet_co_null",
        ),
    )

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
    # When True, an existing local user whose email matches the OIDC claim is
    # automatically linked on first SSO login.  Default is False (conservative):
    # operators must explicitly opt-in to prevent an attacker-controlled IdP from
    # silently hijacking local accounts via email matching (M-2 fix).
    auto_link_existing_accounts: Mapped[bool] = mapped_column(Boolean, default=False)
    # JWT claim name used as the email identity (default "email").
    # Set to "preferred_username" or "upn" for Azure Entra ID, which does not send
    # email_verified — using a custom claim skips the email_verified check entirely
    # and is the recommended Azure configuration.
    # Has no interaction with require_email_verified when set to a non-"email" value:
    # custom claims never perform an email_verified check regardless of that setting.
    email_claim: Mapped[str] = mapped_column(String(64), default="email")
    # When True (default), the "email" claim is only trusted when email_verified=True.
    # Set to False to accept the email even when email_verified is absent — required
    # for providers like Azure Entra ID that never send email_verified and where a
    # custom claim (email_claim != "email") is not preferred.
    # Has no effect when email_claim is not "email": the custom-claim path never
    # performs an email_verified check regardless of this setting.
    require_email_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    # Nullable FK — configurable default group for auto-created OIDC users.
    # Falls back to "Viewers" when None. ON DELETE SET NULL fires on PostgreSQL;
    # SQLite ignores it (no PRAGMA foreign_keys=ON), so runtime resolution handles dangling refs.
    default_group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # Optional icon URL the admin entered. The actual image bytes are fetched
    # server-side and cached in icon_data — the SPA never hotlinks this URL
    # (would require loosening img-src CSP; see PR #1333 / issue #1333).
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Cached icon bytes (PNG/JPEG/WebP/GIF). Marked deferred=True so that
    # list-style queries (`GET /oidc/providers`) don't pull the BLOB on every
    # login-page render — only the GET /icon endpoint un-defers it via
    # `select(...).options(undefer(...))`.
    icon_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, default=None, deferred=True)
    # MIME type derived from the fetched icon (e.g. "image/png"). Also serves
    # as the "has-icon" indicator — checked instead of icon_data so we never
    # accidentally trigger an async lazy-load on the deferred BLOB column.
    # Width 20 is plenty: the longest whitelisted value is "image/jpeg" (10
    # chars). Tighter than 50 so the schema documents the intent.
    icon_content_type: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    # SHA-256 hex of icon_data, served as the ETag header so clients can
    # revalidate via If-None-Match and receive 304 Not Modified.
    icon_etag: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # When True, the LoginPage redirects unauthenticated visitors straight to
    # this provider's authorize URL on mount (#1589). At most one provider can
    # carry this flag at a time; setting it on a new provider clears it on the
    # previous one. The frontend always falls back to the local form if the
    # authorize-URL fetch fails or times out, and ``/login?fallback=local``
    # plus ``BAMBUDDY_LOCAL_LOGIN=true`` provide a documented recovery path.
    is_autologin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Marks the single provider defined by BAMBUDDY_OIDC_* env vars. Upserted on
    # startup; UI/API writes to it are rejected. Never delete-recreated (user_oidc_links
    # FK is ON DELETE CASCADE).
    is_env_managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    @property
    def has_icon(self) -> bool:
        """True when cached icon bytes exist. Reads the non-deferred
        ``icon_content_type`` column so accessing this never triggers an
        async lazy-load on the deferred ``icon_data`` BLOB."""
        return self.icon_content_type is not None

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
    __table_args__ = (
        # T2: Prevent duplicate OIDC identities and duplicate provider links.
        # (provider_id, provider_user_id) — one OIDC sub per provider maps to at most one local user.
        UniqueConstraint("provider_id", "provider_user_id", name="uq_oidc_link_provider_sub"),
        # (user_id, provider_id) — one local user can link to each provider at most once.
        UniqueConstraint("user_id", "provider_id", name="uq_oidc_link_user_provider"),
    )

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
