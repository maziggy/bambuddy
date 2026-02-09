"""Integration tests for Settings API endpoints.

Tests the full request/response cycle for /api/v1/settings/ endpoints.
"""

import os

import pytest
from httpx import AsyncClient


class TestSettingsAPI:
    """Integration tests for /api/v1/settings/ endpoints."""

    # ========================================================================
    # Get settings
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_settings(self, async_client: AsyncClient):
        """Verify settings can be retrieved."""
        response = await async_client.get("/api/v1/settings/")

        assert response.status_code == 200
        result = response.json()
        # Check for actual settings fields
        assert "auto_archive" in result
        assert "currency" in result
        assert "date_format" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_settings_has_defaults(self, async_client: AsyncClient):
        """Verify default settings values are returned."""
        response = await async_client.get("/api/v1/settings/")

        assert response.status_code == 200
        result = response.json()
        # Verify some default values
        assert isinstance(result["auto_archive"], bool)
        assert isinstance(result["currency"], str)

    # ========================================================================
    # Update settings
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_auto_archive(self, async_client: AsyncClient):
        """Verify auto_archive can be updated."""
        # First get current value
        response = await async_client.get("/api/v1/settings/")
        original = response.json()["auto_archive"]

        # Update to opposite value
        new_value = not original
        response = await async_client.put("/api/v1/settings/", json={"auto_archive": new_value})

        assert response.status_code == 200
        assert response.json()["auto_archive"] == new_value

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_currency(self, async_client: AsyncClient):
        """Verify currency can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"currency": "EUR"})

        assert response.status_code == 200
        assert response.json()["currency"] == "EUR"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_date_format(self, async_client: AsyncClient):
        """Verify date format can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"date_format": "eu"})

        assert response.status_code == 200
        assert response.json()["date_format"] == "eu"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_time_format(self, async_client: AsyncClient):
        """Verify time format can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"time_format": "24h"})

        assert response.status_code == 200
        assert response.json()["time_format"] == "24h"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_filament_cost(self, async_client: AsyncClient):
        """Verify default filament cost can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"default_filament_cost": 30.0})

        assert response.status_code == 200
        assert response.json()["default_filament_cost"] == 30.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_energy_cost(self, async_client: AsyncClient):
        """Verify energy cost can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"energy_cost_per_kwh": 0.20})

        assert response.status_code == 200
        assert response.json()["energy_cost_per_kwh"] == 0.20

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_multiple_settings(self, async_client: AsyncClient):
        """Verify multiple settings can be updated at once."""
        response = await async_client.put(
            "/api/v1/settings/",
            json={
                "currency": "GBP",
                "date_format": "iso",
                "time_format": "12h",
                "save_thumbnails": False,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["currency"] == "GBP"
        assert result["date_format"] == "iso"
        assert result["time_format"] == "12h"
        assert result["save_thumbnails"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_spoolman_settings(self, async_client: AsyncClient):
        """Verify Spoolman settings can be updated."""
        response = await async_client.put(
            "/api/v1/settings/",
            json={
                "spoolman_enabled": True,
                "spoolman_url": "http://localhost:7912",
                "spoolman_sync_mode": "manual",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["spoolman_enabled"] is True
        assert result["spoolman_url"] == "http://localhost:7912"
        assert result["spoolman_sync_mode"] == "manual"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_ams_thresholds(self, async_client: AsyncClient):
        """Verify AMS threshold settings can be updated."""
        response = await async_client.put(
            "/api/v1/settings/",
            json={
                "ams_humidity_good": 35,
                "ams_humidity_fair": 55,
                "ams_temp_good": 25.0,
                "ams_temp_fair": 32.0,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["ams_humidity_good"] == 35
        assert result["ams_humidity_fair"] == 55
        assert result["ams_temp_good"] == 25.0
        assert result["ams_temp_fair"] == 32.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_notification_language(self, async_client: AsyncClient):
        """Verify notification language can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"notification_language": "de"})

        assert response.status_code == 200
        assert response.json()["notification_language"] == "de"

    # ========================================================================
    # Settings persistence tests
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_theme_settings(self, async_client: AsyncClient):
        """Verify theme settings can be updated."""
        response = await async_client.put(
            "/api/v1/settings/",
            json={
                "dark_style": "glow",
                "dark_background": "forest",
                "dark_accent": "teal",
                "light_style": "vibrant",
                "light_background": "warm",
                "light_accent": "blue",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["dark_style"] == "glow"
        assert result["dark_background"] == "forest"
        assert result["dark_accent"] == "teal"
        assert result["light_style"] == "vibrant"
        assert result["light_background"] == "warm"
        assert result["light_accent"] == "blue"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_settings_persist_after_update(self, async_client: AsyncClient):
        """CRITICAL: Verify settings changes persist across requests."""
        # Update settings
        await async_client.put("/api/v1/settings/", json={"currency": "JPY", "check_updates": False})

        # Verify persistence in new request
        response = await async_client.get("/api/v1/settings/")
        result = response.json()
        assert result["currency"] == "JPY"
        assert result["check_updates"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_check_printer_firmware(self, async_client: AsyncClient):
        """Verify check_printer_firmware can be updated."""
        # Default should be True
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["check_printer_firmware"] is True

        # Update to False
        response = await async_client.put("/api/v1/settings/", json={"check_printer_firmware": False})
        assert response.status_code == 200
        assert response.json()["check_printer_firmware"] is False

        # Verify persistence
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["check_printer_firmware"] is False

        # Update back to True
        response = await async_client.put("/api/v1/settings/", json={"check_printer_firmware": True})
        assert response.status_code == 200
        assert response.json()["check_printer_firmware"] is True

    # ========================================================================
    # MQTT settings tests
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_mqtt_settings(self, async_client: AsyncClient):
        """Verify MQTT settings can be updated."""
        response = await async_client.put(
            "/api/v1/settings/",
            json={
                "mqtt_enabled": True,
                "mqtt_broker": "mqtt.example.com",
                "mqtt_port": 8883,
                "mqtt_username": "testuser",
                "mqtt_password": "testpass",
                "mqtt_topic_prefix": "myprefix",
                "mqtt_use_tls": True,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["mqtt_enabled"] is True
        assert result["mqtt_broker"] == "mqtt.example.com"
        assert result["mqtt_port"] == 8883
        assert result["mqtt_username"] == "testuser"
        assert result["mqtt_password"] == "testpass"
        assert result["mqtt_topic_prefix"] == "myprefix"
        assert result["mqtt_use_tls"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mqtt_status_endpoint(self, async_client: AsyncClient):
        """Verify MQTT status endpoint returns expected fields."""
        response = await async_client.get("/api/v1/settings/mqtt/status")

        assert response.status_code == 200
        result = response.json()
        assert "enabled" in result
        assert "connected" in result
        assert "broker" in result
        assert "port" in result
        assert "topic_prefix" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mqtt_defaults(self, async_client: AsyncClient):
        """Verify MQTT has correct default values."""
        # Reset MQTT settings to defaults
        await async_client.put(
            "/api/v1/settings/",
            json={
                "mqtt_enabled": False,
                "mqtt_broker": "",
                "mqtt_port": 1883,
                "mqtt_username": "",
                "mqtt_password": "",
                "mqtt_topic_prefix": "bambuddy",
                "mqtt_use_tls": False,
            },
        )

        response = await async_client.get("/api/v1/settings/")
        result = response.json()

        assert result["mqtt_enabled"] is False
        assert result["mqtt_port"] == 1883
        assert result["mqtt_topic_prefix"] == "bambuddy"
        assert result["mqtt_use_tls"] is False

    # ========================================================================
    # Camera settings tests
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_camera_view_mode(self, async_client: AsyncClient):
        """Verify camera view mode can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"camera_view_mode": "embedded"})

        assert response.status_code == 200
        assert response.json()["camera_view_mode"] == "embedded"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_camera_view_mode_persists(self, async_client: AsyncClient):
        """CRITICAL: Verify camera view mode persists after update."""
        # Update to embedded
        await async_client.put("/api/v1/settings/", json={"camera_view_mode": "embedded"})

        # Verify persistence in new request
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["camera_view_mode"] == "embedded"

        # Update back to window
        await async_client.put("/api/v1/settings/", json={"camera_view_mode": "window"})

        # Verify persistence
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["camera_view_mode"] == "window"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_camera_view_mode_default(self, async_client: AsyncClient):
        """Verify camera view mode has correct default value."""
        # Reset by requesting settings (default should be 'window')
        response = await async_client.get("/api/v1/settings/")
        result = response.json()

        assert "camera_view_mode" in result
        # Default is 'window' as defined in schema
        assert result["camera_view_mode"] in ["window", "embedded"]

    # ========================================================================
    # Per-printer mapping settings tests
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_per_printer_mapping_expanded(self, async_client: AsyncClient):
        """Verify per_printer_mapping_expanded can be updated."""
        response = await async_client.put("/api/v1/settings/", json={"per_printer_mapping_expanded": True})

        assert response.status_code == 200
        assert response.json()["per_printer_mapping_expanded"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_per_printer_mapping_expanded_persists(self, async_client: AsyncClient):
        """CRITICAL: Verify per_printer_mapping_expanded persists after update."""
        # Update to True
        await async_client.put("/api/v1/settings/", json={"per_printer_mapping_expanded": True})

        # Verify persistence in new request
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["per_printer_mapping_expanded"] is True

        # Update back to False
        await async_client.put("/api/v1/settings/", json={"per_printer_mapping_expanded": False})

        # Verify persistence
        response = await async_client.get("/api/v1/settings/")
        assert response.json()["per_printer_mapping_expanded"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_per_printer_mapping_expanded_default(self, async_client: AsyncClient):
        """Verify per_printer_mapping_expanded has correct default value."""
        response = await async_client.get("/api/v1/settings/")
        result = response.json()

        assert "per_printer_mapping_expanded" in result
        # Default is False as defined in schema
        assert isinstance(result["per_printer_mapping_expanded"], bool)

    # ========================================================================
    # Home Assistant environment variable tests
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_default_no_env_vars(self, async_client: AsyncClient):
        """Verify HA settings work without environment variables (default behavior)."""
        # Ensure no env vars are set
        os.environ.pop("HA_URL", None)
        os.environ.pop("HA_TOKEN", None)

        response = await async_client.get("/api/v1/settings/")
        result = response.json()

        assert response.status_code == 200
        assert "ha_enabled" in result
        assert "ha_url" in result
        assert "ha_token" in result
        assert "ha_url_from_env" in result
        assert "ha_token_from_env" in result
        assert "ha_env_managed" in result

        # Default values without env vars
        assert result["ha_url_from_env"] is False
        assert result["ha_token_from_env"] is False
        assert result["ha_env_managed"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_with_both_env_vars(self, async_client: AsyncClient):
        """Verify HA settings are overridden when both env vars are set."""
        # Set environment variables
        os.environ["HA_URL"] = "http://supervisor/core"
        os.environ["HA_TOKEN"] = "test-token-12345"

        try:
            response = await async_client.get("/api/v1/settings/")
            result = response.json()

            assert response.status_code == 200

            # Verify env var values are used
            assert result["ha_url"] == "http://supervisor/core"
            assert result["ha_token"] == "test-token-12345"

            # Verify metadata fields
            assert result["ha_url_from_env"] is True
            assert result["ha_token_from_env"] is True
            assert result["ha_env_managed"] is True

            # Verify auto-enable behavior
            assert result["ha_enabled"] is True

        finally:
            # Clean up
            os.environ.pop("HA_URL", None)
            os.environ.pop("HA_TOKEN", None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_with_only_url_env_var(self, async_client: AsyncClient):
        """Verify partial configuration when only HA_URL is set."""
        # Set only URL env var
        os.environ["HA_URL"] = "http://supervisor/core"
        os.environ.pop("HA_TOKEN", None)

        try:
            response = await async_client.get("/api/v1/settings/")
            result = response.json()

            assert response.status_code == 200

            # Verify URL is from env, token is from database
            assert result["ha_url"] == "http://supervisor/core"
            assert result["ha_url_from_env"] is True
            assert result["ha_token_from_env"] is False
            assert result["ha_env_managed"] is False

            # No auto-enable with partial config
            assert result["ha_enabled"] is False  # Database default

        finally:
            os.environ.pop("HA_URL", None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_with_only_token_env_var(self, async_client: AsyncClient):
        """Verify partial configuration when only HA_TOKEN is set."""
        # Set only token env var
        os.environ.pop("HA_URL", None)
        os.environ["HA_TOKEN"] = "test-token-12345"

        try:
            response = await async_client.get("/api/v1/settings/")
            result = response.json()

            assert response.status_code == 200

            # Verify token is from env, URL is from database
            assert result["ha_token"] == "test-token-12345"
            assert result["ha_url_from_env"] is False
            assert result["ha_token_from_env"] is True
            assert result["ha_env_managed"] is False

            # No auto-enable with partial config
            assert result["ha_enabled"] is False  # Database default

        finally:
            os.environ.pop("HA_TOKEN", None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_env_vars_override_database(self, async_client: AsyncClient):
        """Verify environment variables take precedence over database values."""
        # First, set database values
        await async_client.put(
            "/api/v1/settings/",
            json={
                "ha_enabled": True,
                "ha_url": "http://database-url:8123",
                "ha_token": "database-token",
            },
        )

        # Verify database values are set
        response = await async_client.get("/api/v1/settings/")
        result = response.json()
        assert result["ha_url"] == "http://database-url:8123"
        assert result["ha_token"] == "database-token"

        # Now set environment variables
        os.environ["HA_URL"] = "http://env-url/core"
        os.environ["HA_TOKEN"] = "env-token-xyz"

        try:
            response = await async_client.get("/api/v1/settings/")
            result = response.json()

            # Verify env vars override database
            assert result["ha_url"] == "http://env-url/core"
            assert result["ha_token"] == "env-token-xyz"
            assert result["ha_url_from_env"] is True
            assert result["ha_token_from_env"] is True
            assert result["ha_env_managed"] is True
            assert result["ha_enabled"] is True

        finally:
            os.environ.pop("HA_URL", None)
            os.environ.pop("HA_TOKEN", None)

        # Verify database values are still there after removing env vars
        response = await async_client.get("/api/v1/settings/")
        result = response.json()
        assert result["ha_url"] == "http://database-url:8123"
        assert result["ha_token"] == "database-token"
        assert result["ha_url_from_env"] is False
        assert result["ha_token_from_env"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_database_updates_accepted_but_ignored(self, async_client: AsyncClient):
        """Verify database updates are accepted but have no effect when env vars are set."""
        # Set environment variables
        os.environ["HA_URL"] = "http://supervisor/core"
        os.environ["HA_TOKEN"] = "env-token"

        try:
            # Attempt to update via API
            response = await async_client.put(
                "/api/v1/settings/",
                json={
                    "ha_url": "http://different-url:8123",
                    "ha_token": "different-token",
                },
            )

            # Update should succeed
            assert response.status_code == 200

            # But values should still be from env vars
            result = response.json()
            assert result["ha_url"] == "http://supervisor/core"
            assert result["ha_token"] == "env-token"
            assert result["ha_url_from_env"] is True
            assert result["ha_token_from_env"] is True

        finally:
            os.environ.pop("HA_URL", None)
            os.environ.pop("HA_TOKEN", None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_empty_env_vars_treated_as_not_set(self, async_client: AsyncClient):
        """Verify empty environment variables are treated as not set."""
        # Set empty env vars
        os.environ["HA_URL"] = ""
        os.environ["HA_TOKEN"] = ""

        try:
            response = await async_client.get("/api/v1/settings/")
            result = response.json()

            # Empty env vars should be treated as not set
            assert result["ha_url_from_env"] is False
            assert result["ha_token_from_env"] is False
            assert result["ha_env_managed"] is False

        finally:
            os.environ.pop("HA_URL", None)
            os.environ.pop("HA_TOKEN", None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ha_settings_can_be_updated_normally_without_env_vars(self, async_client: AsyncClient):
        """Verify HA settings can be updated normally when env vars are not set."""
        # Ensure no env vars
        os.environ.pop("HA_URL", None)
        os.environ.pop("HA_TOKEN", None)

        # Update HA settings
        response = await async_client.put(
            "/api/v1/settings/",
            json={
                "ha_enabled": True,
                "ha_url": "http://192.168.1.100:8123",
                "ha_token": "my-long-lived-token",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["ha_enabled"] is True
        assert result["ha_url"] == "http://192.168.1.100:8123"
        assert result["ha_token"] == "my-long-lived-token"
        assert result["ha_url_from_env"] is False
        assert result["ha_token_from_env"] is False
        assert result["ha_env_managed"] is False

        # Verify persistence
        response = await async_client.get("/api/v1/settings/")
        result = response.json()
        assert result["ha_enabled"] is True
        assert result["ha_url"] == "http://192.168.1.100:8123"
        assert result["ha_token"] == "my-long-lived-token"


class TestSimplifiedBackupRestore:
    """Integration tests for the simplified backup/restore endpoints (ZIP-based).

    Note: Tests that require actual file operations (backup creation) are skipped
    because the test suite uses an in-memory database. These tests focus on
    validation and error handling which don't require file I/O.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_requires_zip_file(self, async_client: AsyncClient):
        """Verify restore rejects non-ZIP files."""
        files = {"file": ("backup.txt", b"not a zip file", "text/plain")}
        response = await async_client.post("/api/v1/settings/restore", files=files)

        assert response.status_code == 400
        assert "zip" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_requires_database_in_zip(self, async_client: AsyncClient):
        """Verify restore rejects ZIP without database file."""
        import io
        import zipfile

        # Create a ZIP without bambuddy.db
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("dummy.txt", "dummy content")
        zip_buffer.seek(0)

        files = {"file": ("backup.zip", zip_buffer.read(), "application/zip")}
        response = await async_client.post("/api/v1/settings/restore", files=files)

        assert response.status_code == 400
        assert "missing bambuddy.db" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_invalid_zip(self, async_client: AsyncClient):
        """Verify restore rejects corrupted ZIP files."""
        files = {"file": ("backup.zip", b"not valid zip content", "application/zip")}
        response = await async_client.post("/api/v1/settings/restore", files=files)

        assert response.status_code == 400
        assert "not a valid zip" in response.json()["detail"].lower()
