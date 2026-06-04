"""Tests for the inventory-remain override builder in print_scheduler (#1508).

The MQTT ``remain`` field on an AMS tray is the printer firmware's
RFID-tracked value, which is ``-1`` for non-Bambu spools (and even when
set diverges from Bambuddy's inventory). When the user has bound an
inventory spool to an AMS slot, that inventory record's
``label_weight - weight_used`` (or Spoolman's ``remaining_weight``) is
the authoritative remaining-weight signal. These tests verify
``_build_inventory_remain_overrides`` surfaces those values keyed by
``global_tray_id`` so the "Prefer Lowest Remaining Filament" sort can
consume them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
def scheduler():
    return PrintScheduler()


def _make_async_session_returning(rows: list):
    """Build a stub AsyncSession whose .execute() returns an object whose
    .all() (and .scalars().all()) yield ``rows``."""
    result = MagicMock()
    result.all.return_value = rows
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


class TestInternalInventoryOverrides:
    @pytest.mark.asyncio
    async def test_returns_remaining_grams_for_bound_slots(self, scheduler):
        """Two slots bound; both come back keyed by global_tray_id with the
        correct ``label_weight - weight_used`` in grams. This is the
        reporter scenario in #1508: slot 1 has a 950 g clone, slot 4 has
        a 50 g original — the sort can now actually pick the 50 g spool.

        The override builder uses ``select(SpoolAssignment).options(
        selectinload(SpoolAssignment.spool))`` (matching the rest of the
        codebase), so the rows it iterates expose ``.ams_id``, ``.tray_id``
        and ``.spool`` directly — the test stubs the same shape.
        """
        spool_a = SimpleNamespace(label_weight=1000, weight_used=50)  # 950 g remaining
        spool_b = SimpleNamespace(label_weight=1000, weight_used=950)  # 50 g remaining
        rows = [
            SimpleNamespace(ams_id=0, tray_id=0, spool=spool_a),
            SimpleNamespace(ams_id=0, tray_id=3, spool=spool_b),
        ]
        loaded = [
            {"ams_id": 0, "tray_id": 0, "global_tray_id": 0, "is_external": False},
            {"ams_id": 0, "tray_id": 3, "global_tray_id": 3, "is_external": False},
        ]
        db = _make_async_session_returning(rows)
        with patch.object(PrintScheduler, "_is_spoolman_mode", new=AsyncMock(return_value=False)):
            out = await scheduler._build_inventory_remain_overrides(db, printer_id=1, loaded=loaded)
        assert out == {0: 950.0, 3: 50.0}

    @pytest.mark.asyncio
    async def test_skips_external_slots(self, scheduler):
        """VT / external slots are tracked separately from AMS inventory
        bindings — the override builder must not assign them an inventory
        remaining value even if (somehow) an assignment row exists.
        """
        loaded = [
            {"ams_id": -1, "tray_id": 0, "global_tray_id": 254, "is_external": True},
        ]
        db = _make_async_session_returning([])
        with patch.object(PrintScheduler, "_is_spoolman_mode", new=AsyncMock(return_value=False)):
            out = await scheduler._build_inventory_remain_overrides(db, printer_id=1, loaded=loaded)
        # DB shouldn't even be queried — nothing AMS-side to look up.
        db.execute.assert_not_called()
        assert out == {}

    @pytest.mark.asyncio
    async def test_empty_loaded_returns_empty(self, scheduler):
        """No loaded filaments → no overrides. The scheduler short-circuits
        before this is called in practice, but the function must be
        defensive — it's used in any prefer_lowest dispatch path."""
        db = _make_async_session_returning([])
        with patch.object(PrintScheduler, "_is_spoolman_mode", new=AsyncMock(return_value=False)):
            out = await scheduler._build_inventory_remain_overrides(db, printer_id=1, loaded=[])
        assert out == {}
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_negative_remaining_clamped_to_zero(self, scheduler):
        """An over-consumed spool (weight_used > label_weight) shouldn't
        produce a negative grams value — clamped to 0 so the sort treats
        it as fully empty rather than "more empty than zero."
        """
        spool = SimpleNamespace(label_weight=1000, weight_used=1100)
        rows = [
            SimpleNamespace(ams_id=0, tray_id=0, spool=spool),
        ]
        loaded = [{"ams_id": 0, "tray_id": 0, "global_tray_id": 0, "is_external": False}]
        db = _make_async_session_returning(rows)
        with patch.object(PrintScheduler, "_is_spoolman_mode", new=AsyncMock(return_value=False)):
            out = await scheduler._build_inventory_remain_overrides(db, printer_id=1, loaded=loaded)
        assert out == {0: 0.0}

    @pytest.mark.asyncio
    async def test_slot_without_binding_absent_from_overrides(self, scheduler):
        """A slot that has loaded filament but no inventory binding must
        not appear in the override map — the sort then falls back to MQTT
        ``remain`` for that one slot, preserving pre-#1508 behaviour.
        """
        rows = [
            SimpleNamespace(
                ams_id=0,
                tray_id=0,
                spool=SimpleNamespace(label_weight=1000, weight_used=100),
            ),
        ]
        loaded = [
            {"ams_id": 0, "tray_id": 0, "global_tray_id": 0, "is_external": False},
            {"ams_id": 0, "tray_id": 1, "global_tray_id": 1, "is_external": False},
        ]
        db = _make_async_session_returning(rows)
        with patch.object(PrintScheduler, "_is_spoolman_mode", new=AsyncMock(return_value=False)):
            out = await scheduler._build_inventory_remain_overrides(db, printer_id=1, loaded=loaded)
        assert out == {0: 900.0}
        assert 1 not in out


class TestSpoolmanModeOverrides:
    @pytest.mark.asyncio
    async def test_spoolman_remaining_grams_used_when_available(self, scheduler):
        """Spoolman mode: each bound slot's spoolman_spool_id is fetched
        through ``_spoolman_remaining_grams``; the result is the same
        global-tray-id-keyed grams map. Parity rule with internal mode
        (feedback_inventory_modes_parity).
        """
        rows = [
            SimpleNamespace(printer_id=1, ams_id=0, tray_id=0, spoolman_spool_id=42),
            SimpleNamespace(printer_id=1, ams_id=0, tray_id=2, spoolman_spool_id=99),
        ]
        loaded = [
            {"ams_id": 0, "tray_id": 0, "global_tray_id": 0, "is_external": False},
            {"ams_id": 0, "tray_id": 2, "global_tray_id": 2, "is_external": False},
        ]
        db = _make_async_session_returning(rows)

        async def _fake_grams(spool_id: int):
            return {42: 720.0, 99: 80.0}[spool_id]

        with (
            patch.object(PrintScheduler, "_is_spoolman_mode", new=AsyncMock(return_value=True)),
            patch(
                "backend.app.services.filament_deficit._spoolman_remaining_grams",
                new=AsyncMock(side_effect=_fake_grams),
            ),
        ):
            out = await scheduler._build_inventory_remain_overrides(db, printer_id=1, loaded=loaded)
        assert out == {0: 720.0, 2: 80.0}

    @pytest.mark.asyncio
    async def test_spoolman_unreachable_skips_silently(self, scheduler):
        """If Spoolman is unreachable for one spool, ``_spoolman_remaining_grams``
        returns None and that slot is omitted from the override map —
        sorting then falls back to MQTT remain for that slot only.
        """
        rows = [
            SimpleNamespace(printer_id=1, ams_id=0, tray_id=0, spoolman_spool_id=42),
            SimpleNamespace(printer_id=1, ams_id=0, tray_id=1, spoolman_spool_id=99),
        ]
        loaded = [
            {"ams_id": 0, "tray_id": 0, "global_tray_id": 0, "is_external": False},
            {"ams_id": 0, "tray_id": 1, "global_tray_id": 1, "is_external": False},
        ]
        db = _make_async_session_returning(rows)

        async def _fake_grams(spool_id: int):
            return 500.0 if spool_id == 42 else None

        with (
            patch.object(PrintScheduler, "_is_spoolman_mode", new=AsyncMock(return_value=True)),
            patch(
                "backend.app.services.filament_deficit._spoolman_remaining_grams",
                new=AsyncMock(side_effect=_fake_grams),
            ),
        ):
            out = await scheduler._build_inventory_remain_overrides(db, printer_id=1, loaded=loaded)
        assert out == {0: 500.0}
        assert 1 not in out
