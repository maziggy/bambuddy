"""A completion with no archive is notified about its own queue job, or none.

When a completion matches no archive, the notification used to borrow the
queue item this printer had finished in the last five minutes. For a print
started outside Bambuddy that was some earlier, unrelated job: its owner got
the "your print is done" email, and the notification took that job's data. It
now uses only the queue item the completion itself closed (the one
_completion_belongs_to_queue_item accepted), with that job's real duration.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.user import User


async def _complete_without_archive(test_engine, printer_id: int, status: str = "completed"):
    """Run on_print_complete for a print no archive matches.

    Returns (archive_data the notification got, user-email mock). The queue
    reconciliation and the spawned notification run against the test database;
    the archive lookup sees an empty session, so the no-archive path is taken.
    """
    from backend.app.main import on_print_complete

    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    empty_session = AsyncMock()
    empty_session.__aenter__ = AsyncMock(return_value=empty_session)
    empty_session.__aexit__ = AsyncMock()
    empty_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None), scalars=MagicMock())
    )
    with (
        patch("backend.app.main.async_session", MagicMock(return_value=empty_session)),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.ws_manager") as mock_ws,
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main.spawn_background_task") as mock_spawn,
        patch("backend.app.main.clear_3mf_cache"),
    ):
        mock_ws.send_print_complete = AsyncMock()
        mock_relay.on_print_complete = AsyncMock()
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))
        mock_pm.get_current_print_user = MagicMock(return_value=None)
        mock_pm.clear_current_print_user = MagicMock()
        mock_pm.set_awaiting_plate_clear = MagicMock()

        await on_print_complete(printer_id, {"filename": "", "subtask_name": "Benchy", "status": status})

    spawned = [c for c in mock_spawn.call_args_list if c.kwargs.get("name") == "notify-no-archive"]
    assert len(spawned) == 1, mock_spawn.call_args_list
    for other in mock_spawn.call_args_list:
        if other is not spawned[0] and hasattr(other.args[0], "close"):
            other.args[0].close()

    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.main.notification_service.on_print_complete", new_callable=AsyncMock) as sent,
        patch("backend.app.main._dispatch_user_print_email", new_callable=AsyncMock) as email,
    ):
        await spawned[0].args[0]
    sent.assert_awaited_once()
    return sent.await_args.kwargs["archive_data"], email


async def _user(db_session) -> int:
    user = User(username="owner", password_hash="x", role="user", is_active=True)
    db_session.add(user)
    await db_session.commit()
    return user.id


async def _library_file(db_session, metadata: dict | None) -> int:
    lib = LibraryFile(
        filename="benchy.3mf", file_path="library/benchy.3mf", file_type="3mf", file_size=1, file_metadata=metadata
    )
    db_session.add(lib)
    await db_session.commit()
    return lib.id


async def _queue_item(db_session, printer_id: int, **fields) -> None:
    db_session.add(PrintQueueItem(printer_id=printer_id, **fields))
    await db_session.commit()


class TestAPrintFromOutsideBambuddy:
    @pytest.mark.asyncio
    async def test_is_not_attributed_to_the_job_finished_before_it(self, test_engine, db_session, printer_factory):
        printer = await printer_factory()
        owner = await _user(db_session)
        lib_id = await _library_file(db_session, {"print_time_seconds": 5400})
        await _queue_item(
            db_session,
            printer.id,
            status="completed",
            created_by_id=owner,
            library_file_id=lib_id,
            print_time_seconds=5400,
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        )

        archive_data, email = await _complete_without_archive(test_engine, printer.id)

        assert not (archive_data or {}).get("created_by_id")
        assert "print_time_seconds" not in (archive_data or {})
        email.assert_not_awaited()


class TestAQueueJobWhoseArchiveWasNotFound:
    @pytest.mark.asyncio
    async def test_is_notified_with_its_owner_and_real_duration(self, test_engine, db_session, printer_factory):
        printer = await printer_factory()
        owner = await _user(db_session)
        lib_id = await _library_file(db_session, {"print_time_seconds": 5400})
        await _queue_item(
            db_session,
            printer.id,
            status="printing",
            created_by_id=owner,
            library_file_id=lib_id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )

        archive_data, email = await _complete_without_archive(test_engine, printer.id, status="failed")

        assert archive_data["created_by_id"] == owner
        assert archive_data["print_time_seconds"] == 5400
        assert archive_data["actual_time_seconds"] == pytest.approx(1800, abs=60)
        email.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_queue_items_cached_estimate_comes_first(self, test_engine, db_session, printer_factory):
        printer = await printer_factory()
        lib_id = await _library_file(db_session, {"print_time_seconds": 5400})
        await _queue_item(db_session, printer.id, status="printing", library_file_id=lib_id, print_time_seconds=4200)

        archive_data, _ = await _complete_without_archive(test_engine, printer.id)

        assert archive_data["print_time_seconds"] == 4200
        assert "actual_time_seconds" not in archive_data  # no start time recorded
