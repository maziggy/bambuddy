"""Read the single OIDC provider defined by BAMBUDDY_OIDC_* env vars (#2593).

A declarative deployment (compose, Helm, GitOps) has no way to click through
the settings UI, so one provider can be configured entirely from the
environment. This module only reads and defaults; validity is decided by the
same OIDCProviderCreate schema the API uses, so env config cannot bypass a
check the UI enforces.
"""

from __future__ import annotations

import os

# All four or nothing: a provider missing its secret would be written to the
# database and then fail at authorize time, long after the operator could
# connect the failure to a typo in their compose file.
_REQUIRED = (
    "BAMBUDDY_OIDC_NAME",
    "BAMBUDDY_OIDC_ISSUER_URL",
    "BAMBUDDY_OIDC_CLIENT_ID",
    "BAMBUDDY_OIDC_CLIENT_SECRET",
)

_TRUTHY = {"true", "1", "yes"}


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    return default if value is None else value.strip().lower() in _TRUTHY


def read_env_oidc_config() -> dict | None:
    """The provider's fields from the environment, or None if it isn't configured.

    An empty required var counts as unset -- `BAMBUDDY_OIDC_CLIENT_SECRET=` in
    a compose file is a forgotten value, not an intentional empty secret.
    """
    if not all(os.environ.get(key) for key in _REQUIRED):
        return None

    return {
        "name": os.environ["BAMBUDDY_OIDC_NAME"],
        "issuer_url": os.environ["BAMBUDDY_OIDC_ISSUER_URL"],
        "client_id": os.environ["BAMBUDDY_OIDC_CLIENT_ID"],
        "client_secret": os.environ["BAMBUDDY_OIDC_CLIENT_SECRET"],
        "scopes": os.environ.get("BAMBUDDY_OIDC_SCOPES", "openid email profile"),
        "is_enabled": _env_bool("BAMBUDDY_OIDC_ENABLED", True),
        "auto_create_users": _env_bool("BAMBUDDY_OIDC_AUTO_CREATE_USERS", False),
        "auto_link_existing_accounts": _env_bool("BAMBUDDY_OIDC_AUTO_LINK_EXISTING", False),
        "email_claim": os.environ.get("BAMBUDDY_OIDC_EMAIL_CLAIM", "email"),
        "require_email_verified": _env_bool("BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED", True),
        "icon_url": os.environ.get("BAMBUDDY_OIDC_ICON_URL"),
        "is_autologin": _env_bool("BAMBUDDY_OIDC_AUTOLOGIN", False),
    }
