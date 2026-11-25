"""A printer counts as printing until its finished print's usage is booked (WU-3, AS-5).

The AMS remain% weight sync stays off while a print runs, because completion
charges the print exactly from its 3MF (#880). The tracker takes the print's
session first and only then works out and charges the usage, several awaits
later. The printer already reports FINISH, so an AMS update in that window
raised the spool's weight from remain% and the tracker then added the print
on top: charged twice.

A completion with neither file name nor subtask name returned before any of
this, leaving the session open, which kept the weight sync off until the next
print. It now closes the session; it charges nothing, having no print to
charge (AS-5).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services import usage_tracker
from backend.app.services.usage_tracker import PrintSession, _active_sessions, printer_is_charging
from backend.tests.integration.test_print_complete_end_to_end import (  # noqa: F401
    _clean,
    _complete,
    _no_finish_photo,
    queued_print,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _no_sessions_or_marks():
    _active_sessions.clear()
    usage_tracker._completing.clear()
    yield
    _active_sessions.clear()
    usage_tracker._completing.clear()


def _open_session(printer_id: int) -> None:
    _active_sessions[printer_id] = PrintSession(
        printer_id=printer_id, print_name="part", started_at=datetime.now(timezone.utc)
    )


async def _complete_watching(test_engine, printer_id: int, **extra) -> list:
    """Complete the print; the tracker, as the real one, takes the session first.

    Returns the archive each charge was made for; asserts the printer still
    counted as printing when it was made."""
    seen: list = []

    async def tracker(pid, data, printer_manager, db, **kwargs):
        _active_sessions.pop(pid, None)
        assert printer_is_charging(pid), "the AMS sync could charge the print as well"
        seen.append(kwargs.get("archive_id"))
        return []

    with patch("backend.app.services.usage_tracker.on_print_complete", tracker):
        await _complete(test_engine, printer_id, "completed", extra=extra or None)
    return seen


async def test_the_printer_counts_as_printing_until_the_usage_is_booked(test_engine, queued_print):
    _open_session(queued_print["printer"])

    assert await _complete_watching(test_engine, queued_print["printer"]) == [queued_print["archive"]]
    assert not printer_is_charging(queued_print["printer"])


async def test_a_completion_without_a_file_name_closes_the_session_and_charges_nothing(
    test_engine, db_session, queued_print
):
    """Which print ended can't be told: a printing archive is almost always an
    earlier print's, which the reconnect reconcile closes and charges. Closing
    the session is what ends the stuck weight sync."""
    from backend.app.models.archive import PrintArchive

    _open_session(queued_print["printer"])

    charges = await _complete_watching(test_engine, queued_print["printer"], filename="", subtask_name="")

    assert charges == []
    assert not printer_is_charging(queued_print["printer"]), "the weight sync stayed off"
    db_session.expire_all()
    assert (await db_session.get(PrintArchive, queued_print["archive"])).status == "printing"


async def test_a_completion_that_fails_does_not_leave_the_printer_printing(test_engine, queued_print):
    with (
        patch("backend.app.main._recover_fallback_from_cache_before_eviction", AsyncMock(side_effect=RuntimeError)),
        pytest.raises(RuntimeError),
    ):
        await _complete(test_engine, queued_print["printer"], "completed")

    assert not printer_is_charging(queued_print["printer"])


async def test_a_new_print_clears_a_stale_mark():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    usage_tracker._completing.add(5)
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(raw_data=None)

    await usage_tracker.on_print_start(5, {}, printer_manager)

    assert not printer_is_charging(5)


async def test_booking_the_usage_ends_the_mark(db_session):
    """The rest of the completion (SD cleanup, notifications) can take a while;
    the sync need not wait for it once the print is charged."""
    usage_tracker.mark_completing(5)

    await usage_tracker.discard_session(db_session, 5)

    assert not printer_is_charging(5)


async def test_a_deleted_printer_is_forgotten():
    usage_tracker.mark_completing(5)

    usage_tracker.forget_printer(5)

    assert not printer_is_charging(5)
