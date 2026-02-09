"""Unit tests for Home Assistant settings with environment variable support.

Tests the get_homeassistant_settings() function in isolation.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_homeassistant_settings_no_env_vars():
    """Test get_homeassistant_settings with no environment variables."""
    from backend.app.api.routes.settings import get_homeassistant_settings

    # Mock database session
    db = AsyncMock(spec=AsyncSession)

    # Mock get_setting to return database values
    with patch("backend.app.api.routes.settings.get_setting") as mock_get_setting:
        mock_get_setting.side_effect = lambda db, key: {
            "ha_url": "http://db-url:8123",
            "ha_token": "db-token",
            "ha_enabled": "true",
        }.get(key, "")

        # Ensure no env vars
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HA_URL", None)
            os.environ.pop("HA_TOKEN", None)

            result = await get_homeassistant_settings(db)

            # Should use database values
            assert result["ha_url"] == "http://db-url:8123"
            assert result["ha_token"] == "db-token"
            assert result["ha_enabled"] is True
            assert result["ha_url_from_env"] is False
            assert result["ha_token_from_env"] is False
            assert result["ha_env_managed"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_homeassistant_settings_with_env_vars():
    """Test get_homeassistant_settings with environment variables set."""
    from backend.app.api.routes.settings import get_homeassistant_settings

    db = AsyncMock(spec=AsyncSession)

    with patch("backend.app.api.routes.settings.get_setting") as mock_get_setting:
        mock_get_setting.side_effect = lambda db, key: {
            "ha_url": "http://db-url:8123",
            "ha_token": "db-token",
            "ha_enabled": "false",
        }.get(key, "")

        # Set environment variables
        with patch.dict(os.environ, {"HA_URL": "http://supervisor/core", "HA_TOKEN": "env-token"}, clear=False):
            result = await get_homeassistant_settings(db)

            # Should use environment values
            assert result["ha_url"] == "http://supervisor/core"
            assert result["ha_token"] == "env-token"
            assert result["ha_enabled"] is True  # Auto-enabled
            assert result["ha_url_from_env"] is True
            assert result["ha_token_from_env"] is True
            assert result["ha_env_managed"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_homeassistant_settings_partial_env_url_only():
    """Test get_homeassistant_settings with only HA_URL set."""
    from backend.app.api.routes.settings import get_homeassistant_settings

    db = AsyncMock(spec=AsyncSession)

    with patch("backend.app.api.routes.settings.get_setting") as mock_get_setting:
        mock_get_setting.side_effect = lambda db, key: {
            "ha_url": "http://db-url:8123",
            "ha_token": "db-token",
            "ha_enabled": "false",
        }.get(key, "")

        # Set only URL env var
        with patch.dict(os.environ, {"HA_URL": "http://supervisor/core"}, clear=False):
            os.environ.pop("HA_TOKEN", None)

            result = await get_homeassistant_settings(db)

            # URL from env, token from database
            assert result["ha_url"] == "http://supervisor/core"
            assert result["ha_token"] == "db-token"
            assert result["ha_enabled"] is False  # Not auto-enabled
            assert result["ha_url_from_env"] is True
            assert result["ha_token_from_env"] is False
            assert result["ha_env_managed"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_homeassistant_settings_partial_env_token_only():
    """Test get_homeassistant_settings with only HA_TOKEN set."""
    from backend.app.api.routes.settings import get_homeassistant_settings

    db = AsyncMock(spec=AsyncSession)

    with patch("backend.app.api.routes.settings.get_setting") as mock_get_setting:
        mock_get_setting.side_effect = lambda db, key: {
            "ha_url": "http://db-url:8123",
            "ha_token": "db-token",
            "ha_enabled": "false",
        }.get(key, "")

        # Set only token env var
        with patch.dict(os.environ, {"HA_TOKEN": "env-token"}, clear=False):
            os.environ.pop("HA_URL", None)

            result = await get_homeassistant_settings(db)

            # URL from database, token from env
            assert result["ha_url"] == "http://db-url:8123"
            assert result["ha_token"] == "env-token"
            assert result["ha_enabled"] is False  # Not auto-enabled
            assert result["ha_url_from_env"] is False
            assert result["ha_token_from_env"] is True
            assert result["ha_env_managed"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_homeassistant_settings_empty_env_vars():
    """Test get_homeassistant_settings with empty environment variables."""
    from backend.app.api.routes.settings import get_homeassistant_settings

    db = AsyncMock(spec=AsyncSession)

    with patch("backend.app.api.routes.settings.get_setting") as mock_get_setting:
        mock_get_setting.side_effect = lambda db, key: {
            "ha_url": "http://db-url:8123",
            "ha_token": "db-token",
            "ha_enabled": "false",
        }.get(key, "")

        # Set empty env vars
        with patch.dict(os.environ, {"HA_URL": "", "HA_TOKEN": ""}, clear=False):
            result = await get_homeassistant_settings(db)

            # Empty env vars treated as not set, should use database values
            assert result["ha_url"] == "http://db-url:8123"
            assert result["ha_token"] == "db-token"
            assert result["ha_enabled"] is False
            assert result["ha_url_from_env"] is False
            assert result["ha_token_from_env"] is False
            assert result["ha_env_managed"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_homeassistant_settings_auto_enable_logic():
    """Test auto-enable behavior with various configurations."""
    from backend.app.api.routes.settings import get_homeassistant_settings

    db = AsyncMock(spec=AsyncSession)

    with patch("backend.app.api.routes.settings.get_setting") as mock_get_setting:
        # Database has ha_enabled=false
        mock_get_setting.side_effect = lambda db, key: {
            "ha_url": "",
            "ha_token": "",
            "ha_enabled": "false",
        }.get(key, "")

        # Test 1: No env vars - use database enabled state
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HA_URL", None)
            os.environ.pop("HA_TOKEN", None)

            result = await get_homeassistant_settings(db)
            assert result["ha_enabled"] is False

        # Test 2: Both env vars set - auto-enable
        with patch.dict(os.environ, {"HA_URL": "http://test", "HA_TOKEN": "token"}, clear=False):
            result = await get_homeassistant_settings(db)
            assert result["ha_enabled"] is True

        # Test 3: Only URL - use database enabled state
        with patch.dict(os.environ, {"HA_URL": "http://test"}, clear=False):
            os.environ.pop("HA_TOKEN", None)

            result = await get_homeassistant_settings(db)
            assert result["ha_enabled"] is False

        # Test 4: Only token - use database enabled state
        with patch.dict(os.environ, {"HA_TOKEN": "token"}, clear=False):
            os.environ.pop("HA_URL", None)

            result = await get_homeassistant_settings(db)
            assert result["ha_enabled"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_homeassistant_settings_env_vars_override_enabled_true():
    """Test that env vars auto-enable even when database has ha_enabled=true."""
    from backend.app.api.routes.settings import get_homeassistant_settings

    db = AsyncMock(spec=AsyncSession)

    with patch("backend.app.api.routes.settings.get_setting") as mock_get_setting:
        # Database has ha_enabled=true
        mock_get_setting.side_effect = lambda db, key: {
            "ha_url": "http://db-url:8123",
            "ha_token": "db-token",
            "ha_enabled": "true",
        }.get(key, "")

        # Both env vars set - should still be enabled
        with patch.dict(os.environ, {"HA_URL": "http://supervisor/core", "HA_TOKEN": "env-token"}, clear=False):
            result = await get_homeassistant_settings(db)

            assert result["ha_enabled"] is True  # Auto-enabled by env vars
            assert result["ha_url"] == "http://supervisor/core"
            assert result["ha_token"] == "env-token"
            assert result["ha_env_managed"] is True
