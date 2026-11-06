"""Where Bambuddy finds the Manyfold install, and how it signs in (#1471).

Manyfold is self-hosted, so unlike MakerWorld there is no fixed host: an admin
enters the install's URL and the client ID and secret of an OAuth application
created in Manyfold (Settings -> API). The three values live in the settings
table under their own keys. They are not part of ``AppSettings``, so the
general settings API never returns them; the secret is never returned at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes._url_safety import assert_safe_lan_service_url
from backend.app.models.settings import Settings

URL_KEY = "manyfold_url"
CLIENT_ID_KEY = "manyfold_client_id"
CLIENT_SECRET_KEY = "manyfold_client_secret"
CONFIG_KEYS = (URL_KEY, CLIENT_ID_KEY, CLIENT_SECRET_KEY)


@dataclass(frozen=True)
class ManyfoldConfig:
    url: str = ""
    client_id: str = ""
    client_secret: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.client_id and self.client_secret)


def normalize_url(raw: str) -> str:
    """The install's base URL without a trailing slash.

    Accepts a sub-path (``https://example.com/manyfold``) for installs behind a
    reverse proxy. Raises ``ValueError`` for anything that is not a plain
    http(s) URL with a host, and, like Spoolman's, for what the LAN-service
    tier refuses (cloud-metadata endpoints, numeric-encoded IPs, ...).
    Loopback and private addresses stay allowed: Manyfold usually runs on
    the same host or LAN.
    """
    candidate = (raw or "").strip()
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise ValueError("The Manyfold URL is not a valid URL") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The Manyfold URL must start with http:// or https:// and name a host")
    if parts.username or parts.password:
        raise ValueError("The Manyfold URL must not contain a user name or password")
    if parts.query or parts.fragment:
        raise ValueError("The Manyfold URL must not contain a query or fragment")
    assert_safe_lan_service_url(candidate, label="Manyfold URL")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


async def load_config(db: AsyncSession) -> ManyfoldConfig:
    rows = await db.execute(select(Settings.key, Settings.value).where(Settings.key.in_(CONFIG_KEYS)))
    values = {key: value or "" for key, value in rows.all()}
    return ManyfoldConfig(
        url=values.get(URL_KEY, ""),
        client_id=values.get(CLIENT_ID_KEY, ""),
        client_secret=values.get(CLIENT_SECRET_KEY, ""),
    )
