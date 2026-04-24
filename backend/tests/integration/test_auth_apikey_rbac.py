"""Integration tests for API key RBAC enforcement (security fix C1)."""

import pytest
from httpx import AsyncClient


@pytest.fixture
async def api_key_data(async_client: AsyncClient, db_session):
    """Create an API key and return its full key value."""
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    full_key, key_hash, key_prefix = generate_api_key()
    api_key = APIKey(
        name="test-key",
        key_hash=key_hash,
        key_prefix=key_prefix,
        can_queue=True,
        can_control_printer=True,
        can_read_status=True,
        enabled=True,
    )
    db_session.add(api_key)
    await db_session.commit()
    return full_key


@pytest.fixture
async def spoolman_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


class TestApiKeyRbacDenied:
    """API keys must be refused for admin-only endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_cannot_access_settings_update_endpoint(
        self, async_client: AsyncClient, db_session, api_key_data
    ):
        """API key must not be usable for settings:update endpoints (C1)."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

        resp = await async_client.put(
            "/api/v1/settings/",
            json={},
            headers={"X-API-Key": api_key_data},
        )
        assert resp.status_code == 403
        assert "administrative operations" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_bearer_cannot_access_settings_update(
        self, async_client: AsyncClient, db_session, api_key_data
    ):
        """Bearer bb_ API key must also be refused for settings:update (C1)."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

        resp = await async_client.put(
            "/api/v1/settings/",
            json={},
            headers={"Authorization": f"Bearer {api_key_data}"},
        )
        assert resp.status_code == 403
        assert "administrative operations" in resp.json()["detail"]


class TestApiKeyRbacAllowed:
    """API keys must still work for non-admin endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_can_access_inventory_read(
        self, async_client: AsyncClient, db_session, api_key_data, spoolman_settings
    ):
        """API key must be accepted for inventory:read endpoints (C1)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.app.models.settings import Settings

        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.base_url = "http://localhost:7912"
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.get_all_spools = AsyncMock(return_value=[])
        with patch(
            "backend.app.api.routes.spoolman_inventory._get_client",
            AsyncMock(return_value=mock_client),
        ):
            resp = await async_client.get(
                "/api/v1/spoolman/inventory/spools",
                headers={"X-API-Key": api_key_data},
            )
        assert resp.status_code == 200


class TestApiKeyDenylistIntegrity:
    """Drift-detection: assert that admin-tier permissions remain in the denylist."""

    def test_admin_permissions_are_denied_for_api_keys(self):
        """All known admin-tier permissions must be in _APIKEY_DENIED_PERMISSIONS (H1 guard)."""
        from backend.app.core.auth import _APIKEY_DENIED_PERMISSIONS
        from backend.app.core.permissions import Permission

        expected_denied = {
            # SETTINGS_READ is intentionally NOT denied — SpoolBuddy kiosk reads
            # settings via API key (e.g. to sync the UI language).
            Permission.SETTINGS_UPDATE,
            Permission.SETTINGS_BACKUP,
            Permission.SETTINGS_RESTORE,
            Permission.USERS_READ,
            Permission.USERS_CREATE,
            Permission.USERS_UPDATE,
            Permission.USERS_DELETE,
            Permission.GROUPS_READ,
            Permission.GROUPS_CREATE,
            Permission.GROUPS_UPDATE,
            Permission.GROUPS_DELETE,
            Permission.API_KEYS_READ,
            Permission.API_KEYS_CREATE,
            Permission.API_KEYS_UPDATE,
            Permission.API_KEYS_DELETE,
            Permission.GITHUB_BACKUP,
            Permission.GITHUB_RESTORE,
            Permission.FIRMWARE_UPDATE,
        }
        missing = expected_denied - _APIKEY_DENIED_PERMISSIONS
        assert not missing, (
            f"Admin-tier permissions not in API key denylist (add them to _APIKEY_DENIED_PERMISSIONS): {missing}"
        )

    def test_operational_permissions_are_allowed_for_api_keys(self):
        """Core operational permissions must NOT be in the denylist."""
        from backend.app.core.auth import _APIKEY_DENIED_PERMISSIONS
        from backend.app.core.permissions import Permission

        expected_allowed = {
            Permission.INVENTORY_READ,
            Permission.INVENTORY_CREATE,
            Permission.INVENTORY_UPDATE,
            Permission.PRINTERS_READ,
            Permission.PRINTERS_CONTROL,
            Permission.ARCHIVES_READ,
            # SpoolBuddy kiosk reads settings (e.g. language) via API key — must stay allowed.
            Permission.SETTINGS_READ,
        }
        incorrectly_denied = expected_allowed & _APIKEY_DENIED_PERMISSIONS
        assert not incorrectly_denied, (
            f"Operational permissions incorrectly in API key denylist: {incorrectly_denied}"
        )
