"""Tests for the public /settings/ui-preferences endpoint (#1293).

Reporter @Tivonfeng: granting `printers:clear_plate` alone wasn't enough to
make the Clear Plate button work — the frontend also needs `require_plate_clear`
from /settings, which requires SETTINGS_READ (and also surfaces SMTP/LDAP/MQTT
secrets). The fix is a public subset endpoint that returns only UI rendering
fields, so the frontend doesn't have to demand SETTINGS_READ for non-admin UX.

The two guarantees pinned here:
1. The endpoint is accessible without SETTINGS_READ.
2. The endpoint NEVER returns sensitive fields (SMTP/LDAP/MQTT credentials,
   API tokens, HA bearer token, etc.) — even if a future commit accidentally
   adds one of those keys to _UI_PREFERENCE_FIELDS, this test fails loudly.
"""

import pytest
from httpx import AsyncClient

# Anything in this list MUST NOT appear in the /ui-preferences response.
# Mirror of _SENSITIVE_FIELDS_FOR_API_KEY in backend/app/api/routes/settings.py
# plus a wider net for any *_password / *_token / *_key suffix.
_SENSITIVE_KEYS = {
    "smtp_password",
    "smtp_username",
    "smtp_from_email",
    "smtp_host",
    "smtp_port",
    "mqtt_password",
    "mqtt_username",
    "mqtt_broker",
    "ha_token",
    "ha_url",
    "prometheus_token",
    "virtual_printer_access_code",
    "ldap_bind_password",
    "ldap_bind_dn",
    "ldap_server_url",
    "external_url",
    "bambu_studio_api_url",
    "orcaslicer_api_url",
    "local_backup_path",
    "github_token",
    "gitea_token",
    "obico_api_key",
    "obico_endpoint_url",
}


class TestUiPreferencesEndpoint:
    """The new public endpoint must work without SETTINGS_READ and must
    never return sensitive fields."""

    @pytest.mark.asyncio
    async def test_endpoint_returns_200_without_auth(self, async_client: AsyncClient):
        """No SETTINGS_READ required — that's the whole point of the endpoint."""
        response = await async_client.get("/api/v1/settings/ui-preferences")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_returns_require_plate_clear(self, async_client: AsyncClient):
        """The field that drove #1293: PrintersPage gates the Clear Plate button
        on this. Must be present in the response."""
        response = await async_client.get("/api/v1/settings/ui-preferences")
        assert response.status_code == 200
        data = response.json()
        assert "require_plate_clear" in data
        # Type must be bool (frontend does === true checks)
        assert isinstance(data["require_plate_clear"], bool)

    @pytest.mark.asyncio
    async def test_returns_expected_field_set(self, async_client: AsyncClient):
        """Pin the exact set of fields the endpoint exposes — adding a sensitive
        field to _UI_PREFERENCE_FIELDS by accident should fail this assert and
        force the author to reconsider."""
        response = await async_client.get("/api/v1/settings/ui-preferences")
        data = response.json()
        expected = {
            "require_plate_clear",
            "check_printer_firmware",
            "camera_view_mode",
            "time_format",
            "date_format",
            "drying_presets",
            "ams_humidity_thresholds",
            "ams_humidity_good",
            "ams_humidity_fair",
            "ams_temp_good",
            "ams_temp_fair",
            "bed_cooled_threshold",
            "nozzle_temp_presets",
            "bed_temp_presets",
            "chamber_temp_presets",
            "fan_speed_presets",
        }
        assert set(data.keys()) == expected

    @pytest.mark.asyncio
    async def test_response_excludes_sensitive_fields(self, async_client: AsyncClient, db_session):
        """Even with sensitive fields seeded in the DB, none of them must
        appear in the response — the endpoint is opt-in, not opt-out."""
        from backend.app.models.settings import Settings

        # Seed every sensitive field with a unique recognizable value so a leak
        # would be obvious in failure output.
        for i, key in enumerate(_SENSITIVE_KEYS):
            db_session.add(Settings(key=key, value=f"SECRET_VALUE_{i}_DO_NOT_LEAK"))
        await db_session.commit()

        response = await async_client.get("/api/v1/settings/ui-preferences")
        assert response.status_code == 200
        data = response.json()

        # No sensitive key should appear in the response keys
        leaked_keys = _SENSITIVE_KEYS & set(data.keys())
        assert leaked_keys == set(), f"Leaked sensitive fields: {leaked_keys}"

        # And the recognizable values shouldn't appear in any value either
        response_text = response.text
        for i in range(len(_SENSITIVE_KEYS)):
            assert f"SECRET_VALUE_{i}_DO_NOT_LEAK" not in response_text, (
                f"Sensitive value index {i} leaked into response body"
            )
