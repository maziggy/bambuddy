"""Tests for GET /printers/{id}/inventory-remain (#1766).

The endpoint exposes the same `_build_inventory_remain_overrides` map the
dispatcher uses so PrintModal's client-side "Prefer Lowest Remaining Filament"
sort agrees with what gets dispatched — closes the gap where Spoolman-mode
users couldn't see inventory grams from the frontend.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.printers import get_inventory_remain


@pytest.fixture
def db():
    return MagicMock()


async def _call_endpoint(db, printer_id=1):
    return await get_inventory_remain(printer_id=printer_id, _=None, db=db)


class TestGetInventoryRemain:
    @pytest.mark.asyncio
    async def test_returns_empty_when_printer_has_no_status(self, db):
        # Printer disconnected / unknown — endpoint must not error, return {}.
        with patch(
            "backend.app.services.printer_manager.printer_manager.get_status",
            return_value=None,
        ):
            result = await _call_endpoint(db)
        assert result == {"inventory_remain_g": {}}

    @pytest.mark.asyncio
    async def test_serialises_globaltrayid_keys_as_strings(self, db):
        # JSON requires string keys; client converts back to Number on receive.
        # Asserts the key-shape contract the frontend depends on.
        state = SimpleNamespace(raw_data={})
        with (
            patch(
                "backend.app.services.printer_manager.printer_manager.get_status",
                return_value=state,
            ),
            patch(
                "backend.app.services.print_scheduler.PrintScheduler._build_loaded_filaments",
                return_value=[
                    {"ams_id": 0, "tray_id": 0, "global_tray_id": 0, "is_external": False},
                    {"ams_id": 0, "tray_id": 3, "global_tray_id": 3, "is_external": False},
                ],
            ),
            patch(
                "backend.app.services.print_scheduler.PrintScheduler._build_inventory_remain_overrides",
                new=AsyncMock(return_value={0: 950.0, 3: 50.0}),
            ),
        ):
            result = await _call_endpoint(db)

        assert result == {"inventory_remain_g": {"0": 950.0, "3": 50.0}}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_bound_slots(self, db):
        # Loaded filaments exist but none are bound to an inventory spool.
        # Backend returns {}; route serialises it unchanged.
        state = SimpleNamespace(raw_data={})
        with (
            patch(
                "backend.app.services.printer_manager.printer_manager.get_status",
                return_value=state,
            ),
            patch(
                "backend.app.services.print_scheduler.PrintScheduler._build_loaded_filaments",
                return_value=[
                    {"ams_id": 0, "tray_id": 0, "global_tray_id": 0, "is_external": False},
                ],
            ),
            patch(
                "backend.app.services.print_scheduler.PrintScheduler._build_inventory_remain_overrides",
                new=AsyncMock(return_value={}),
            ),
        ):
            result = await _call_endpoint(db)

        assert result == {"inventory_remain_g": {}}
