"""What Bambuddy keeps per printer id goes when the printer is deleted.

SQLite used to give a deleted printer's id to the next printer created, which
inherited it all: the old print's owner (its prints passed the kill switch and
were billed to that user), the plate-clear gate, pending notifications, the old
camera feed, the old printer's plate-detection references. Ids are no longer
reused, but nothing kept for a printer that is gone should stay.
"""

import ast
import asyncio
from pathlib import Path

import pytest

from backend.app import main

pytestmark = pytest.mark.asyncio

GONE, KEPT = 5, 6

# Module-level maps in main.py that are not keyed by a printer, or are kept on
# purpose. Anything else main.py adds must be forgotten with the printer.
NOT_FORGOTTEN = {
    "_print_ams_mappings": "keyed by archive (dropped with the printer's expected/active prints)",
    "_print_cost_center_ids": "keyed by archive (dropped with the printer's expected/active prints)",
    "_print_plate_ids": "keyed by archive (dropped with the printer's expected/active prints)",
    "_owned_print_archive_ids": "keyed by archive (dropped with the printer's expected/active prints)",
    "_ams_assignment_locks": "a lock carries nothing over; popping one a task holds would let a second be made",
    "_fallback_recovery_locks": "a lock carries nothing over (see its comment on popping)",
    "_HMS_FAILURE_REASONS": "constant",
    "_ACTIVE_PRINT_STATES": "constant",
    "_SENSOR_TARGET_KEYS": "constant",
    "PUBLIC_API_ROUTES": "constant",
    "_HTML_CACHE_HEADERS": "constant",
}


def _module_maps() -> set[str]:
    """Names of main.py's module-level dicts and sets."""
    tree = ast.parse(Path(main.__file__).read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            kind = ast.unparse(node.annotation)
            if kind.startswith(("dict", "set")):
                names.add(node.target.id)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, (ast.Dict, ast.Set)):
                names.add(node.targets[0].id)
    return names


def _sleeper() -> asyncio.Task:
    return asyncio.ensure_future(asyncio.sleep(3600))


def _entries(printer_id: int) -> dict[str, tuple]:
    """For each forgotten map: (key, value) for this printer."""
    return {
        "_active_prints": ((printer_id, "cube.3mf"), 100 + printer_id),
        "_expected_prints": ((printer_id, "cube.3mf"), 200 + printer_id),
        "_expected_print_creators": ((printer_id, "cube.3mf"), 1),
        "_expected_print_registered_at": ((printer_id, "cube.3mf"), 1.0),
        "_stage22_finish_frames": (printer_id, b"jpg"),
        "_stage22_finish_in_flight": (printer_id, asyncio.Event()),
        "_inprint_frame_bank": (printer_id, b"jpg"),
        "_inprint_frame_bank_ts": (printer_id, 1.0),
        "_printer_reconciled_since_connect": (printer_id, True),
        "_printer_kprofiles_primed_since_connect": (printer_id, True),
        "_last_progress_milestone": (printer_id, 50),
        "_first_layer_notified": (printer_id, True),
        "_unauthorized_print_kill_sent": (printer_id, None),
        "_kill_switch_notification_tasks": (printer_id, _sleeper()),
        "_notified_hms_errors": (printer_id, {"0300_0001": 1.0}),
        "_hms_print_state": (printer_id, "RUNNING"),
        "_hms_forget_when_gone": (printer_id, {"0300_0001"}),
        "_timelapse_baselines": (printer_id, {"video.mp4"}),
        "_timelapse_baselines_unread": (printer_id, None),
        "_bed_cool_waiters": (printer_id, {"threshold": 35.0, "filename": "cube", "registered_at": 1.0}),
        "_user_stopped_printers": (printer_id, None),
        "_kill_switch_stopped": (printer_id, None),
        "_printer_last_connected": (printer_id, True),
        "_printer_offline_published": (printer_id, None),
        "_printer_offline_notify_tasks": (printer_id, _sleeper()),
        "_unknown_tag_last_broadcast": (printer_id, {(0, 0): ("uid", "x")}),
        "_last_status_broadcast": (printer_id, "hash"),
        "_nozzle_count_updated": (printer_id, None),
        "_runtime_seen_connected": (printer_id, None),
        "_fallback_3mf_retry_tasks": (printer_id, _sleeper()),
        "_connection_watchdog_last_attempt": (printer_id, 1.0),
        "_ams_index_only_logged": ((printer_id, 0), None),
        "_ams_alarm_cooldown": (f"{printer_id}:0:humidity", None),
    }


def _put(name: str, key, value) -> None:
    target = getattr(main, name)
    if isinstance(target, set):
        target.add(key)
    else:
        target[key] = value


def _has(name: str, key) -> bool:
    return key in getattr(main, name)


async def test_every_printer_keyed_map_in_main_is_forgotten():
    covered = set(_entries(GONE))
    unclassified = _module_maps() - covered - set(NOT_FORGOTTEN)
    assert not unclassified, (
        f"main.py maps neither forgotten with a deleted printer nor listed in NOT_FORGOTTEN: {sorted(unclassified)}"
    )


async def test_forget_printer_clears_main_and_keeps_other_printers():
    gone, kept = _entries(GONE), _entries(KEPT)
    for name in gone:
        _put(name, *gone[name])
        _put(name, *kept[name])
    main._print_ams_mappings[100 + GONE] = [0]
    main._print_cost_center_ids[200 + GONE] = 1
    main._print_plate_ids[200 + GONE] = 2
    pending = {name: gone[name][1] for name in ("_printer_offline_notify_tasks", "_fallback_3mf_retry_tasks")}
    try:
        await main.forget_printer(GONE)

        left = [name for name in gone if _has(name, gone[name][0])]
        assert left == []
        lost = [name for name in kept if not _has(name, kept[name][0])]
        assert lost == []
        assert 100 + GONE not in main._print_ams_mappings
        assert 200 + GONE not in main._print_cost_center_ids
        assert 200 + GONE not in main._print_plate_ids
        await asyncio.sleep(0)
        assert all(task.cancelled() for task in pending.values())
    finally:
        for entries in (gone, kept):
            for name, (key, value) in entries.items():
                target = getattr(main, name)
                if isinstance(target, set):
                    target.discard(key)
                else:
                    target.pop(key, None)
                if isinstance(value, asyncio.Task):
                    value.cancel()


async def test_forget_printer_clears_the_services():
    from backend.app.api.routes import camera
    from backend.app.services import (
        bambu_ftp,
        kprofile_drift,
        layer_timelapse,
        obico_detection,
        print_dispatch_context,
        usage_tracker,
    )
    from backend.app.services.printer_manager import printer_manager

    printer_manager._current_print_user[GONE] = {"user_id": 1, "username": "alice"}
    printer_manager._awaiting_plate_clear.add(GONE)
    printer_manager._last_trays[GONE] = {"ams": []}
    print_dispatch_context.mark_pending(GONE)
    kprofile_drift.note_slot_configured(GONE, 0, 0, -1)
    kprofile_drift._watches[(GONE, 0, 1)] = kprofile_drift._SlotWatch()
    layer_timelapse._active_sessions[GONE] = type("S", (), {"cleanup": lambda self: None})()
    usage_tracker._active_sessions[GONE] = object()
    service = obico_detection.obico_detection_service
    for attr in ("_states", "_state_keys", "_last_class", "_errors", "_action_fired"):
        getattr(service, attr)[GONE] = object()
    bambu_ftp._threemf_path_cache[(GONE, "cube")] = Path("/nonexistent/cube.3mf")
    camera._last_frames[GONE] = b"jpg"
    camera._last_frame_times[GONE] = 1.0
    camera._stream_start_times[GONE] = 1.0
    camera._active_external_streams[GONE] = 1
    camera._external_live_readers[GONE] = 1
    from backend.app.api.routes import printers

    printers._cover_cache[GONE] = {("Cube", "front"): b"png"}
    printers._cover_404_cache[GONE] = {}
    from backend.app.services.printer_file_cache import printer_file_cache

    printer_file_cache._keep((GONE, "/cache/a.3mf"), b"3mf")

    await main.forget_printer(GONE)

    assert GONE not in printer_manager._current_print_user
    assert GONE not in printer_manager._awaiting_plate_clear
    assert GONE not in printer_manager._last_trays
    assert not print_dispatch_context.adopt(GONE)
    assert (GONE, 0, 0) not in kprofile_drift._default_chosen
    assert (GONE, 0, 1) not in kprofile_drift._watches
    assert GONE not in layer_timelapse._active_sessions
    assert GONE not in usage_tracker._active_sessions
    for attr in ("_states", "_state_keys", "_last_class", "_errors", "_action_fired"):
        assert GONE not in getattr(service, attr), attr
    assert (GONE, "cube") not in bambu_ftp._threemf_path_cache
    assert GONE not in camera._last_frames
    assert GONE not in camera._last_frame_times
    assert GONE not in camera._stream_start_times
    assert GONE not in camera._active_external_streams
    assert GONE not in camera._external_live_readers
    assert GONE not in printers._cover_cache
    assert GONE not in printers._cover_404_cache
    assert not [key for key in printer_file_cache._entries if key[0] == GONE]


async def test_the_plate_references_of_a_printer_go(tmp_path, monkeypatch):
    from backend.app.services import plate_detection

    monkeypatch.setattr(plate_detection, "_get_calibration_dir", lambda: tmp_path)
    for printer_id in (GONE, KEPT, 55):
        (tmp_path / f"printer_{printer_id}_ref_0.jpg").write_bytes(b"jpg")
        (tmp_path / f"printer_{printer_id}_metadata.json").write_text("{}")

    plate_detection.remove_printer_references(GONE)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "printer_55_metadata.json",
        "printer_55_ref_0.jpg",
        f"printer_{KEPT}_metadata.json",
        f"printer_{KEPT}_ref_0.jpg",
    ]


async def test_the_scheduler_and_live_activities_forget_it():
    from backend.app.services.bambu_ftp import FtpFailureKind
    from backend.app.services.notify_live_activities import notify_live_activities
    from backend.app.services.print_scheduler import scheduler

    scheduler._upload_backoff[GONE] = 1e12
    scheduler._upload_refusals[GONE] = 6
    scheduler._upload_refusal_notified[GONE] = "printer is not accepting files"
    scheduler._upload_refusal_kind[GONE] = FtpFailureKind.STORAGE
    scheduler._wake_failures[GONE] = 1e12
    scheduler._dispatch_holds[GONE] = (1.0, "IDLE", None)
    scheduler._auto_dry_units[(GONE, 0)] = {}
    for attr in ("_snapshots", "_new_fallbacks", "_started_at", "_progress_resets"):
        getattr(notify_live_activities, attr)[GONE] = object()
    notify_live_activities._wake.clear()

    await main.forget_printer(GONE)

    assert GONE not in scheduler._upload_backoff
    assert GONE not in scheduler._upload_refusals
    assert GONE not in scheduler._upload_refusal_notified
    assert GONE not in scheduler._upload_refusal_kind
    assert GONE not in scheduler._wake_failures
    assert GONE not in scheduler._dispatch_holds
    assert (GONE, 0) not in scheduler._auto_dry_units
    for attr in ("_snapshots", "_new_fallbacks", "_started_at", "_progress_resets"):
        assert GONE not in getattr(notify_live_activities, attr), attr
    # Its tiles are ended at once (the printer is gone), before a new one takes the id.
    assert notify_live_activities._wake.is_set()


@pytest.mark.parametrize("ml_fails", [False, True])
async def test_an_obico_check_in_flight_when_the_printer_goes_ends_quietly(ml_fails):
    """The check waits up to 30 s for the ML server. A KeyError there stopped the
    poll for every printer; an error written back stuck to the next printer given the id."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from httpx import Request, Response

    from backend.app.services.obico_detection import ObicoDetectionService

    service = ObicoDetectionService()

    async def ml_call(url, **_kwargs):
        service.forget_printer(GONE)  # deleted meanwhile
        if ml_fails:
            raise OSError("ML server gone")
        return Response(200, json={"detections": []}, request=Request("GET", url))

    client = MagicMock()
    client.get = AsyncMock(side_effect=ml_call)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    settings = {"sensitivity": "medium", "snapshot_base_url": "http://b", "ml_url": "http://o", "action": "notify"}
    with (
        patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=client),
        patch.object(service, "_capture_frame", new=AsyncMock(return_value=b"\xff\xd8jpg\xff\xd9")),
    ):
        await service._check_printer(GONE, MagicMock(state="RUNNING", task_name="job", subtask_name=""), settings)

    for attr in ("_states", "_state_keys", "_last_class", "_errors", "_action_fired"):
        assert GONE not in getattr(service, attr), attr
    assert service._last_error is None
