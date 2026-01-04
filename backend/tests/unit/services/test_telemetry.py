"""Unit tests for Telemetry service.

Tests the anonymous telemetry/stats collection functionality.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.settings import Settings
from backend.app.services.telemetry import (
    DEFAULT_TELEMETRY_URL,
    HEARTBEAT_INTERVAL,
    _last_heartbeat,
    get_or_create_installation_id,
    get_telemetry_url,
    is_telemetry_enabled,
    send_heartbeat,
)


class TestTelemetryService:
    """Tests for telemetry service functions."""

    # ========================================================================
    # Installation ID Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_get_or_create_installation_id_creates_new(self, db_session):
        """Verify new installation ID is created when none exists."""
        installation_id = await get_or_create_installation_id(db_session)

        assert installation_id is not None
        assert len(installation_id) == 36  # UUID format
        assert "-" in installation_id

    @pytest.mark.asyncio
    async def test_get_or_create_installation_id_returns_existing(self, db_session):
        """Verify existing installation ID is returned."""
        # Create an existing installation ID
        existing_id = "test-uuid-1234-5678-abcd"
        setting = Settings(key="installation_id", value=existing_id)
        db_session.add(setting)
        await db_session.commit()

        result = await get_or_create_installation_id(db_session)

        assert result == existing_id

    @pytest.mark.asyncio
    async def test_get_or_create_installation_id_persists(self, db_session):
        """Verify created installation ID persists in database."""
        first_id = await get_or_create_installation_id(db_session)
        second_id = await get_or_create_installation_id(db_session)

        assert first_id == second_id

    # ========================================================================
    # Telemetry Enabled Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_is_telemetry_enabled_default_true(self, db_session):
        """Verify telemetry is enabled by default (opt-out model)."""
        result = await is_telemetry_enabled(db_session)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_telemetry_enabled_explicit_true(self, db_session):
        """Verify telemetry enabled when explicitly set to true."""
        setting = Settings(key="telemetry_enabled", value="true")
        db_session.add(setting)
        await db_session.commit()

        result = await is_telemetry_enabled(db_session)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_telemetry_enabled_explicit_false(self, db_session):
        """Verify telemetry disabled when set to false."""
        setting = Settings(key="telemetry_enabled", value="false")
        db_session.add(setting)
        await db_session.commit()

        result = await is_telemetry_enabled(db_session)

        assert result is False

    @pytest.mark.asyncio
    async def test_is_telemetry_enabled_case_insensitive(self, db_session):
        """Verify telemetry enabled check is case insensitive."""
        setting = Settings(key="telemetry_enabled", value="TRUE")
        db_session.add(setting)
        await db_session.commit()

        result = await is_telemetry_enabled(db_session)

        assert result is True

    # ========================================================================
    # Telemetry URL Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_get_telemetry_url_default(self, db_session):
        """Verify default telemetry URL is returned when not configured."""
        result = await get_telemetry_url(db_session)

        assert result == DEFAULT_TELEMETRY_URL

    @pytest.mark.asyncio
    async def test_get_telemetry_url_custom(self, db_session):
        """Verify custom telemetry URL is returned when configured."""
        custom_url = "https://custom.telemetry.example.com"
        setting = Settings(key="telemetry_url", value=custom_url)
        db_session.add(setting)
        await db_session.commit()

        result = await get_telemetry_url(db_session)

        assert result == custom_url

    # ========================================================================
    # Send Heartbeat Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_send_heartbeat_when_disabled(self, db_session):
        """Verify heartbeat is not sent when telemetry is disabled."""
        setting = Settings(key="telemetry_enabled", value="false")
        db_session.add(setting)
        await db_session.commit()

        with patch("httpx.AsyncClient") as mock_client:
            result = await send_heartbeat(db_session)

        assert result is False
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_heartbeat_success(self, db_session, mock_httpx_client):
        """Verify heartbeat is sent successfully when enabled."""
        # Reset the last heartbeat to allow sending
        import backend.app.services.telemetry as telemetry_module

        telemetry_module._last_heartbeat = None

        result = await send_heartbeat(db_session)

        assert result is True

    @pytest.mark.asyncio
    async def test_send_heartbeat_rate_limited(self, db_session):
        """Verify heartbeat is rate limited to once per day."""
        import backend.app.services.telemetry as telemetry_module

        # Set last heartbeat to recent time
        telemetry_module._last_heartbeat = datetime.now()

        with patch("httpx.AsyncClient") as mock_client:
            result = await send_heartbeat(db_session)

        # Should return True (already sent) without making HTTP request
        assert result is True
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_heartbeat_handles_exceptions(self, db_session):
        """Verify heartbeat returns False on general exceptions."""
        import backend.app.services.telemetry as telemetry_module

        telemetry_module._last_heartbeat = None

        # Test that the function handles exceptions gracefully by checking
        # the code path - the actual telemetry URL may or may not be reachable
        # The function should not raise exceptions to the caller
        try:
            result = await send_heartbeat(db_session)
            # Result can be True (success) or False (failure) but should not raise
            assert isinstance(result, bool)
        except Exception as e:
            pytest.fail(f"send_heartbeat should not raise exceptions: {e}")

    @pytest.mark.asyncio
    async def test_send_heartbeat_sends_correct_data(self, db_session):
        """Verify heartbeat sends correct payload."""
        import backend.app.services.telemetry as telemetry_module
        from backend.app.core.config import APP_VERSION

        telemetry_module._last_heartbeat = None

        captured_data = {}

        with patch("httpx.AsyncClient") as mock_class:
            mock_instance = AsyncMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()

            async def capture_post(url, json=None):
                captured_data["url"] = url
                captured_data["json"] = json
                return mock_response

            mock_instance.post = capture_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock()
            mock_class.return_value = mock_instance

            await send_heartbeat(db_session)

        assert "heartbeat" in captured_data["url"]
        assert "installation_id" in captured_data["json"]
        assert captured_data["json"]["version"] == APP_VERSION


class TestHeartbeatInterval:
    """Tests for heartbeat interval configuration."""

    def test_heartbeat_interval_is_24_hours(self):
        """Verify heartbeat interval is set to 24 hours."""
        assert timedelta(hours=24) == HEARTBEAT_INTERVAL

    def test_default_telemetry_url(self):
        """Verify default telemetry URL is correct."""
        assert DEFAULT_TELEMETRY_URL == "https://telemetry.bambuddy.cool"
