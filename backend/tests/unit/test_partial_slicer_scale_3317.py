"""A cancelled print is charged on the slicer's weights, like a finished one (#3317).

The G-code feeds more than the slicer counts: the start sequence and the purge
at a filament change, and both ahead of the layers they serve. Its grams on
their own charged a print cancelled near the end more than the same print
finished. The shape below is the reporter's X1C two-colour cylinder: charcoal
(slot 4) on layers 0-24 after a ~220 mm start sequence, turquoise (slot 1) on
layers 25-46 after a ~245 mm purge.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import spoolman_tracking
from backend.app.utils.threemf_tools import (
    mm_to_grams,
    parse_gcode_layer_filament_usage,
    slicer_grams_at_layer,
)

SLICER = [{"slot_id": 1, "used_g": 0.77}, {"slot_id": 4, "used_g": 1.55}]
DENSITY = {1: 1.26, 4: 1.24}


def _two_colour_gcode() -> str:
    lines = ["M620 S3A", "G1 E220"]  # start sequence on the first filament
    for layer in range(1, 25):
        lines += [f"M73 L{layer}", "G1 E13.83", "G1 E-0.8", "G1 E0.8"]
    lines += ["M73 L25", "M620 S0A", "G1 E244.5"]  # change to turquoise, flush
    for layer in range(25, 48):
        lines += [f"M73 L{layer}", "G1 E3.28"]
    return "\n".join(lines)


LAYERS = parse_gcode_layer_filament_usage(_two_colour_gcode())


def _total(grams: dict[int, float]) -> float:
    return sum(grams.values())


def test_the_gcode_alone_charges_a_late_cancel_more_than_a_finish():
    # What dev charged before: the premise of the fix, kept so it stays true.
    from backend.app.utils.threemf_tools import get_cumulative_usage_at_layer

    raw = {
        fid + 1: mm_to_grams(mm, 1.75, DENSITY[fid + 1])
        for fid, mm in get_cumulative_usage_at_layer(LAYERS, 33).items()
    }
    assert _total(raw) > sum(u["used_g"] for u in SLICER)


@pytest.mark.parametrize("layer", [1, 10, 24, 25, 33, 42, 46])
def test_a_cancel_never_costs_more_than_the_finished_print(layer):
    grams = slicer_grams_at_layer(LAYERS, layer, SLICER)
    for usage in SLICER:
        assert grams.get(usage["slot_id"], 0.0) <= usage["used_g"] + 1e-9


def test_each_filament_follows_its_own_feed():
    at_33 = slicer_grams_at_layer(LAYERS, 33, SLICER)
    # Charcoal finished at layer 24: all of it.
    assert at_33[4] == pytest.approx(1.55)
    # Turquoise's purge came first, so it is well past a third of its weight
    # eight layers in, and short of the whole.
    assert 0.6 < at_33[1] < 0.77


def test_the_last_layer_is_the_slicer_weight():
    assert slicer_grams_at_layer(LAYERS, 47, SLICER) == pytest.approx({1: 0.77, 4: 1.55})


def test_a_filament_not_fed_yet_is_not_charged():
    assert set(slicer_grams_at_layer(LAYERS, 10, SLICER)) == {4}


def test_a_slot_the_slicer_did_not_weigh_is_left_to_the_caller():
    assert slicer_grams_at_layer(LAYERS, 33, [{"slot_id": 4, "used_g": 1.55}, {"slot_id": 1, "used_g": 0}]) == {
        4: pytest.approx(1.55)
    }


def test_a_retraction_at_the_end_does_not_charge_above_the_weight():
    layers = {1: {0: 100.0}, 2: {0: 98.0}}
    assert slicer_grams_at_layer(layers, 1, [{"slot_id": 1, "used_g": 3.0}]) == {1: 3.0}


def test_a_slot_listed_twice_keeps_its_first_weight():
    # Every plate listed when the plate is unknown.
    layers = {1: {0: 50.0}, 2: {0: 100.0}}
    usage = [{"slot_id": 1, "used_g": 4.0}, {"slot_id": 1, "used_g": 9.0}]
    assert slicer_grams_at_layer(layers, 1, usage) == {1: 2.0}


def test_no_layer_data():
    assert slicer_grams_at_layer({}, 10, SLICER) == {}


@pytest.mark.asyncio
async def test_spoolman_charges_a_cancel_on_the_slicer_weights():
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    tracking = SimpleNamespace(
        # Stored as JSON, so with string keys.
        layer_usage={str(layer): {str(fid): mm for fid, mm in used.items()} for layer, used in LAYERS.items()},
        filament_properties={},
        filament_usage=SLICER,
        ams_trays={},
        slot_to_tray=[0, 1, 2, 3],
        tray_remain_start={},
        archive_id=1,
        tray_now_at_start=0,
    )
    status = SimpleNamespace(layer_num=33, total_layers=47, raw_data={}, tray_change_log=[])
    report = AsyncMock(return_value=2)

    with (
        patch.object(spoolman_tracking, "async_session", session),
        patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=status)),
        patch.object(spoolman_tracking, "_get_printer_serial", AsyncMock(return_value="SERIAL")),
        patch.object(spoolman_tracking, "_get_spoolman_client_with_fallback", AsyncMock(return_value=MagicMock())),
        patch.object(spoolman_tracking, "_report_spool_usage_for_slots", report),
    ):
        await spoolman_tracking._report_partial_usage(1, tracking, last_layer_num=33)

    items = dict(report.call_args.args[1])
    assert items[4] == pytest.approx(1.55)
    assert 0.6 < items[1] < 0.77


def test_a_filament_that_never_fed_net_is_left_to_the_caller():
    # Only retracted: its snapshots read 0 mm, and there is no share to take.
    layers = {1: {0: 50.0, 1: 0.0}, 2: {0: 100.0, 1: 0.0}}
    usage = [{"slot_id": 1, "used_g": 4.0}, {"slot_id": 2, "used_g": 1.0}]
    assert slicer_grams_at_layer(layers, 1, usage) == {1: 2.0}


@pytest.mark.asyncio
async def test_inventory_backup_switch_shares_the_scaled_weight_between_both_spools():
    # The split prices segments in G-code grams; on the slicer's (smaller)
    # total the spool switched to at layer 30 came out below zero and got
    # nothing, while the first spool got more than the whole cancelled print.
    from backend.app.services.usage_tracker import _track_from_3mf
    from backend.tests.unit.test_usage_tracker import (
        _make_archive,
        _make_assignment,
        _make_spool,
        _mock_db_sequential,
    )

    spool_a, spool_b = _make_spool(spool_id=10), _make_spool(spool_id=20)
    db = _mock_db_sequential(
        [
            _make_archive(archive_id=100),
            None,
            _make_assignment(spool_id=10, ams_id=0, tray_id=0),
            spool_a,
            _make_assignment(spool_id=20, ams_id=0, tray_id=1),
            spool_b,
        ]
    )
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        progress=31,
        layer_num=31,
        tray_now=1,
        last_loaded_tray=1,
        total_layers=100,
        tray_change_log=[(0, 0), (1, 30)],
    )
    # 37 mm a layer: 3700 mm (11.0 g) in all, against the slicer's 10 g.
    layers = {layer: {0: 37.0 * layer} for layer in range(1, 101)}

    with (
        patch("backend.app.core.config.settings") as mock_settings,
        patch(
            "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
            return_value=[{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": ""}],
        ),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=layers),
        patch(
            "backend.app.utils.threemf_tools.extract_filament_properties_from_3mf",
            return_value={1: {"density": 1.24, "diameter": 1.75}},
        ),
    ):
        path = MagicMock()
        path.exists.return_value = True
        mock_settings.base_dir.__truediv__ = MagicMock(return_value=path)
        results = await _track_from_3mf(
            printer_id=1,
            archive_id=100,
            status="failed",
            print_name="Runout",
            handled_trays=set(),
            printer_manager=printer_manager,
            db=db,
        )

    charged = {r["spool_id"]: r["weight_used"] for r in results}
    # 10 g x 1147/3700 = 3.1 g, shared as the G-code fed it: 30 layers, then 1.
    assert sum(charged.values()) == pytest.approx(3.1, abs=0.05)
    assert charged[10] == pytest.approx(3.0, abs=0.05)
    assert charged[20] == pytest.approx(0.1, abs=0.05)


@pytest.mark.asyncio
async def test_spoolman_does_not_charge_a_slot_the_slicer_did_not_weigh():
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    tracking = SimpleNamespace(
        layer_usage={"10": {"0": 100.0, "1": 200.0}, "20": {"0": 200.0, "1": 200.0}},
        filament_properties={},
        # Fed by the G-code, but weighed 0 g: a finished print charges it nothing.
        filament_usage=[{"slot_id": 1, "used_g": 4.0}, {"slot_id": 2, "used_g": 0}],
        ams_trays={},
        slot_to_tray=[0, 1],
        tray_remain_start={},
        archive_id=1,
        tray_now_at_start=0,
    )
    status = SimpleNamespace(layer_num=10, total_layers=20, raw_data={}, tray_change_log=[])
    report = AsyncMock(return_value=1)

    with (
        patch.object(spoolman_tracking, "async_session", session),
        patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=status)),
        patch.object(spoolman_tracking, "_get_printer_serial", AsyncMock(return_value="SERIAL")),
        patch.object(spoolman_tracking, "_get_spoolman_client_with_fallback", AsyncMock(return_value=MagicMock())),
        patch.object(spoolman_tracking, "_report_spool_usage_for_slots", report),
    ):
        await spoolman_tracking._report_partial_usage(1, tracking, last_layer_num=10)

    assert dict(report.call_args.args[1]) == {1: 2.0}
