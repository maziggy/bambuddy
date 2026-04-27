"""T-Gap 1 & T-Gap 2: Settings scrubbing for API-key callers + permission checks on RCE endpoints."""

import pytest
from httpx import AsyncClient


@pytest.fixture
async def api_key_with_settings_read(db_session):
    """API key that has only INVENTORY_UPDATE permission (no SETTINGS_UPDATE)."""
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    full_key, key_hash, key_prefix = generate_api_key()
    api_key = APIKey(
        name="read-only-key",
        key_hash=key_hash,
        key_prefix=key_prefix,
        can_queue=False,
        can_control_printer=False,
        can_read_status=True,
        enabled=True,
    )
    db_session.add(api_key)
    await db_session.commit()
    return full_key


@pytest.fixture
async def sensitive_settings(db_session):
    """Seed all 5 sensitive settings fields with non-empty values."""
    from backend.app.models.settings import Settings

    # Keys listed separately so no single line pairs a credential-looking name
    # with a string value (avoids false-positive secret scanner hits).
    _credential_keys = [
        "mqtt_password",
        "ha_token",
        "prometheus_token",
        "virtual_printer_access_code",
        "ldap_bind_password",
    ]
    for key in _credential_keys:
        db_session.add(Settings(key=key, value="testdata"))
    db_session.add(Settings(key="auth_enabled", value="false"))
    await db_session.commit()


class TestSettingsScrubForApiKey:
    """T-Gap 1: GET /settings must blank all 5 sensitive fields for API-key callers."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_header_blanks_sensitive_fields(
        self,
        async_client: AsyncClient,
        db_session,
        api_key_with_settings_read,
        sensitive_settings,
    ):
        resp = await async_client.get(
            "/api/v1/settings/",
            headers={"X-API-Key": api_key_with_settings_read},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mqtt_password"] == ""
        assert data["ha_token"] == ""
        assert data["prometheus_token"] == ""
        assert data["virtual_printer_access_code"] == ""
        assert data["ldap_bind_password"] == ""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bearer_api_key_blanks_sensitive_fields(
        self,
        async_client: AsyncClient,
        db_session,
        api_key_with_settings_read,
        sensitive_settings,
    ):
        resp = await async_client.get(
            "/api/v1/settings/",
            headers={"Authorization": f"Bearer {api_key_with_settings_read}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mqtt_password"] == ""
        assert data["ha_token"] == ""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unauthenticated_request_does_not_blank_fields(
        self,
        async_client: AsyncClient,
        db_session,
        sensitive_settings,
    ):
        """Without auth, settings are returned as-is (auth disabled in test env)."""
        resp = await async_client.get("/api/v1/settings/")
        assert resp.status_code == 200
        data = resp.json()
        # Only ldap_bind_password is always blanked regardless of caller
        assert data["ldap_bind_password"] == ""
        # Other fields should NOT be blanked for non-API-key callers
        assert data["mqtt_password"] != ""
        assert data["ha_token"] != ""


class TestRceEndpointPermissions:
    """T-Gap 2: System command endpoints require SETTINGS_UPDATE permission."""

    @pytest.fixture
    async def auth_enabled(self, db_session):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

    @pytest.fixture
    async def inventory_only_api_key(self, db_session):
        """API key with ONLY inventory:update permission (no settings:update)."""
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        full_key, key_hash, key_prefix = generate_api_key()
        api_key = APIKey(
            name="inventory-key",
            key_hash=key_hash,
            key_prefix=key_prefix,
            can_queue=True,
            can_control_printer=False,
            can_read_status=True,
            enabled=True,
        )
        db_session.add(api_key)
        await db_session.commit()
        return full_key

    @pytest.fixture
    async def spoolbuddy_device(self, db_session):
        from backend.app.models.spoolbuddy_device import SpoolBuddyDevice

        device = SpoolBuddyDevice(
            device_id="test-device-001",
            hostname="spoolbuddy-01",
            ip_address="192.168.1.50",
        )
        db_session.add(device)
        await db_session.commit()
        return device

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_command_requires_settings_update(
        self,
        async_client: AsyncClient,
        db_session,
        auth_enabled,
        inventory_only_api_key,
        spoolbuddy_device,
    ):
        resp = await async_client.post(
            f"/api/v1/spoolbuddy/devices/{spoolbuddy_device.device_id}/system/command",
            json={"command": "reboot"},
            headers={"X-API-Key": inventory_only_api_key},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trigger_update_requires_settings_update(
        self,
        async_client: AsyncClient,
        db_session,
        auth_enabled,
        inventory_only_api_key,
        spoolbuddy_device,
    ):
        resp = await async_client.post(
            f"/api/v1/spoolbuddy/devices/{spoolbuddy_device.device_id}/update",
            json={},
            headers={"X-API-Key": inventory_only_api_key},
        )
        assert resp.status_code == 403
