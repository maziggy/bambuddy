"""A finished print's backup-spool split follows the G-code's proportions (#3322).

The split prices every segment but the last in the G-code's grams and gives
the last the rest. A finished print was split on the slicer's grams, so when
the G-code prices the first spool's share above the slicer's whole figure, the
backup spool got nothing and the first spool more than the print used. A
stopped print already split on the G-code's total and scaled after (#3317);
a finished one now does the same with the G-code's total over the whole print.

The print: one slot, 100 g by the slicer, tray 3 runs out and the AMS switches
to tray 2 at layer 39. The G-code has 35000 mm up to the switch and 50000 mm in
all, about 104 g and 149 g, so the first spool fed 70 % of the print.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.utils import threemf_tools
from backend.app.utils.tray_split import compute_tray_split_grams, whole_print_gcode_grams
from backend.tests.unit.services import test_spoolman_tray_split as tray_split_tests
from backend.tests.unit.services.test_spoolman_tray_split import _AsyncCtx, _make_db
from backend.tests.unit.test_aborted_backup_split_3652 import _by_tray, _run

GCODE = {layer: {0: 35000.0 * min(layer, 39) / 39 + 15000.0 * max(layer - 39, 0) / 61} for layer in range(1, 101)}
SHARE = 35000.0 / 50000.0


class TestTheSplit:
    def test_the_whole_print_total_is_the_last_layer(self):
        expected = threemf_tools.mm_to_grams(50000.0, 1.75, 1.24)
        assert whole_print_gcode_grams(GCODE, 1, 1.24, 1.75) == pytest.approx(expected)
        assert expected > 100

    def test_no_per_layer_data_or_slot_is_zero(self):
        assert whole_print_gcode_grams(None, 1, 1.24, 1.75) == 0.0
        assert whole_print_gcode_grams(GCODE, 2, 1.24, 1.75) == 0.0

    def test_the_last_segment_never_goes_below_zero(self):
        # Split on the slicer's 100 g, the first segment is priced at 104 g.
        segments = compute_tray_split_grams([(3, 0), (2, 39)], 100.0, 1, GCODE, 1.24, 1.75, 100, 100)
        assert segments[0][2] > 100
        assert segments[1][2] == 0.0


class TestBuiltInInventory:
    @pytest.mark.asyncio
    async def test_the_backup_spool_gets_its_share(self):
        results = await _run(status="completed", layer_num=100, last_layer_num=100, progress=100, layer_usage=GCODE)
        assert _by_tray(results) == {
            3: pytest.approx(100 * SHARE, abs=0.1),
            2: pytest.approx(100 * (1 - SHARE), abs=0.1),
        }


def _charged(client):
    charged: dict[int, float] = {}
    for call in client.use_spool.await_args_list:
        charged[call.args[0]] = round(charged.get(call.args[0], 0.0) + call.args[1], 2)
    return charged


def _json(layer_usage):
    return {str(layer): {str(fid): mm for fid, mm in used.items()} for layer, used in layer_usage.items()}


class TestSpoolman:
    @pytest.mark.asyncio
    async def test_the_backup_spool_gets_its_share(self):
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            archive_id=100,
            filament_usage=[{"slot_id": 1, "used_g": 100.0}],
            ams_trays={3: {"tray_uuid": "T3", "tray_type": "PLA"}, 2: {"tray_uuid": "T2", "tray_type": "PLA"}},
            slot_to_tray=[3],
            tray_remain_start={},
            layer_usage=_json(GCODE),
            filament_properties={"1": {"density": 1.24, "diameter": 1.75}},
        )
        client = AsyncMock()
        client.find_spool_by_tag = AsyncMock(
            side_effect=lambda tag, *a, **k: (
                {"id": {"T3": 30, "T2": 20}[tag], "filament": {}} if tag in ("T3", "T2") else None
            )
        )
        client.use_spool = AsyncMock()
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            tray_change_log=[(3, 0), (2, 39)], total_layers=100, layer_num=100, raw_data={}
        )
        with ExitStack() as stack:
            for p in (
                patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(_make_db(tracking))),
                patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
                patch(
                    "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                    AsyncMock(return_value=client),
                ),
                patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SERIAL")),
                patch("backend.app.services.spoolman_tracking._apply_spool_colors_to_archive", AsyncMock()),
                patch("backend.app.services.printer_manager.printer_manager", printer_manager),
            ):
                stack.enter_context(p)
            await report_usage(printer_id=1, archive_id=100)

        assert _charged(client) == {
            30: pytest.approx(100 * SHARE, abs=0.1),
            20: pytest.approx(100 * (1 - SHARE), abs=0.1),
        }

    @pytest.mark.asyncio
    async def test_a_multi_filament_print_splits_the_switched_slot_alike(self):
        # #3650's print: slot 1 (113.2 g) switches from tray 3 to tray 2 at
        # layer 40; slot 2 (2 g) stays on tray 0. Its G-code puts 80 % of slot
        # 1 before the switch, at more grams than the slicer's whole 113.2.
        harness = tray_split_tests.TestMultiFilamentBackupSplit3650()
        layer_usage = {10: {0: 20000.0, 1: 300.0}, 40: {0: 40000.0, 1: 500.0}, 100: {0: 50000.0, 1: 700.0}}
        assert threemf_tools.mm_to_grams(40000.0, 1.75, 1.24) > 113.2

        charged = await harness._report(harness._tracking(layer_usage=_json(layer_usage)), harness._state())

        assert charged == {30: pytest.approx(113.2 * 0.8, abs=0.1), 20: pytest.approx(113.2 * 0.2, abs=0.1), 10: 2.0}
