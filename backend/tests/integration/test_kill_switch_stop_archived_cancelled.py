"""A print the kill switch stopped is archived as cancelled, with its reason.

The printer reports a stop as "failed" or "aborted", and only stops made from
Bambuddy's queue were turned into "cancelled". A kill-switch stop therefore
went into the failure statistics as a failed print with no reason. It is now
"cancelled" with the reason ``killSwitch``.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app import main as main_module
from backend.app.services.archive import ArchiveService
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    _clear_print_state,
    _drive_print_start,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean():
    for tracked in (main_module._active_prints, main_module._kill_switch_stopped, main_module._user_stopped_printers):
        tracked.clear()
    yield
    for tracked in (main_module._active_prints, main_module._kill_switch_stopped, main_module._user_stopped_printers):
        tracked.clear()


async def _complete(printer_id, status, update):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    # Plain mocks for what the reads return: an AsyncMock's results are
    # AsyncMocks too, so a plain call on one (completed_at.astimezone(),
    # .first()) leaves an unawaited coroutine, reported in a later test.
    for read in ("execute", "get", "scalar", "scalars"):
        setattr(session, read, AsyncMock(return_value=MagicMock()))
    tasks_before = set(asyncio.all_tasks())
    with (
        patch("backend.app.main.async_session", return_value=session),
        patch("backend.app.core.database.async_session", return_value=session),
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.printer_manager") as pm,
        patch.object(ArchiveService, "update_archive_status", update),
    ):
        ws.send_print_complete = AsyncMock()
        ws.broadcast = AsyncMock()
        relay.on_print_complete = AsyncMock()
        notif.on_print_complete = AsyncMock()
        plug.on_print_complete = AsyncMock()
        pm.get_printer.return_value = None
        await main_module.on_print_complete(
            printer_id,
            {"status": status, "filename": "x.gcode", "subtask_name": "Kill_Me", "timelapse_was_active": False},
        )
        for task in asyncio.all_tasks() - tasks_before:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["failed", "aborted"])
async def test_a_kill_switch_stop_is_a_cancel_with_its_reason(reported):
    main_module._active_prints[(7, "Kill_Me.3mf")] = 55
    main_module._kill_switch_stopped.add(7)
    update = AsyncMock()

    await _complete(7, reported, update)

    kwargs = update.await_args.kwargs
    assert (kwargs["status"], kwargs["failure_reason"]) == ("cancelled", "killSwitch")
    assert 7 not in main_module._kill_switch_stopped


@pytest.mark.asyncio
async def test_a_print_that_failed_on_its_own_stays_failed():
    main_module._active_prints[(7, "Kill_Me.3mf")] = 55
    update = AsyncMock()

    await _complete(7, "failed", update)

    assert update.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_the_next_print_starts_without_the_flag(test_engine, printer_factory, monkeypatch):
    """A stop whose completion never arrived must not mark the next print."""
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    printer_id = (await printer_factory()).id
    main_module._kill_switch_stopped.add(printer_id)

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=False)

    assert printer_id not in main_module._kill_switch_stopped


def test_the_reason_is_in_the_vocabulary():
    from backend.app.api.routes.print_log import _FAILURE_REASON_KEYS

    assert "killSwitch" in _FAILURE_REASON_KEYS


def test_the_archive_editor_offers_the_same_vocabulary():
    """A reason the editor does not list shows untranslated, as its own cause."""
    import re
    from pathlib import Path

    from backend.app.api.routes.print_log import _FAILURE_REASON_KEYS

    source = (Path(__file__).parents[3] / "frontend/src/components/EditArchiveModal.tsx").read_text()
    listed = re.search(r"FAILURE_REASON_KEYS[^=]*=\s*\[(.*?)\]", source, re.S).group(1)

    assert set(re.findall(r"'([^']*)'", listed)) == _FAILURE_REASON_KEYS - {""}
