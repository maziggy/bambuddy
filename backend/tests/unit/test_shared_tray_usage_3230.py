"""Several slicer slots charged to one tray (#3230).

The 3MF pass skipped a tray once it had charged it, so a second slicer slot
mapped to the same tray was dropped. The reporter's H2C printed filaments 1 and
4 from one AMS-HT tray (mapping ``[128, -1, -1, 128, -1]``) and lost filament
4's 2.13 g. The same skip dropped the last segment of a single-filament print
that switched to another tray and back. A slot is now charged once and a tray
as often as slots use it.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.spool import Spool
from backend.app.services.usage_tracker import _track_from_3mf

pytestmark = pytest.mark.unit


def _spool(spool_id: int):
    spool = MagicMock()
    spool.id = spool_id
    spool.label_weight = 1000
    spool.weight_used = 0
    spool.cost_per_kg = None
    spool.material = "PLA"
    spool.rgba = None
    return spool


def _db(spools: dict[int, MagicMock]):
    archive = MagicMock()
    archive.id = 3230
    archive.file_path = "archives/3230/test.3mf"
    archive.extra_data = None
    archive.plate_id = None

    async def execute(stmt, *args, **kwargs):
        entity = stmt.column_descriptions[0].get("entity")
        result = MagicMock()
        value = None
        if entity is PrintArchive:
            value = archive
        elif entity is Spool:
            value = spools.get(stmt.whereclause.right.value)
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.first.return_value = None
        result.scalar.return_value = None
        return result

    db = AsyncMock()
    db.execute = execute
    db.add = MagicMock()
    return db


# HT tray 128 holds spool 7; AMS 0 slot 1 holds spool 8.
ASSIGNED = {(128, 0): 7, (0, 1): 8}


async def _track(ams_mapping, filament_usage, tray_change_log=None, total_layers=0, handled_trays=None, raw_data=None):
    spools = {7: _spool(7), 8: _spool(8)}
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        progress=100,
        layer_num=90,
        total_layers=total_layers,
        tray_now=128,
        last_loaded_tray=-1,
        tray_change_log=tray_change_log or [],
        raw_data=raw_data or {},
    )

    async def resolve(printer_id, ams_id, tray_id, **kwargs):
        return ASSIGNED.get((ams_id, tray_id))

    with (
        patch("backend.app.core.config.settings") as mock_settings,
        patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=None),
        patch("backend.app.utils.threemf_tools.extract_filament_properties_from_3mf", return_value={}),
        patch("backend.app.services.usage_tracker._resolve_spool_id_for_tray", side_effect=resolve),
    ):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_settings.base_dir = MagicMock()
        mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)
        results = await _track_from_3mf(
            printer_id=1,
            archive_id=3230,
            status="completed",
            print_name="shared_tray",
            handled_trays=handled_trays if handled_trays is not None else set(),
            printer_manager=printer_manager,
            db=_db(spools),
            ams_mapping=ams_mapping,
            print_started_at=datetime.now(timezone.utc),
        )
    return results, spools


def _usage(slot_id: int, used_g: float) -> dict:
    return {"slot_id": slot_id, "used_g": used_g, "type": "PLA", "color": "#FFFFFF"}


class TestSharedTray:
    @pytest.mark.asyncio
    async def test_two_slots_on_one_tray_are_both_charged(self):
        # The reporter's job, verbatim mapping and grams.
        results, spools = await _track([128, -1, -1, 128, -1], [_usage(1, 1.05), _usage(4, 2.13)])
        assert [(r["slot_id"], r["spool_id"], r["weight_used"]) for r in results] == [(1, 7, 1.1), (4, 7, 2.1)]
        assert spools[7].weight_used == pytest.approx(3.18)

    @pytest.mark.asyncio
    async def test_slots_on_different_trays_are_unchanged(self):
        results, spools = await _track([128, 1], [_usage(1, 1.0), _usage(2, 2.0)])
        assert [r["spool_id"] for r in results] == [7, 8]
        assert spools[7].weight_used == pytest.approx(1.0)
        assert spools[8].weight_used == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_a_shared_tray_is_still_handed_to_the_remain_fallback_as_covered(self):
        handled: set = set()
        await _track([128, -1, -1, 128, -1], [_usage(1, 1.05), _usage(4, 2.13)], handled_trays=handled)
        assert handled == {(128, 0)}

    @pytest.mark.asyncio
    async def test_a_slot_listed_once_per_plate_is_charged_once(self):
        # With the plate unknown, every plate of the file is listed, so slot 1
        # appears once per plate. Charging it per plate would multiply the debit.
        results, spools = await _track([128], [_usage(1, 4.0), _usage(1, 4.0), _usage(1, 4.0)])
        assert len(results) == 1
        assert spools[7].weight_used == pytest.approx(4.0)


class TestReturningToATray:
    @pytest.mark.asyncio
    async def test_every_segment_is_charged_when_a_print_returns_to_an_earlier_tray(self):
        # Single-filament print: HT tray, then AMS 0 slot 1, then back to the HT
        # tray. Linear split over 90 layers gives a third per segment.
        results, spools = await _track(
            [128],
            [_usage(1, 9.0)],
            tray_change_log=[(128, 0), (1, 30), (128, 60)],
            total_layers=90,
        )
        assert [r["spool_id"] for r in results] == [7, 8, 7]
        assert spools[7].weight_used == pytest.approx(6.0)
        assert spools[8].weight_used == pytest.approx(3.0)


class TestGuessedTrays:
    @pytest.mark.asyncio
    async def test_a_guessed_tray_already_charged_is_not_charged_again(self):
        # No mapping: slot 1 takes the only loaded tray (AMS 0 slot 1, global
        # 1), slot 2 is past the loaded trays and falls back to slot_id - 1,
        # the same tray. A position guess says nothing about which spool fed
        # slot 2, so it is not added to slot 1's spool.
        raw = {"ams": [{"id": "0", "tray": [{"id": "1", "tray_type": "PLA"}]}]}
        with patch(
            "backend.app.services.spoolman_tracking.build_ams_tray_lookup", return_value={1: {"tray_type": "PLA"}}
        ):
            results, spools = await _track(None, [_usage(1, 1.0), _usage(2, 2.0)], raw_data=raw)
        assert [r["slot_id"] for r in results] == [1]
        assert spools[8].weight_used == pytest.approx(1.0)
