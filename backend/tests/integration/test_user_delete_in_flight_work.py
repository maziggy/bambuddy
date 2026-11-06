"""Work a deleted user started must not be credited to whoever gets their id.

A running print, a slice, a pipeline run and an open WebSocket all keep the
user id they started with, in memory, for minutes or hours. Written after the
delete, that id is a dangling reference, and on SQLite, which gives a freed id
to the next user created, it is someone else: they are credited with the print
and billed for it, own the sliced file, see the slice job and receive the
deleted user's notifications.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main
from backend.app.api.routes.library import slice_and_persist, slice_and_persist_as_archive
from backend.app.api.routes.pipeline_runs import _make_orchestration_callable
from backend.app.core.printer_scope import ALL_PRINTERS, PrinterScope
from backend.app.core.websocket import ws_manager
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.pipeline_run import PipelineJob, PipelineRun
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.slicer_pipeline import SlicerPipeline
from backend.app.models.user import User
from backend.app.schemas.slicer import SliceRequest, SliceResponse
from backend.app.services.printer_manager import printer_manager
from backend.app.services.slice_dispatch import SliceJob, slice_dispatch
from backend.app.services.slicer_api import SliceResult

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _user(db, name: str, user_id: int | None = None) -> int:
    user = User(id=user_id, username=name, password_hash="x", role="user")
    db.add(user)
    await db.commit()
    return user.id


async def _replace_user(db, user_id: int) -> None:
    """Delete the user and create the next one on the same id.

    AUTOINCREMENT (core/database) now stops SQLite reusing ids; the id is given
    by hand, as databases from before gave it, to keep the delete's own cleanup
    covered.
    """
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()
    assert await _user(db, "bob", user_id) == user_id


def _slice_job(owner_id: int) -> SliceJob:
    job = SliceJob(id=987_654, kind="library_file", source_id=1, source_name="a.3mf", owner_id=owner_id)
    slice_dispatch._jobs[job.id] = job
    return job


def _slicer_that_runs_while(during):
    async def slice_(*args, **kwargs):
        await during()
        return SliceResult(b"PK\x03\x04 not-a-real-3mf", 3600, 12.5, 4200.0), False

    return patch("backend.app.api.routes.library._run_slicer_with_fallback", AsyncMock(side_effect=slice_))


async def test_deleting_a_user_forgets_what_the_app_holds_for_them(async_client: AsyncClient, db_session):
    alice = await _user(db_session, "alice")
    printer_manager.set_current_print_user(4242, alice, "alice")
    main._expected_print_creators[(4242, "a.3mf")] = alice
    job = _slice_job(alice)
    socket = SimpleNamespace(
        state=SimpleNamespace(
            bambuddy_principal_user_id=alice,
            bambuddy_scope_principal=("alice", None),
            bambuddy_printer_scope=None,
        ),
        close=AsyncMock(),
        send_text=AsyncMock(),
    )
    ws_manager.active_connections.append(socket)
    try:
        response = await async_client.delete(f"/api/v1/users/{alice}")
        assert response.status_code == 204, response.text

        # The print ends later; nobody is credited with it.
        assert printer_manager.get_current_print_user(4242) is None
        # Nor is the print-start email sent to whoever is given the id.
        assert (4242, "a.3mf") not in main._expected_print_creators
        # The slice job is no longer theirs, so the next user given the id can't poll it.
        assert job.owner_id is None
        # Their open dashboard is closed, not left to receive the next user's messages.
        socket.close.assert_awaited_once_with(code=4401)
        assert socket not in ws_manager.active_connections
    finally:
        printer_manager.clear_current_print_user(4242)
        main._expected_print_creators.pop((4242, "a.3mf"), None)
        slice_dispatch._jobs.pop(job.id, None)
        if socket in ws_manager.active_connections:
            ws_manager.active_connections.remove(socket)


async def test_a_slice_finishing_after_its_user_was_deleted_belongs_to_nobody(db_session):
    alice = await _user(db_session, "alice")
    job = _slice_job(alice)
    try:

        async def alice_is_deleted_and_bob_created():
            slice_dispatch.forget_owner(alice)  # what deleting her does
            await _replace_user(db_session, alice)

        with _slicer_that_runs_while(alice_is_deleted_and_bob_created):
            response = await slice_and_persist(
                db_session,
                model_bytes=b"model",
                model_filename="a.3mf",
                folder_id=None,
                extra_metadata=None,
                request=SliceRequest(printer_preset_id=1, process_preset_id=2, filament_preset_id=3),
                current_user_id=alice,
                job_id=job.id,
            )
    finally:
        slice_dispatch._jobs.pop(job.id, None)

    sliced = await db_session.get(LibraryFile, response.library_file_id)
    assert sliced.created_by_id is None


async def test_an_archive_slice_finishing_after_its_user_was_deleted_belongs_to_nobody(
    db_session, monkeypatch, tmp_path
):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    alice = await _user(db_session, "alice")
    source = PrintArchive(filename="a.3mf", file_path="archive/a.3mf", file_size=1, status="completed")
    db_session.add(source)
    await db_session.commit()
    job = _slice_job(alice)
    try:

        async def alice_is_deleted_and_bob_created():
            slice_dispatch.forget_owner(alice)
            await _replace_user(db_session, alice)

        with _slicer_that_runs_while(alice_is_deleted_and_bob_created):
            response = await slice_and_persist_as_archive(
                db_session,
                model_bytes=b"model",
                model_filename="a.3mf",
                request=SliceRequest(printer_preset_id=1, process_preset_id=2, filament_preset_id=3),
                source_archive=source,
                current_user_id=alice,
                job_id=job.id,
            )
    finally:
        slice_dispatch._jobs.pop(job.id, None)

    sliced = await db_session.get(PrintArchive, response.archive_id)
    assert sliced.created_by_id is None


async def _run_pipeline_while_its_creator_is_replaced(
    db_session, test_engine, tmp_path, *, specific_printer: bool, printer_scope=ALL_PRINTERS
) -> list[PrintQueueItem]:
    alice = await _user(db_session, "alice")
    printer = Printer(name="P", serial_number="SPIPE1", ip_address="192.0.2.9", access_code="x", model="X1C")
    source = LibraryFile(filename="a.3mf", file_path="library/a.3mf", file_type="3mf", file_size=1)
    pipeline = SlicerPipeline(
        name="p",
        printer_preset_source="local",
        printer_preset_id="1",
        process_preset_source="local",
        process_preset_id="2",
        filament_presets_json='[{"source": "local", "id": "3"}]',
    )
    db_session.add_all([printer, source, pipeline])
    await db_session.commit()
    if specific_printer:
        pipeline.target_kind, pipeline.target_printer_id = "specific_printer", printer.id
    else:
        pipeline.target_kind, pipeline.target_model_class = "printer_class", "X1C"
    run = PipelineRun(pipeline_id=pipeline.id, source_library_file_id=source.id, copies=1, created_by=alice)
    db_session.add(run)
    await db_session.commit()
    db_session.add(PipelineJob(pipeline_run_id=run.id, copy_index=0))
    await db_session.commit()
    model = tmp_path / "a.3mf"
    model.write_bytes(b"model")

    async def slice_and_persist_while_alice_is_replaced(session, **kwargs):
        await _replace_user(db_session, alice)
        sliced = LibraryFile(filename="a.gcode.3mf", file_path="library/a.gcode.3mf", file_type="3mf", file_size=1)
        session.add(sliced)
        await session.flush()
        return SliceResponse(
            library_file_id=sliced.id, name="a", print_time_seconds=1, filament_used_g=1.0, filament_used_mm=1.0
        )

    orchestrate = _make_orchestration_callable(
        run_id=run.id,
        pipeline_id=pipeline.id,
        src_kind="library_file",
        src_id=source.id,
        src_filename="a.3mf",
        src_path=Path(model),
        creator_user_id=alice,
        copies=1,
        printer_scope=printer_scope,
    )
    with (
        patch(
            "backend.app.api.routes.pipeline_runs.async_session",
            async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False),
        ),
        patch("backend.app.api.routes.library.slice_and_persist", new=slice_and_persist_while_alice_is_replaced),
        patch("backend.app.api.routes.pipeline_runs._publish_run_event", new=AsyncMock()),
    ):
        await orchestrate(1)

    statement = select(PrintQueueItem).execution_options(populate_existing=True)
    return list((await db_session.execute(statement)).scalars().all())


async def test_a_pipeline_run_finishing_after_its_user_was_deleted_queues_ownerless_copies(
    db_session, test_engine, tmp_path
):
    copies = await _run_pipeline_while_its_creator_is_replaced(db_session, test_engine, tmp_path, specific_printer=True)

    assert [copy.created_by_id for copy in copies] == [None]


async def test_any_model_copies_of_a_deleted_restricted_user_wait_for_an_admin(db_session, test_engine, tmp_path):
    """As delete_user does with such jobs that already exist: without an owner the
    scheduler would run them on any printer of the model, not only on theirs."""
    copies = await _run_pipeline_while_its_creator_is_replaced(
        db_session, test_engine, tmp_path, specific_printer=False, printer_scope=PrinterScope(frozenset({1}))
    )

    assert [(copy.printer_id, copy.created_by_id, copy.manual_start) for copy in copies] == [(None, None, True)]


async def test_an_ownerless_run_is_not_announced_to_everyone(db_session):
    """broadcast_to_user(None) goes to every connection; with auth on, a run whose
    user was deleted is nobody's to watch (the runs page polls)."""
    from backend.app.api.routes.pipeline_runs import _publish_run_event
    from backend.app.api.routes.settings import set_setting

    run = PipelineRun(copies=1, created_by=None)
    db_session.add(run)
    await db_session.commit()

    with patch("backend.app.api.routes.pipeline_runs.ws_manager.broadcast_to_user", new=AsyncMock()) as sent:
        await _publish_run_event(db_session, run)
        assert sent.await_count == 1  # auth off: one user, everything is theirs
        await set_setting(db_session, "auth_enabled", "true")
        await db_session.commit()
        await _publish_run_event(db_session, run)
        assert sent.await_count == 1


async def test_a_finished_print_is_credited_to_its_user_unless_they_were_deleted(db_session):
    from backend.app.main import _print_user_id_if_still_there

    alice = await _user(db_session, "alice")
    # Renamed while the print ran: still theirs.
    assert await _print_user_id_if_still_there(db_session, {"user_id": alice, "username": "old name"}) == alice
    await db_session.execute(delete(User).where(User.id == alice))
    await db_session.commit()
    assert await _print_user_id_if_still_there(db_session, {"user_id": alice, "username": "alice"}) is None


async def test_a_queue_print_whose_user_was_deleted_during_the_upload_is_nobodys(db_session, test_engine, monkeypatch):
    """The scheduler loaded the user and the item before the upload; after it,
    neither may bring the deleted user back (and with them, whoever gets the id)."""
    from backend.app.services import print_scheduler as scheduler_module
    from backend.app.services.print_scheduler import PrintScheduler

    alice = await _user(db_session, "alice")
    printer = Printer(name="P", serial_number="SUPL1", ip_address="192.0.2.10", access_code="x")
    db_session.add(printer)
    await db_session.commit()
    item = PrintQueueItem(printer_id=printer.id, position=1, created_by_id=alice)
    db_session.add(item)
    await db_session.commit()
    assert await db_session.get(User, alice) is not None  # held by the dispatch, as _start_print does

    other = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with other() as admin_db:  # meanwhile, through the API
        await admin_db.execute(delete(User).where(User.id == alice))
        await admin_db.commit()
        admin_db.add(User(username="bob", password_hash="x", role="user"))
        await admin_db.commit()

    credited: list = []
    monkeypatch.setattr(scheduler_module.printer_manager, "set_current_print_user", lambda *args: credited.append(args))
    await PrintScheduler()._propagate_owner_to_printer_manager(db_session, item)

    assert credited == []
