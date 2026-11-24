"""Aborted print after a backup spool switch, built-in inventory (#3652).

One filament, 100 g, mapped to tray 3. Tray 3 runs out and the AMS switches to
tray 2 at layer 39. Without per-layer G-code the split is linear by layer. On a
print that stopped at layer 50 the partial grams cover 50 layers, so the split
has to run over those 50, as Spoolman's partial path does, not over the whole
print's 100.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.usage_tracker import _track_from_3mf

SPOOL_BY_TRAY = {3: 30, 2: 20}  # global tray -> spool id
LOG = [(3, 0), (2, 39)]
USAGE = [{"slot_id": 1, "used_g": 100.0, "type": "PLA", "color": "#000000"}]


def _spool(spool_id):
    spool = MagicMock()
    spool.id = spool_id
    spool.label_weight = 1000
    spool.weight_used = 0
    spool.cost_per_kg = None
    spool.material = "PLA"
    spool.rgba = "000000FF"
    return spool


def _db():
    """Archive and queue lookups answer as for a direct print; spools by id."""
    spools = {sid: _spool(sid) for sid in SPOOL_BY_TRAY.values()}
    archive = MagicMock()
    archive.id = 100
    archive.file_path = "archives/100/test.3mf"
    archive.extra_data = None
    archive.plate_id = None

    async def execute(stmt, *args, **kwargs):
        result = MagicMock()
        entity = stmt.column_descriptions[0].get("entity")
        name = getattr(entity, "__name__", "")
        value = None
        if name == "PrintArchive":
            value = archive
        elif name == "Spool":
            params = stmt.compile().params
            value = spools.get(next(iter(params.values())))
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.first.return_value = value
        result.scalar.return_value = None
        return result

    db = AsyncMock()
    db.execute = execute
    db.scalar = AsyncMock(return_value=1.0)
    return db


async def _run(*, status, layer_num, last_layer_num, progress, layer_usage=None):
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        progress=progress,
        layer_num=layer_num,
        tray_now=2,
        last_loaded_tray=2,
        total_layers=100,
        tray_change_log=list(LOG),
        spool_change_log=None,
        raw_data={},
    )
    snapshot = {(0, tray): spool_id for tray, spool_id in SPOOL_BY_TRAY.items()}
    with (
        patch("backend.app.core.config.settings") as mock_settings,
        patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=USAGE),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=layer_usage),
        patch(
            "backend.app.utils.threemf_tools.extract_filament_properties_from_3mf",
            return_value={1: {"density": 1.24, "diameter": 1.75}},
        ),
    ):
        mock_settings.base_dir = MagicMock()
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)
        return await _track_from_3mf(
            printer_id=1,
            archive_id=100,
            status=status,
            print_name="part-a",
            handled_trays=set(),
            printer_manager=printer_manager,
            db=_db(),
            ams_mapping=[3],
            spool_assignments=snapshot,
            last_layer_num=last_layer_num,
            last_progress=progress,
        )


def _by_tray(results):
    charged: dict[int, float] = {}
    for r in results:
        charged[r["tray_id"]] = round(charged.get(r["tray_id"], 0.0) + r["weight_used"], 1)
    return charged


class TestAbortedBackupSplit3652:
    @pytest.mark.asyncio
    async def test_aborted_print_splits_up_to_stop_layer(self):
        # 50 g partial (50 %): 39 of 50 printed layers on tray 3, 11 on tray 2.
        results = await _run(status="aborted", layer_num=50, last_layer_num=50, progress=50)
        assert _by_tray(results) == {3: 39.0, 2: 11.0}

    @pytest.mark.asyncio
    async def test_firmware_reset_layer_uses_last_layer_num(self):
        # Firmware reset layer_num to 0 on cancel; the captured layer counts.
        results = await _run(status="failed", layer_num=0, last_layer_num=50, progress=50)
        assert _by_tray(results) == {3: 39.0, 2: 11.0}

    @pytest.mark.asyncio
    async def test_unknown_stop_layer_keeps_total_layers(self):
        # No layer known at all: the whole print's layer count, as before #3652.
        results = await _run(status="failed", layer_num=0, last_layer_num=0, progress=50)
        assert _by_tray(results) == {3: 19.5, 2: 30.5}

    @pytest.mark.asyncio
    async def test_completed_print_unchanged(self):
        results = await _run(status="completed", layer_num=100, last_layer_num=100, progress=100)
        assert _by_tray(results) == {3: 39.0, 2: 61.0}


# Front-loaded per-layer G-code: 37 mm a layer up to layer 39, then 3.7 mm a
# layer, so a linear split by layer and one that follows the G-code differ.
GCODE = {layer: {0: 37.0 * min(layer, 39) + 3.7 * max(layer - 39, 0)} for layer in range(1, 101)}
SPOOLMAN_TAGS = {"T3": 30, "T2": 20}


async def _spoolman_partial(*, layer_num, layer_usage):
    """Spoolman's partial path on the same print: spool id -> grams charged."""
    from backend.app.services import spoolman_tracking

    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    tracking = SimpleNamespace(
        # Stored as JSON, so with string keys.
        layer_usage=(
            {str(layer): {str(fid): mm for fid, mm in used.items()} for layer, used in layer_usage.items()}
            if layer_usage
            else None
        ),
        filament_properties={"1": {"density": 1.24, "diameter": 1.75}},
        filament_usage=[{"slot_id": 1, "used_g": 100.0}],
        ams_trays={3: {"tray_uuid": "T3", "tray_type": "PLA"}, 2: {"tray_uuid": "T2", "tray_type": "PLA"}},
        slot_to_tray=[3],
        tray_remain_start={},
        archive_id=100,
        tray_now_at_start=3,
    )
    status = SimpleNamespace(
        layer_num=layer_num, total_layers=100, raw_data={}, tray_change_log=list(LOG), spool_change_log=None
    )
    client = AsyncMock()
    client.find_spool_by_tag = AsyncMock(
        side_effect=lambda tag, *a, **k: {"id": SPOOLMAN_TAGS[tag], "filament": {}} if tag in SPOOLMAN_TAGS else None
    )
    client.use_spool = AsyncMock()
    with (
        patch.object(spoolman_tracking, "async_session", session),
        patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=status)),
        patch.object(spoolman_tracking, "_get_printer_serial", AsyncMock(return_value="SERIAL")),
        patch.object(spoolman_tracking, "_get_spoolman_client_with_fallback", AsyncMock(return_value=client)),
    ):
        await spoolman_tracking._report_partial_usage(1, tracking, last_layer_num=layer_num)
    charged: dict[int, float] = {}
    for call in client.use_spool.await_args_list:
        charged[call.args[0]] = round(charged.get(call.args[0], 0.0) + call.args[1], 1)
    return charged


class TestAbortedBackupSplitParity3652:
    """Both inventories split an aborted print alike, with and without G-code."""

    @pytest.mark.asyncio
    async def test_spoolman_follows_the_gcode(self):
        # Stopped at layer 50: 1443 of 1483.7 mm before the switch, so tray 3
        # took 97 % of the partial grams, not the 39/50 a linear split gives.
        charged = await _spoolman_partial(layer_num=50, layer_usage=GCODE)
        assert charged[30] / (charged[30] + charged[20]) == pytest.approx(1443.0 / 1483.7, abs=0.01)

    @pytest.mark.asyncio
    async def test_both_inventories_charge_the_same_with_gcode(self):
        spoolman = await _spoolman_partial(layer_num=50, layer_usage=GCODE)
        builtin = await _run(status="aborted", layer_num=50, last_layer_num=50, progress=50, layer_usage=GCODE)
        by_spool = {SPOOL_BY_TRAY[tray]: grams for tray, grams in _by_tray(builtin).items()}
        assert spoolman == pytest.approx(by_spool, abs=0.1)

    @pytest.mark.asyncio
    async def test_both_inventories_charge_the_same_without_gcode(self):
        spoolman = await _spoolman_partial(layer_num=50, layer_usage=None)
        builtin = await _run(status="aborted", layer_num=50, last_layer_num=50, progress=50)
        by_spool = {SPOOL_BY_TRAY[tray]: grams for tray, grams in _by_tray(builtin).items()}
        assert spoolman == pytest.approx(by_spool, abs=0.1)
