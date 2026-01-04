"""Integration tests for Notifications API endpoints.

Tests the full request/response cycle for /api/v1/notifications/ endpoints.
"""

import pytest
from httpx import AsyncClient


class TestNotificationsAPI:
    """Integration tests for /api/v1/notifications/ endpoints."""

    # ========================================================================
    # List endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_notification_providers_empty(self, async_client: AsyncClient):
        """Verify empty list is returned when no providers exist."""
        response = await async_client.get("/api/v1/notifications/")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_notification_providers_with_data(
        self, async_client: AsyncClient, notification_provider_factory, db_session
    ):
        """Verify list returns existing providers."""
        _provider = await notification_provider_factory(name="Test Provider")

        response = await async_client.get("/api/v1/notifications/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(p["name"] == "Test Provider" for p in data)

    # ========================================================================
    # Create endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_callmebot_provider(self, async_client: AsyncClient):
        """Verify callmebot notification provider can be created."""
        data = {
            "name": "Test CallMeBot",
            "provider_type": "callmebot",
            "enabled": True,
            "config": {"phone_number": "+1234567890", "api_key": "test-api-key"},
            "on_print_start": True,
            "on_print_complete": True,
            "on_print_failed": True,
            "on_print_stopped": False,
        }

        response = await async_client.post("/api/v1/notifications/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Test CallMeBot"
        assert result["provider_type"] == "callmebot"
        assert result["on_print_start"] is True
        assert result["on_print_stopped"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_ntfy_provider(self, async_client: AsyncClient):
        """Verify ntfy notification provider can be created."""
        data = {
            "name": "Test Ntfy",
            "provider_type": "ntfy",
            "enabled": True,
            "config": {
                "server": "https://ntfy.sh",
                "topic": "test-topic",
            },
            "on_print_complete": True,
        }

        response = await async_client.post("/api/v1/notifications/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["provider_type"] == "ntfy"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_provider_with_printer(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify provider can be linked to specific printer."""
        printer = await printer_factory(name="Test Printer")

        data = {
            "name": "Printer Ntfy",
            "provider_type": "ntfy",
            "config": {"server": "https://ntfy.sh", "topic": "test-topic"},
            "printer_id": printer.id,
        }

        response = await async_client.post("/api/v1/notifications/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["printer_id"] == printer.id

    # ========================================================================
    # Get single endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_notification_provider(
        self, async_client: AsyncClient, notification_provider_factory, db_session
    ):
        """Verify single provider can be retrieved."""
        provider = await notification_provider_factory(name="Get Test Provider")

        response = await async_client.get(f"/api/v1/notifications/{provider.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["id"] == provider.id
        assert result["name"] == "Get Test Provider"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_provider_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent provider."""
        response = await async_client.get("/api/v1/notifications/9999")

        assert response.status_code == 404

    # ========================================================================
    # Update endpoints (CRITICAL - toggle persistence)
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_event_toggles(self, async_client: AsyncClient, notification_provider_factory, db_session):
        """CRITICAL: Verify notification event toggles persist correctly."""
        provider = await notification_provider_factory(
            on_print_start=True,
            on_print_complete=True,
            on_print_stopped=False,
        )

        # Toggle on_print_stopped to True
        response = await async_client.patch(f"/api/v1/notifications/{provider.id}", json={"on_print_stopped": True})

        assert response.status_code == 200
        assert response.json()["on_print_stopped"] is True

        # Verify change persisted
        response = await async_client.get(f"/api/v1/notifications/{provider.id}")
        assert response.json()["on_print_stopped"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_ams_alarm_toggles(self, async_client: AsyncClient, notification_provider_factory, db_session):
        """CRITICAL: Verify AMS alarm toggles persist correctly."""
        provider = await notification_provider_factory(
            on_ams_humidity_high=False,
            on_ams_temperature_high=False,
        )

        # Enable AMS alarms
        response = await async_client.patch(
            f"/api/v1/notifications/{provider.id}",
            json={
                "on_ams_humidity_high": True,
                "on_ams_temperature_high": True,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["on_ams_humidity_high"] is True
        assert result["on_ams_temperature_high"] is True

        # Verify persistence
        response = await async_client.get(f"/api/v1/notifications/{provider.id}")
        result = response.json()
        assert result["on_ams_humidity_high"] is True
        assert result["on_ams_temperature_high"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enable_disable_provider(self, async_client: AsyncClient, notification_provider_factory, db_session):
        """Verify provider can be enabled/disabled."""
        provider = await notification_provider_factory(enabled=True)

        # Disable
        response = await async_client.patch(f"/api/v1/notifications/{provider.id}", json={"enabled": False})

        assert response.status_code == 200
        assert response.json()["enabled"] is False

        # Enable
        response = await async_client.patch(f"/api/v1/notifications/{provider.id}", json={"enabled": True})

        assert response.status_code == 200
        assert response.json()["enabled"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_quiet_hours(self, async_client: AsyncClient, notification_provider_factory, db_session):
        """Verify quiet hours can be configured."""
        provider = await notification_provider_factory(quiet_hours_enabled=False)

        response = await async_client.patch(
            f"/api/v1/notifications/{provider.id}",
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:00",
                "quiet_hours_end": "07:00",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["quiet_hours_enabled"] is True
        assert result["quiet_hours_start"] == "22:00"
        assert result["quiet_hours_end"] == "07:00"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_daily_digest(self, async_client: AsyncClient, notification_provider_factory, db_session):
        """Verify daily digest can be configured."""
        provider = await notification_provider_factory(daily_digest_enabled=False)

        response = await async_client.patch(
            f"/api/v1/notifications/{provider.id}",
            json={
                "daily_digest_enabled": True,
                "daily_digest_time": "09:00",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["daily_digest_enabled"] is True
        assert result["daily_digest_time"] == "09:00"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_multiple_event_toggles(
        self, async_client: AsyncClient, notification_provider_factory, db_session
    ):
        """Verify multiple event toggles can be updated at once."""
        provider = await notification_provider_factory(
            on_print_start=True,
            on_print_complete=True,
            on_print_failed=True,
            on_print_stopped=False,
            on_printer_offline=False,
        )

        response = await async_client.patch(
            f"/api/v1/notifications/{provider.id}",
            json={
                "on_print_start": False,
                "on_print_stopped": True,
                "on_printer_offline": True,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["on_print_start"] is False
        assert result["on_print_stopped"] is True
        assert result["on_printer_offline"] is True
        # Unchanged fields should remain
        assert result["on_print_complete"] is True
        assert result["on_print_failed"] is True

    # ========================================================================
    # Test notification endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_test_notification(
        self, async_client: AsyncClient, notification_provider_factory, mock_httpx_client, db_session
    ):
        """Verify test notification can be sent."""
        provider = await notification_provider_factory()

        response = await async_client.post(f"/api/v1/notifications/{provider.id}/test")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_test_notification_disabled_provider(
        self, async_client: AsyncClient, notification_provider_factory, db_session
    ):
        """Verify test notification works even for disabled provider."""
        provider = await notification_provider_factory(enabled=False)

        response = await async_client.post(f"/api/v1/notifications/{provider.id}/test")

        # Test should still work for disabled providers
        assert response.status_code == 200

    # ========================================================================
    # Delete endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_notification_provider(
        self, async_client: AsyncClient, notification_provider_factory, db_session
    ):
        """Verify notification provider can be deleted."""
        provider = await notification_provider_factory()
        provider_id = provider.id

        response = await async_client.delete(f"/api/v1/notifications/{provider_id}")

        assert response.status_code == 200

        # Verify deleted
        response = await async_client.get(f"/api/v1/notifications/{provider_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_nonexistent_provider(self, async_client: AsyncClient):
        """Verify deleting non-existent provider returns 404."""
        response = await async_client.delete("/api/v1/notifications/9999")

        assert response.status_code == 404


class TestNotificationTemplatesAPI:
    """Integration tests for /api/v1/notification-templates/ endpoints."""

    @pytest.fixture
    async def seeded_templates(self, db_session):
        """Seed notification templates for tests."""
        from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate

        templates = []
        for template_data in DEFAULT_TEMPLATES:
            template = NotificationTemplate(**template_data)
            db_session.add(template)
            templates.append(template)
        await db_session.commit()
        for template in templates:
            await db_session.refresh(template)
        return templates

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_templates(self, async_client: AsyncClient, seeded_templates):
        """Verify default templates are seeded and can be listed."""
        response = await async_client.get("/api/v1/notification-templates/")

        assert response.status_code == 200
        templates = response.json()
        # Should have default templates seeded
        assert len(templates) >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_template_by_id(self, async_client: AsyncClient, seeded_templates):
        """Verify template can be retrieved by ID."""
        # Get first template ID from seeded data
        template_id = seeded_templates[0].id

        response = await async_client.get(f"/api/v1/notification-templates/{template_id}")

        assert response.status_code == 200
        template = response.json()
        assert template["id"] == template_id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_template(self, async_client: AsyncClient, seeded_templates):
        """Verify template can be updated."""
        # Get first template
        template_id = seeded_templates[0].id

        # Update it (route uses PUT, not PATCH)
        response = await async_client.put(
            f"/api/v1/notification-templates/{template_id}",
            json={
                "title_template": "Custom Title: {printer}",
                "body_template": "Custom body for {filename}",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["title_template"] == "Custom Title: {printer}"
        assert result["body_template"] == "Custom body for {filename}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_template_to_default(self, async_client: AsyncClient, seeded_templates):
        """Verify template can be reset to default."""
        template_id = seeded_templates[0].id

        response = await async_client.post(f"/api/v1/notification-templates/{template_id}/reset")

        assert response.status_code == 200
        result = response.json()
        assert result["is_default"] is True
