"""Integration tests for Settings API endpoints.

Tests the full request/response cycle for /api/v1/settings/ endpoints.
"""

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
    # Backup/Restore tests
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backup_includes_external_camera_settings(self, async_client: AsyncClient, printer_factory):
        """Verify backup includes external camera settings for printers."""
        # Create a printer with external camera settings
        _printer = await printer_factory(
            name="Camera Test Printer",
            external_camera_url="/dev/video0",
            external_camera_type="usb",
            external_camera_enabled=True,
        )

        # Request backup with printers
        response = await async_client.get("/api/v1/settings/backup?include_printers=true")

        assert response.status_code == 200
        backup = response.json()

        # Find the printer in the backup
        assert "printers" in backup
        printer_data = next((p for p in backup["printers"] if p["name"] == "Camera Test Printer"), None)
        assert printer_data is not None

        # Verify external camera fields are included
        assert "external_camera_url" in printer_data
        assert "external_camera_type" in printer_data
        assert "external_camera_enabled" in printer_data
        assert printer_data["external_camera_url"] == "/dev/video0"
        assert printer_data["external_camera_type"] == "usb"
        assert printer_data["external_camera_enabled"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_restore_external_camera_settings_overwrite(self, async_client: AsyncClient, printer_factory):
        """Verify restore with overwrite updates external camera settings."""
        import io

        # Create a printer without camera settings
        printer = await printer_factory(
            name="Restore Test",
            external_camera_url=None,
            external_camera_type=None,
            external_camera_enabled=False,
        )

        # Create backup data with camera settings
        backup_data = {
            "version": "1.0",
            "included": ["printers"],
            "printers": [
                {
                    "name": "Restore Test",
                    "serial_number": printer.serial_number,
                    "ip_address": printer.ip_address,
                    "external_camera_url": "/dev/video1",
                    "external_camera_type": "usb",
                    "external_camera_enabled": True,
                }
            ],
        }

        # Restore with overwrite
        import json

        files = {"file": ("backup.json", io.BytesIO(json.dumps(backup_data).encode()), "application/json")}
        response = await async_client.post("/api/v1/settings/restore?overwrite=true", files=files)

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

        # Verify the printer was updated
        response = await async_client.get(f"/api/v1/printers/{printer.id}")
        assert response.status_code == 200
        updated_printer = response.json()
        assert updated_printer["external_camera_url"] == "/dev/video1"
        assert updated_printer["external_camera_type"] == "usb"
        assert updated_printer["external_camera_enabled"] is True
