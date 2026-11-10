"""Per-printer plate-clear override (#2860).

``require_plate_clear`` is a global setting; a printer can override it with
"on" (always wait for the confirmation) or "off" (never), and "inherit"
follows the setting. A printer with a plate changer clears the plate itself,
so the confirmation only gets in its way.
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.database import Base
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.wled import WLEDManager


@pytest.fixture
def scheduler():
    return PrintScheduler()


class TestPlateGate:
    @pytest.mark.parametrize("global_setting", [True, False])
    def test_printer_without_override_follows_the_setting(self, scheduler, global_setting):
        assert scheduler._plate_gate(1, global_setting) is global_setting

    @pytest.mark.parametrize("global_setting", [True, False])
    def test_on_waits_whatever_the_setting_says(self, scheduler, global_setting):
        scheduler._plate_clear_overrides = {1: "on"}
        assert scheduler._plate_gate(1, global_setting) is True

    @pytest.mark.parametrize("global_setting", [True, False])
    def test_off_never_waits(self, scheduler, global_setting):
        scheduler._plate_clear_overrides = {1: "off"}
        assert scheduler._plate_gate(1, global_setting) is False

    def test_override_only_applies_to_its_own_printer(self, scheduler):
        scheduler._plate_clear_overrides = {1: "off"}
        assert scheduler._plate_gate(1, True) is False
        assert scheduler._plate_gate(2, True) is True

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_off_printer_is_idle_while_awaiting_but_the_other_is_not(self, mock_pm, scheduler):
        """The swap-mod A1 Mini dispatches after a finish; the manual P1S still waits."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_awaiting_plate_clear.return_value = True
        scheduler._plate_clear_overrides = {1: "off"}

        assert scheduler._is_printer_idle(1, scheduler._plate_gate(1, True)) is True
        assert scheduler._is_printer_idle(2, scheduler._plate_gate(2, True)) is False

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_on_printer_waits_even_with_the_setting_off(self, mock_pm, scheduler):
        mock_pm.is_connected.return_value = True
        mock_pm.get_status.return_value = MagicMock(state="FINISH")
        mock_pm.is_awaiting_plate_clear.return_value = True
        scheduler._plate_clear_overrides = {1: "on"}

        assert scheduler._is_printer_idle(1, scheduler._plate_gate(1, False)) is False
        assert scheduler._is_printer_idle(2, scheduler._plate_gate(2, False)) is True


class TestLoadOverrides:
    @pytest.mark.asyncio
    async def test_only_printers_that_override_are_loaded(self, scheduler):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as db:
            for idx, mode in enumerate(("inherit", "off", "on"), start=1):
                db.add(
                    Printer(
                        name=f"P{idx}",
                        serial_number=f"SERIAL{idx}",
                        ip_address=f"192.168.1.{idx}",
                        access_code="12345678",
                        plate_clear_override=mode,
                    )
                )
            await db.commit()

            await scheduler._load_plate_clear_overrides(db)

        assert scheduler._plate_clear_overrides == {2: "off", 3: "on"}
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_a_printer_set_back_to_inherit_is_dropped(self, scheduler):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        scheduler._plate_clear_overrides = {1: "off"}
        async with session_maker() as db:
            await scheduler._load_plate_clear_overrides(db)

        assert scheduler._plate_clear_overrides == {}
        await engine.dispose()


class TestWledOverride:
    """The plate-clear light follows the printer's gate, not only the setting."""

    def test_follows_the_setting_without_an_override(self):
        manager = WLEDManager()
        manager.require_plate_clear = True
        assert manager._requires_plate_clear(1) is True
        manager.require_plate_clear = False
        assert manager._requires_plate_clear(1) is False

    def test_override_wins_over_the_setting(self):
        manager = WLEDManager()
        manager.require_plate_clear = True
        manager.set_plate_clear_override(1, "off")
        manager.set_plate_clear_override(2, "on")
        assert manager._requires_plate_clear(1) is False
        assert manager._requires_plate_clear(2) is True
        assert manager._requires_plate_clear(3) is True

    def test_inherit_clears_an_override(self):
        manager = WLEDManager()
        manager.set_plate_clear_override(1, "off")
        manager.set_plate_clear_override(1, "inherit")
        manager.require_plate_clear = True
        assert manager._requires_plate_clear(1) is True

    def test_removing_a_printer_forgets_its_override(self):
        manager = WLEDManager()
        manager.set_plate_clear_override(1, "off")
        manager.remove_printer(1)
        manager.require_plate_clear = True
        assert manager._requires_plate_clear(1) is True
