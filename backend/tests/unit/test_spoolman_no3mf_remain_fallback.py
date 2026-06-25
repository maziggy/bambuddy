"""AMS remain%-delta fallback for the no-3MF Spoolman path (#1820).

When a Bambu print starts without leaving a retrievable .gcode.3mf on the
printer (subtask_name='名称未設定'/'Untitled'), Bambuddy creates a
fallback archive with no 3MF on disk. Before this fix the Spoolman
tracking row was never created, so the print silently didn't decrement
the spool weight. This is the Spoolman mirror of usage_tracker's Path 2
fallback (already in place for the internal-inventory side).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.spoolman_tracking import (
    _snapshot_tray_remain,
    store_print_data,
)


class TestSnapshotTrayRemain:
    def test_captures_valid_remain(self):
        raw = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_uuid": "AAAA", "remain": 75},
                        {"id": 1, "tray_uuid": "BBBB", "remain": 30},
                    ],
                }
            ]
        }
        snap = _snapshot_tray_remain(raw)
        assert snap == {
            "0-0": {"remain": 75, "tray_uuid": "AAAA"},
            "0-1": {"remain": 30, "tray_uuid": "BBBB"},
        }

    def test_skips_invalid_remain(self):
        """remain=-1 means the AMS hasn't read the spool; a delta would be
        meaningless. Skip those slots — usage_tracker does the same
        (line 309)."""
        raw = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_uuid": "AAAA", "remain": 75},
                        {"id": 1, "tray_uuid": "BBBB", "remain": -1},
                        {"id": 2, "tray_uuid": "CCCC", "remain": 150},
                    ],
                }
            ]
        }
        snap = _snapshot_tray_remain(raw)
        assert set(snap.keys()) == {"0-0"}

    def test_captures_vt_tray(self):
        """External (VT) spool gets ams_id=255, tray_id=vt_id-254."""
        raw = {"ams": [], "vt_tray": [{"id": 254, "tray_uuid": "EEEE", "remain": 50}]}
        snap = _snapshot_tray_remain(raw)
        assert snap == {"255-0": {"remain": 50, "tray_uuid": "EEEE"}}

    def test_empty_when_no_ams_data(self):
        assert _snapshot_tray_remain({}) == {}

    def test_handles_missing_uuid(self):
        raw = {"ams": [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]}
        snap = _snapshot_tray_remain(raw)
        assert snap == {"0-0": {"remain": 80, "tray_uuid": ""}}


class TestStorePrintDataNo3mf:
    """store_print_data must create an ActivePrintSpoolman row even when
    no 3MF is available, populating tray_remain_start so report_usage can
    write a remain-delta at completion (#1820)."""

    @pytest.mark.asyncio
    async def test_creates_row_with_snapshot_when_no_3mf(self):
        db = AsyncMock()
        # No queue lookup for the no-3MF branch — only the DELETE.
        delete_result = MagicMock()
        db.execute = AsyncMock(side_effect=[delete_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_uuid": "AAAA", "tag_uid": "11", "tray_type": "PLA", "remain": 80},
                            {"id": 1, "tray_uuid": "BBBB", "tag_uid": "22", "tray_type": "PLA", "remain": 20},
                        ],
                    }
                ]
            }
        )

        mock_settings = MagicMock()
        mock_path = MagicMock()
        mock_path.exists.return_value = False  # no 3MF — the #1820 case
        mock_settings.base_dir.__truediv__.return_value = mock_path

        with (
            patch("backend.app.services.spoolman_tracking.app_settings", mock_settings),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        ):
            await store_print_data(
                printer_id=1,
                archive_id=42,
                file_path="",  # fallback-archive file_path
                db=db,
                printer_manager=printer_manager,
            )

        db.add.assert_called_once()
        tracking = db.add.call_args.args[0]
        assert tracking.filament_usage is None
        assert tracking.tray_remain_start == {
            "0-0": {"remain": 80, "tray_uuid": "AAAA"},
            "0-1": {"remain": 20, "tray_uuid": "BBBB"},
        }

    @pytest.mark.asyncio
    async def test_no_row_when_no_3mf_and_no_remain_data(self):
        """If the AMS has no slot with valid remain either (e.g. printer
        offline at print start), there's nothing to track. Don't create
        a row that contributes no value."""
        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": -1}]}]}
        )

        mock_settings = MagicMock()
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_settings.base_dir.__truediv__.return_value = mock_path

        with (
            patch("backend.app.services.spoolman_tracking.app_settings", mock_settings),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        ):
            await store_print_data(
                printer_id=1,
                archive_id=42,
                file_path="",
                db=db,
                printer_manager=printer_manager,
            )

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_3mf_path_also_captures_snapshot(self):
        """The remain snapshot is captured ALWAYS, not just for no-3MF.
        That lets report_usage fall back per-slot when 3MF coverage is
        partial — same shape as usage_tracker.on_print_complete which
        runs Path 1 (3MF) and Path 2 (remain delta) for unhandled slots."""
        db = AsyncMock()
        queue_item = SimpleNamespace(ams_mapping=None, plate_id=None)
        queue_result = MagicMock()
        queue_result.scalar_one_or_none.return_value = queue_item
        delete_result = MagicMock()
        db.execute = AsyncMock(side_effect=[queue_result, delete_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_uuid": "AAAA", "tray_type": "PLA", "remain": 90}]}]}
        )

        mock_settings = MagicMock()
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_settings.base_dir.__truediv__.return_value = mock_path

        with (
            patch("backend.app.services.spoolman_tracking.app_settings", mock_settings),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=[{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#000000"}],
            ),
            patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=None),
            patch("backend.app.utils.threemf_tools.extract_filament_properties_from_3mf", return_value={}),
        ):
            await store_print_data(
                printer_id=1,
                archive_id=42,
                file_path="archives/test.3mf",
                db=db,
                printer_manager=printer_manager,
            )

        db.add.assert_called_once()
        tracking = db.add.call_args.args[0]
        assert tracking.filament_usage == [{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#000000"}]
        assert tracking.tray_remain_start == {"0-0": {"remain": 90, "tray_uuid": "AAAA"}}


class TestReportUsageRemainDelta:
    """report_usage must write a per-slot remain-delta when filament_usage
    is missing (no-3MF print), gated on a resolvable Spoolman spool and a
    sane current remain%."""

    @pytest.mark.asyncio
    async def test_remain_delta_writes_to_resolved_spool(self):
        """Print started at remain=80% on a 1000g filament, finished at 60%.
        Delta = 20% × 1000g = 200g."""
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            filament_usage=None,
            ams_trays={"0": {"tray_uuid": "AAAA", "tag_uid": "11", "tray_type": "PLA"}},
            slot_to_tray=None,
            tray_remain_start={"0-0": {"remain": 80, "tray_uuid": "AAAA"}},
        )

        # Mock db.execute().scalar_one_or_none() -> tracking
        db = AsyncMock()
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = tracking
        db.execute = AsyncMock(return_value=select_result)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        client = AsyncMock()
        client.get_spool = AsyncMock(return_value={"id": 7, "filament": {"weight": 1000.0, "color_hex": "00FF00"}})
        client.use_spool = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_uuid": "AAAA", "remain": 60}]}]}
        )

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="serial")),
            patch(
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                AsyncMock(return_value=7),
            ),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await report_usage(printer_id=1, archive_id=42)

        client.use_spool.assert_awaited_once_with(7, 200.0)

    @pytest.mark.asyncio
    async def test_remain_delta_skips_swapped_spool(self):
        """tray_uuid changed between start and completion → user replaced
        the spool mid-print. We don't know how much went to each side; skip
        rather than mis-charge."""
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            filament_usage=None,
            ams_trays={"0": {"tray_uuid": "AAAA"}},
            slot_to_tray=None,
            tray_remain_start={"0-0": {"remain": 80, "tray_uuid": "AAAA"}},
        )

        db = AsyncMock()
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = tracking
        db.execute = AsyncMock(return_value=select_result)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        client = AsyncMock()
        client.use_spool = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            # tray_uuid changed -> swap detected
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_uuid": "CCCC", "remain": 50}]}]}
        )

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="serial")),
            patch(
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                AsyncMock(return_value=7),
            ),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await report_usage(printer_id=1, archive_id=42)

        client.use_spool.assert_not_called()

    @pytest.mark.asyncio
    async def test_remain_delta_skips_slot_handled_by_3mf(self):
        """Mixed coverage: 3MF carried slot 1 (=global tray 0). Remain
        delta on the same physical slot must not double-charge it."""
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 50.0}],
            ams_trays={"0": {"tray_uuid": "AAAA", "tray_type": "PLA"}},
            slot_to_tray=None,
            tray_remain_start={"0-0": {"remain": 80, "tray_uuid": "AAAA"}},
        )

        db = AsyncMock()
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = tracking
        db.execute = AsyncMock(return_value=select_result)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        client = AsyncMock()
        client.use_spool = AsyncMock()
        client.get_spool = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_uuid": "AAAA", "remain": 60}]}]}
        )

        # Make the 3MF path resolve to a spool too, so it actually writes.
        async def fake_report_slots(_client, items, *args, **kwargs):
            for _slot_id, grams in items:
                if grams > 0:
                    await _client.use_spool(99, grams)
            return 1

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="serial")),
            patch(
                "backend.app.services.spoolman_tracking._report_spool_usage_for_slots",
                AsyncMock(side_effect=fake_report_slots),
            ),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await report_usage(printer_id=1, archive_id=42)

        # Only the 3MF path called use_spool. get_spool (remain-delta path)
        # was never reached because slot 0 was already in the handled set.
        client.use_spool.assert_awaited_once_with(99, 50.0)
        client.get_spool.assert_not_called()


class TestPartialUsageRemainDelta:
    """cleanup_tracking → _report_partial_usage must also write the
    remain-delta for ABORTED no-3MF prints — same shape as the completion
    path, otherwise aborts of "Untitled" prints stay silent."""

    @pytest.mark.asyncio
    async def test_aborted_no_3mf_writes_remain_delta(self):
        """Aborted at remain=70% from start of 90% on a 1000g spool.
        Delta = 20% × 1000g = 200g — must be written even though no
        3MF / layer data is available."""
        from backend.app.services.spoolman_tracking import _report_partial_usage

        tracking = SimpleNamespace(
            archive_id=99,
            filament_usage=None,
            layer_usage=None,
            filament_properties=None,
            ams_trays={"0": {"tray_uuid": "AAAA"}},
            slot_to_tray=None,
            tray_remain_start={"0-0": {"remain": 90, "tray_uuid": "AAAA"}},
        )

        client = AsyncMock()
        client.get_spool = AsyncMock(return_value={"id": 7, "filament": {"weight": 1000.0}})
        client.use_spool = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_uuid": "AAAA", "remain": 70}]}]},
            layer_num=42,
            total_layers=100,
        )

        with (
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch(
                "backend.app.services.spoolman_tracking._get_printer_serial",
                AsyncMock(return_value="serial"),
            ),
            patch(
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                AsyncMock(return_value=7),
            ),
            patch("backend.app.services.printer_manager.printer_manager", printer_manager),
        ):
            await _report_partial_usage(printer_id=1, tracking=tracking)

        client.use_spool.assert_awaited_once_with(7, 200.0)


class _AsyncCtx:
    """Tiny async-context shim returning a pre-built db mock; mirrors
    async_session()'s ``async with`` interface."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_):
        return False
