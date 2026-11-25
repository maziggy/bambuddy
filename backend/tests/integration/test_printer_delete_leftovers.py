"""Deleting a printer drops what Bambuddy kept for its id (SQLite gives the id
to the next printer created). See unit/test_forget_printer.py for the full list."""

import pytest
from httpx import AsyncClient

from backend.app import main
from backend.app.models.printer import Printer
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_manager import printer_manager

pytestmark = [pytest.mark.integration]


async def test_the_next_printer_given_the_id_starts_clean(async_client: AsyncClient, db_session, tmp_path, monkeypatch):
    from backend.app.services import plate_detection

    monkeypatch.setattr(plate_detection, "_get_calibration_dir", lambda: tmp_path)
    printer = Printer(name="A", serial_number="LEFT-A", ip_address="192.0.2.41", access_code="x")
    db_session.add(printer)
    await db_session.commit()
    pid = printer.id
    # Mid-print, dispatched for alice, plate calibrated.
    printer_manager.set_current_print_user(pid, 1, "alice")
    printer_manager._awaiting_plate_clear.add(pid)
    main._active_prints[(pid, "cube.3mf")] = 42
    (tmp_path / f"printer_{pid}_ref_0.jpg").write_bytes(b"jpg")
    (tmp_path / f"printer_{pid}_metadata.json").write_text("{}")
    try:
        response = await async_client.delete(f"/api/v1/printers/{pid}")
        assert response.status_code == 200, response.text

        assert printer_manager.get_current_print_user(pid) is None
        assert not printer_manager.is_awaiting_plate_clear(pid)
        state = PrinterState(subtask_name="cube", gcode_file="cube.3mf")
        assert not main._is_bambuddy_authorized_print_in_memory(pid, state)  # the kill switch stops it
        assert list(tmp_path.iterdir()) == []
    finally:
        printer_manager._current_print_user.pop(pid, None)
        printer_manager._awaiting_plate_clear.discard(pid)
        main._active_prints.pop((pid, "cube.3mf"), None)


async def _print_sessions(db, printer_id: int) -> None:
    from datetime import datetime

    from backend.app.models.active_print_session import ActivePrintSession
    from backend.app.models.active_print_spoolman import ActivePrintSpoolman
    from backend.app.models.archive import PrintArchive

    archive = PrintArchive(filename="cube.3mf", file_path="x", file_size=1, printer_id=printer_id)
    db.add(archive)
    await db.commit()
    db.add_all(
        [
            ActivePrintSession(printer_id=printer_id, print_name="cube", started_at=datetime(2026, 1, 1)),
            ActivePrintSpoolman(printer_id=printer_id, archive_id=archive.id, ams_trays={}),
        ]
    )
    await db.commit()


async def _session_rows(db) -> tuple[list, list]:
    from sqlalchemy import select

    from backend.app.models.active_print_session import ActivePrintSession
    from backend.app.models.active_print_spoolman import ActivePrintSpoolman

    sessions = (await db.execute(select(ActivePrintSession.printer_id))).scalars().all()
    spoolman = (await db.execute(select(ActivePrintSpoolman.printer_id))).scalars().all()
    return list(sessions), list(spoolman)


async def test_the_print_being_tracked_goes_with_the_printer(async_client: AsyncClient, db_session):
    """A new printer given the id and already printing would restore it, and its
    filament would be charged to the deleted printer's spools."""
    printer = Printer(name="A", serial_number="LEFT-B", ip_address="192.0.2.42", access_code="x")
    db_session.add(printer)
    await db_session.commit()
    await _print_sessions(db_session, printer.id)

    response = await async_client.delete(f"/api/v1/printers/{printer.id}", params={"delete_archives": "false"})

    assert response.status_code == 200, response.text
    assert await _session_rows(db_session) == ([], [])


@pytest.mark.sqlite_only  # stages stale ids with a raw delete, which foreign keys refuse or null out
async def test_the_startup_repair_drops_print_tracking_of_deleted_printers(db_session):
    from sqlalchemy import text

    from backend.app.core.database import _repair_sqlite_orphans

    printer = Printer(name="A", serial_number="LEFT-C", ip_address="192.0.2.43", access_code="x")
    db_session.add(printer)
    await db_session.commit()
    await _print_sessions(db_session, printer.id)
    await db_session.execute(text("DELETE FROM printers WHERE id = :id"), {"id": printer.id})  # the old way
    await db_session.commit()

    await _repair_sqlite_orphans(await db_session.connection())
    await db_session.commit()

    assert await _session_rows(db_session) == ([], [])


async def test_an_external_camera_stream_of_the_printer_ends(async_client: AsyncClient, db_session):  # noqa: ARG001 - patches the session factory
    """An MJPEG or snapshot camera runs no ffmpeg the stop could find: a viewer
    left open kept writing the old camera's frames under the id, which plate
    detection and the finish photo of the next printer given it would read."""
    import asyncio
    from unittest.mock import MagicMock, patch

    from backend.app.api.routes import camera
    from backend.app.core.auth import StreamOpener

    printer = Printer(
        name="A",
        serial_number="LEFT-D",
        ip_address="192.0.2.44",
        access_code="x",
        external_camera_enabled=True,
        external_camera_url="http://192.0.2.51/mjpeg",
        external_camera_type="mjpeg",
    )
    db_session.add(printer)
    await db_session.commit()

    async def _frames(*_args, on_frame=None, **_kwargs):
        while True:
            on_frame(b"\xff\xd8old\xff\xd9")
            yield b"--frame\r\n"
            await asyncio.sleep(0)

    with patch("backend.app.services.external_camera.generate_mjpeg_stream", _frames):
        response = await camera.camera_stream(printer.id, MagicMock(), fps=5, opener=StreamOpener(auth_on=False))
        stream = response.body_iterator
        await anext(stream)
        assert camera._last_frames.get(printer.id)

        await camera.forget_printer(printer.id)

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 2)
    assert printer.id not in camera._last_frames


async def test_a_new_printer_starts_without_plate_references(
    async_client: AsyncClient, db_session, tmp_path, monkeypatch
):
    """Ones left under its id by a delete before deletes removed them would show
    as its calibration and be compared with its plate."""
    from unittest.mock import AsyncMock, patch

    from backend.app.services import plate_detection

    monkeypatch.setattr(plate_detection, "_get_calibration_dir", lambda: tmp_path)
    existing = Printer(name="A", serial_number="LEFT-E", ip_address="192.0.2.45", access_code="x")
    db_session.add(existing)
    await db_session.commit()
    reused = existing.id + 1  # SQLite: the highest id plus one
    for printer_id in (existing.id, reused):
        (tmp_path / f"printer_{printer_id}_ref_0.jpg").write_bytes(b"jpg")
        (tmp_path / f"printer_{printer_id}_metadata.json").write_text("{}")

    with patch(
        "backend.app.api.routes.printers.printer_manager.test_connection",
        AsyncMock(return_value={"success": True}),
    ):
        response = await async_client.post(
            "/api/v1/printers/",
            json={
                "name": "B",
                "serial_number": "LEFT-F",
                "ip_address": "192.0.2.46",
                "access_code": "12345678",
                "is_active": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == reused
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        f"printer_{existing.id}_metadata.json",
        f"printer_{existing.id}_ref_0.jpg",
    ]
