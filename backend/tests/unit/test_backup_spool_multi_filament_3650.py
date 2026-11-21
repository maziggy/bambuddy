"""AMS backup switch on a multi-filament print, built-in inventory (#3650).

Two filaments: slot 1 black (113.2 g) mapped to tray 3, slot 2 yellow (2.0 g)
mapped to tray 0. Tray 3 runs out at layer 40 and the AMS switches black to
tray 2, which holds the same filament and is in no slot of the mapping.
Before #3650 the whole 113.2 g went to tray 3's spool and tray 2's spool was
charged nothing; the single-filament split of #957 did not apply because the
print used two slots.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.usage_tracker import _track_from_3mf

SPOOL_BY_TRAY = {3: 30, 2: 20, 0: 10}  # global tray -> spool id
LOG = [(3, 0), (0, 10), (3, 11), (2, 40), (0, 41), (2, 42)]
USAGE = [
    {"slot_id": 1, "used_g": 113.2, "type": "PLA", "color": "#000000"},
    {"slot_id": 2, "used_g": 2.0, "type": "PLA", "color": "#FFFF00"},
]


def _raw(mapping=None):
    trays = [
        {"id": "0", "tray_type": "PLA", "tray_color": "FFFF00FF"},
        {"id": "1", "tray_type": "", "tray_color": ""},
        {"id": "2", "tray_type": "PLA", "tray_color": "000000FF"},
        {"id": "3", "tray_type": "PLA", "tray_color": "000000FF"},
    ]
    raw = {"ams": [{"id": "0", "tray": trays}]}
    if mapping is not None:
        raw["mapping"] = mapping
    return raw


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


async def _run(*, status="completed", ams_mapping=(3, 0), raw=None, layer_usage=None, layer_num=100, progress=100):
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        progress=progress,
        layer_num=layer_num,
        tray_now=2,
        last_loaded_tray=2,
        total_layers=100,
        tray_change_log=list(LOG),
        spool_change_log=None,
        raw_data=_raw() if raw is None else raw,
    )
    snapshot = {(0, tray): spool_id for tray, spool_id in SPOOL_BY_TRAY.items()}
    with (
        patch("backend.app.core.config.settings") as mock_settings,
        patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=USAGE),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=layer_usage),
        patch(
            "backend.app.utils.threemf_tools.extract_filament_properties_from_3mf",
            return_value={1: {"density": 1.24, "diameter": 1.75}, 2: {"density": 1.24, "diameter": 1.75}},
        ),
        patch("backend.app.utils.threemf_tools.mm_to_grams", side_effect=lambda mm, d, dens: mm * 0.01),
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
            ams_mapping=list(ams_mapping) if ams_mapping is not None else None,
            spool_assignments=snapshot,
            last_layer_num=layer_num,
            last_progress=progress,
        )


def _by_tray(results):
    charged: dict[int, float] = {}
    for r in results:
        charged[r["tray_id"]] = round(charged.get(r["tray_id"], 0.0) + r["weight_used"], 1)
    return charged


class TestBackupSwitchMultiFilament3650:
    @pytest.mark.asyncio
    async def test_runout_on_multi_filament_splits_slot_between_trays(self):
        # No per-layer G-code: linear by layer, 40 of 100 layers on tray 3.
        results = await _run()
        assert _by_tray(results) == {3: 45.3, 2: 67.9, 0: 2.0}
        assert {r["slot_id"] for r in results if r["tray_id"] in (2, 3)} == {1}

    @pytest.mark.asyncio
    async def test_runout_split_uses_slot_gcode(self):
        # Cumulative mm per filament id: slot 1 is filament 0, slot 2 filament 1.
        # Slot 1 fed 2000 mm up to the switch at layer 40 -> 20 g on tray 3; slot 2's
        # millimetres must not leak into slot 1's segments.
        layer_usage = {10: {0: 1000.0, 1: 50.0}, 40: {0: 2000.0, 1: 150.0}, 100: {0: 11320.0, 1: 200.0}}
        results = await _run(layer_usage=layer_usage)
        assert _by_tray(results) == {3: 20.0, 2: 93.2, 0: 2.0}

    @pytest.mark.asyncio
    async def test_mqtt_mapping_source_keeps_mapping(self):
        # Read at completion, the MQTT mapping may already name the backup tray,
        # so it is no evidence of which tray the slot started on: as before #3650.
        results = await _run(ams_mapping=None, raw=_raw(mapping=[3, 0]))
        assert _by_tray(results) == {3: 113.2, 0: 2.0}

    @pytest.mark.asyncio
    async def test_failed_print_splits_partial_grams(self):
        # Stopped at layer 60 of 100: the slicer totals scale to 60 %, and slot 1's
        # share is split at the switch the same way as a finished print.
        results = await _run(status="failed", layer_num=60, progress=60)
        charged = _by_tray(results)
        assert charged[3] == pytest.approx(113.2 * 0.6 * 40 / 100, abs=0.1)
        assert charged[2] == pytest.approx(113.2 * 0.6 - charged[3], abs=0.1)
        assert charged[0] == pytest.approx(1.2, abs=0.1)

    @pytest.mark.asyncio
    async def test_colour_mismatch_keeps_mapping(self):
        raw = _raw()
        raw["ams"][0]["tray"][2]["tray_color"] = "FF0000FF"
        results = await _run(raw=raw)
        assert _by_tray(results) == {3: 113.2, 0: 2.0}
