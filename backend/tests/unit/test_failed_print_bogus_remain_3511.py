"""A print stopped before its first layer charges nothing, whatever the AMS reads (#3511).

The reporter's X2D failed 290 s in. The 3MF named slot 2 on AMS0-T3 and worked
out 0 g at layer 0, but did not claim the tray, so the remain% fallback charged
it from the AMS reading at the stop: 100% at the start, a transient 0% at the
end, the whole 1000 g spool.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.usage_tracker import PrintSession, _active_sessions, _track_from_3mf, on_print_complete
from backend.tests.unit.test_usage_tracker import (
    _make_archive,
    _make_assignment,
    _make_spool,
    _mock_db_sequential,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# The reporter's plate: one PETG filament in slot 2, mapped to AMS0-T3.
USAGE = [{"slot_id": 2, "used_g": 28.05, "type": "PETG", "color": ""}]
MAPPING = [65535, 3]


def _stopped_state(
    remain_at_stop: int = 0, mqtt_mapping: list | None = MAPPING, last_loaded_tray: int = 3
) -> SimpleNamespace:
    raw = {"ams": [{"id": 0, "tray": [{"id": 3, "remain": remain_at_stop, "tray_type": "PETG"}]}]}
    if mqtt_mapping is not None:
        raw["mapping"] = mqtt_mapping
    return SimpleNamespace(
        progress=0,
        layer_num=0,
        total_layers=120,
        tray_now=255,
        last_loaded_tray=last_loaded_tray,
        tray_change_log=[],
        spool_change_log=[],
        raw_data=raw,
    )


def _threemf(usage):
    path = MagicMock()
    path.exists.return_value = True
    settings = patch("backend.app.core.config.settings")
    return (
        settings,
        patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=usage),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=None),
        path,
    )


async def _track(usage, ams_mapping, mqtt_mapping=MAPPING, last_loaded_tray=3):
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = _stopped_state(
        mqtt_mapping=mqtt_mapping, last_loaded_tray=last_loaded_tray
    )
    handled: set = set()
    db = _mock_db_sequential([_make_archive(archive_id=32), None, _make_assignment(spool_id=38, ams_id=0, tray_id=3)])
    settings, extract, layers, path = _threemf(usage)
    with settings as mock_settings, extract, layers:
        mock_settings.base_dir.__truediv__ = MagicMock(return_value=path)
        results = await _track_from_3mf(
            printer_id=1,
            archive_id=32,
            status="failed",
            print_name="Desiccant core",
            handled_trays=handled,
            printer_manager=printer_manager,
            db=db,
            ams_mapping=ams_mapping,
        )
    return results, handled


class TestTheThreeMfClaimsTheTray:
    async def test_a_mapped_slot_stopped_before_using_any_is_handled(self):
        results, handled = await _track(USAGE, MAPPING)

        assert results == []
        assert (0, 3) in handled

    async def test_a_tray_guessed_by_position_is_left_to_the_fallback(self):
        # No mapping from the dispatch nor from the printer, and no tray loaded.
        _, handled = await _track(USAGE, None, mqtt_mapping=None, last_loaded_tray=-1)

        assert handled == set()


class TestTheCompletion:
    @pytest.fixture(autouse=True)
    def _clean(self):
        _active_sessions.clear()
        with patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None):
            yield
        _active_sessions.clear()

    async def test_the_reporters_print_charges_nothing(self):
        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Desiccant core",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 3): 100},
            tray_now_at_start=3,
            ams_mapping=MAPPING,
        )
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = _stopped_state(remain_at_stop=0)
        spool = _make_spool(spool_id=38)
        assignment = _make_assignment(spool_id=38, ams_id=0, tray_id=3)
        # The 3MF path's archive, queue item and assignment; then what the
        # fallback would read if it ran: the assignment and the spool.
        db = _mock_db_sequential([_make_archive(archive_id=32), None, assignment, assignment, spool])
        settings, extract, layers, path = _threemf(USAGE)
        with settings as mock_settings, extract, layers:
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=path)
            results = await on_print_complete(
                printer_id=1,
                data={"status": "failed"},
                printer_manager=printer_manager,
                db=db,
                archive_id=32,
                ams_mapping=MAPPING,
            )

        assert results == []
        db.add.assert_not_called()
