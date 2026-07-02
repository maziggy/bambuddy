"""Spoolman-side mid-print tray-split accounting (#1793).

Reporter (@ojimpo) shipped the OP shape:
- H2S, AMS filament backup ON, two same-material spools loaded
- Single-slot print (72.56g on slot 1)
- Origin ran dry at layer 37, AMS auto-switched to backup, print finished
- Pre-fix: whole 72.56g charged to origin (via tag path) + separate 30g to
  backup (via remain-delta) — origin exceeded initial_weight, backup double-count

The fix ports usage_tracker's split path to spoolman_tracking so both
inventory backends attribute segments identically. These tests pin the
OP's shape plus the Path 2 (remain-delta) skip guarantee so it can't
double-charge tray IDs the split path already covered.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _AsyncCtx:
    """async_session() shim — same shape as test_spoolman_no3mf_remain_fallback."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_):
        return False


def _make_db(tracking):
    db = AsyncMock()
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = tracking
    db.execute = AsyncMock(return_value=select_result)
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    return db


class TestReportUsageTraySplit:
    """report_usage must consult state.tray_change_log and split per-segment."""

    @pytest.mark.asyncio
    async def test_op_sample_a_seamless_switch_splits_origin_to_backup(self):
        """Sample A from the reporter, verbatim: 72.56g single-slot print,
        AMS runout switch tray 0 → tray 1 at layer 37 of ~100 total.

        No gcode layer_usage is provided → linear-by-layer-ratio fallback:
        - seg 0 (tray 0, layers 0-37) = 72.56 * 37/100 = 26.85g → spool 8
        - seg 1 (tray 1, layers 37-end) = 72.56 - 26.85 = 45.71g → spool 7
        Path 2 (remain-delta) must NOT run against either tray — the split
        path already covered them.
        """
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 72.56}],
            ams_trays={
                0: {"tray_uuid": "AAAA", "tag_uid": "T1TAG", "tray_type": "PLA"},
                1: {"tray_uuid": "BBBB", "tag_uid": "T2TAG", "tray_type": "PLA"},
            },
            slot_to_tray=[0],
            tray_remain_start={
                "0-0": {"remain": 3, "tray_uuid": "AAAA"},  # origin near-empty at start-of-completion snapshot
                "0-1": {"remain": 73, "tray_uuid": "BBBB"},
            },
            layer_usage={},
            filament_properties={},
        )

        db = _make_db(tracking)

        client = AsyncMock()

        async def _find_spool_by_tag(tag):
            return (
                {"id": 8, "filament": {"color_hex": "000000"}}
                if tag == "AAAA"
                else {"id": 7, "filament": {"color_hex": "000000"}}
                if tag == "BBBB"
                else None
            )

        client.find_spool_by_tag = AsyncMock(side_effect=_find_spool_by_tag)
        client.use_spool = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            tray_change_log=[(0, 0), (1, 37)],
            total_layers=100,
            layer_num=100,
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_uuid": "AAAA", "remain": 0},
                            {"id": 1, "tray_uuid": "BBBB", "remain": 70},
                        ],
                    }
                ]
            },
        )

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SERIAL")),
            patch(
                "backend.app.services.spoolman_tracking._apply_spool_colors_to_archive",
                AsyncMock(),
            ),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await report_usage(printer_id=1, archive_id=143)

        # Exactly two use_spool calls — one per segment. Origin (spool 8)
        # gets the layers-0-37 slice, backup (spool 7) gets the remainder.
        calls = client.use_spool.await_args_list
        assert len(calls) == 2, f"expected 2 use_spool calls, got {len(calls)}: {calls}"

        by_spool = {c.args[0]: c.args[1] for c in calls}
        assert set(by_spool.keys()) == {8, 7}

        # Sum must equal the OP's total — no phantom grams created or lost.
        assert round(sum(by_spool.values()), 2) == 72.56

        # Origin (spool 8) should carry roughly the layers-0-37 fraction.
        # Linear: 72.56 * 37/100 = 26.85g. Allow small rounding wiggle.
        assert 26.0 < by_spool[8] < 28.0
        # Backup (spool 7) carries the remainder.
        assert 44.0 < by_spool[7] < 46.6

    @pytest.mark.asyncio
    async def test_path_2_remain_delta_skips_tray_handled_by_split(self):
        """After the split path attributes segments to tray 0 AND tray 1,
        the Path 2 remain-delta iterator must skip BOTH — otherwise backup
        would get charged twice (~30g double-count in the OP's Sample A).
        """
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 100.0}],
            ams_trays={
                0: {"tray_uuid": "AAAA", "tag_uid": "T1TAG", "tray_type": "PLA"},
                1: {"tray_uuid": "BBBB", "tag_uid": "T2TAG", "tray_type": "PLA"},
            },
            slot_to_tray=[0],
            tray_remain_start={
                "0-0": {"remain": 20, "tray_uuid": "AAAA"},
                "0-1": {"remain": 80, "tray_uuid": "BBBB"},
            },
            layer_usage={},
            filament_properties={},
        )

        db = _make_db(tracking)
        client = AsyncMock()

        async def _find_spool_by_tag(tag):
            return {"id": 8, "filament": {}} if tag == "AAAA" else {"id": 7, "filament": {}}

        client.find_spool_by_tag = AsyncMock(side_effect=_find_spool_by_tag)
        client.use_spool = AsyncMock()
        # If Path 2 ever runs, it needs a filament.weight to compute grams.
        # Making it valid means a failure to guard = extra use_spool calls,
        # not a silent skip. Combined with a truthy slot-assignment result
        # below, this is what actually proves the double-count guard works.
        client.get_spool = AsyncMock(return_value={"filament": {"weight": 1000.0}})

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            tray_change_log=[(0, 0), (1, 50)],
            total_layers=100,
            layer_num=100,
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_uuid": "AAAA", "remain": 0},
                            {"id": 1, "tray_uuid": "BBBB", "remain": 60},
                        ],
                    }
                ]
            },
        )

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SERIAL")),
            patch(
                # Path 2 uses this to resolve trays. Return valid IDs so
                # the ONLY thing stopping Path 2 from double-charging is
                # ``handled_global_tray_ids``. If the guard is broken,
                # Path 2 would successfully call ``use_spool`` two more
                # times and this test would fail with 4 calls, not 2.
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                AsyncMock(side_effect=lambda pid, ams, tray: 999 if (ams, tray) == (0, 0) else 888),
            ),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await report_usage(printer_id=1, archive_id=200)

        # EXACTLY 2 — one per segment; Path 2 must not add a third.
        assert client.use_spool.await_count == 2, (
            f"Path 2 leaked past the split — expected 2 use_spool calls, got "
            f"{client.use_spool.await_count}: {client.use_spool.await_args_list}"
        )

    @pytest.mark.asyncio
    async def test_multi_slot_print_does_not_activate_split_even_with_tray_changes(self):
        """Multi-colour prints normally cycle trays every colour change, so
        ``tray_change_log`` has many entries — but splitting each slot's
        grams across all of them would attribute slot 1's usage to segments
        where slot 2's tray was loaded (and vice versa).

        Mirrors ``usage_tracker.py:1002``'s gate: split only when there's
        exactly one nonzero slot. Multi-slot prints fall through to the
        existing single-tray path with its stable ``slot_to_tray`` mapping.
        """
        from backend.app.services.spoolman_tracking import report_usage

        # Two nonzero slots — regular multi-colour print
        tracking = SimpleNamespace(
            filament_usage=[
                {"slot_id": 1, "used_g": 30.0},
                {"slot_id": 2, "used_g": 20.0},
            ],
            ams_trays={
                0: {"tray_uuid": "AAAA", "tag_uid": "T1TAG", "tray_type": "PLA"},
                1: {"tray_uuid": "BBBB", "tag_uid": "T2TAG", "tray_type": "PLA"},
            },
            slot_to_tray=[0, 1],
            tray_remain_start={
                "0-0": {"remain": 90, "tray_uuid": "AAAA"},
                "0-1": {"remain": 80, "tray_uuid": "BBBB"},
            },
            layer_usage={},
            filament_properties={},
        )

        db = _make_db(tracking)
        client = AsyncMock()

        async def _find_spool_by_tag(tag):
            return {"id": 100, "filament": {}} if tag == "AAAA" else {"id": 200, "filament": {}}

        client.find_spool_by_tag = AsyncMock(side_effect=_find_spool_by_tag)
        client.use_spool = AsyncMock()

        printer_manager = MagicMock()
        # Multi-colour print naturally cycles between trays many times.
        # If we don't gate on single-slot, my split would attribute slot 1's
        # grams to every segment — including segments where tray 1 was loaded.
        printer_manager.get_status.return_value = SimpleNamespace(
            tray_change_log=[(0, 0), (1, 10), (0, 20), (1, 30), (0, 40)],
            total_layers=50,
            layer_num=50,
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_uuid": "AAAA", "remain": 87},
                            {"id": 1, "tray_uuid": "BBBB", "remain": 78},
                        ],
                    }
                ]
            },
        )

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SERIAL")),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await report_usage(printer_id=1, archive_id=42)

        # Split path must NOT engage. The single-tray path charges each
        # slot to its stable slot_to_tray mapping: slot 1 → tray 0 → spool
        # 100 (30g), slot 2 → tray 1 → spool 200 (20g). Two calls, exact
        # weights from the 3MF (not split).
        assert client.use_spool.await_count == 2
        by_spool = {c.args[0]: c.args[1] for c in client.use_spool.await_args_list}
        assert by_spool == {100: 30.0, 200: 20.0}

    @pytest.mark.asyncio
    async def test_single_tray_change_entry_uses_normal_path(self):
        """Only ONE entry in tray_change_log (start-of-print seed, no
        switch) must fall through to the existing single-tray charging
        path — not accidentally split when there's nothing to split.
        """
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 50.0}],
            ams_trays={0: {"tray_uuid": "AAAA", "tag_uid": "T1TAG", "tray_type": "PLA"}},
            slot_to_tray=[0],
            tray_remain_start={"0-0": {"remain": 80, "tray_uuid": "AAAA"}},
            layer_usage={},
            filament_properties={},
        )

        db = _make_db(tracking)
        client = AsyncMock()
        client.find_spool_by_tag = AsyncMock(return_value={"id": 8, "filament": {}})
        client.use_spool = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            tray_change_log=[(0, 0)],  # just the start-of-print seed
            total_layers=100,
            layer_num=100,
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_uuid": "AAAA", "remain": 75}]}]},
        )

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SERIAL")),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await report_usage(printer_id=1, archive_id=42)

        # Single-tray path: exactly one use_spool call, all 50g to spool 8.
        client.use_spool.assert_awaited_once_with(8, 50.0)
