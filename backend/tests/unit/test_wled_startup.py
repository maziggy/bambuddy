"""Startup WLED initialization keeps inactive printers offline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import initialize_wled_printers


@pytest.mark.asyncio
async def test_startup_sets_only_inactive_printers_offline():
    db = MagicMock()
    db.execute = AsyncMock()
    db.execute.return_value.all.return_value = [
        (1, {"enabled": True, "base_url": "http://wled.local", "presets": {"offline": 9}}, False),
        (2, {"enabled": True, "base_url": "http://wled.local", "presets": {"offline": 9}}, True),
    ]
    with (
        patch("backend.app.api.routes.settings.get_setting", new=AsyncMock(return_value=False)),
        patch("backend.app.main.wled_manager") as manager,
    ):
        await initialize_wled_printers(db)
    assert manager.configure_printer.call_count == 2
    manager.handle_offline.assert_called_once_with(1)
