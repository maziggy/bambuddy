"""Startup WLED initialization keeps inactive printers offline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import initialize_wled_printers
from backend.app.services.wled import WLEDManager


@pytest.mark.asyncio
async def test_startup_sets_only_inactive_printers_offline():
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    inactive_config = {"enabled": True, "base_url": "http://wled.local", "presets": {"offline": 9}}
    active_config = {"enabled": True, "base_url": "http://wled.local", "presets": {"offline": 10}}
    db.execute.return_value.all.return_value = [
        (1, inactive_config, False),
        (2, active_config, True),
    ]
    with (
        patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value=False)),
        patch("backend.app.main.wled_manager") as manager,
    ):
        await initialize_wled_printers(db)
    assert manager.configure_printer.call_count == 2
    manager.configure_printer.assert_any_call(1, inactive_config)
    manager.configure_printer.assert_any_call(2, active_config)
    manager.handle_offline.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_startup_skips_missing_disabled_and_unmapped_wled_configs():
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.execute.return_value.all.return_value = [
        (1, None, False),
        (2, {"enabled": False, "base_url": "http://wled.local", "presets": {"offline": 9}}, False),
        (3, {"enabled": True, "base_url": "http://wled.local", "presets": {}}, False),
    ]
    manager = WLEDManager()
    with (
        patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value=False)),
        patch("backend.app.main.wled_manager", manager),
    ):
        await initialize_wled_printers(db)
    assert all(runtime.send_task is None for runtime in manager._runtime.values())
    assert manager._client is None
