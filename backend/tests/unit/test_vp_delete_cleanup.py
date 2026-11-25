"""Tests for DELETE /virtual-printers/{vp_id} orphan cleanup.

Before the fix, deleting a VP only stopped the running instance and
removed the row. The on-disk ``base_dir/uploads/<vp_id>/`` directory
lingered, and any ``PendingUpload`` rows that pointed into it remained
in ``pending`` status — showing up as phantom entries in
``/pending-uploads/``. The route now (a) marks those rows as
``discarded`` and (b) ``shutil.rmtree``s the upload_dir after the DB
commit succeeds.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.virtual_printers import delete_virtual_printer
from backend.app.core.printer_scope import ALL_PRINTERS


@pytest.mark.asyncio
async def test_delete_vp_marks_orphan_pending_uploads_discarded(tmp_path, db_session):
    """A VP with PendingUpload rows pointing at its upload_dir: after
    DELETE, those rows must be flipped to ``discarded`` and the on-disk
    directory must be gone."""
    from sqlalchemy import select

    from backend.app.models.pending_upload import PendingUpload
    from backend.app.models.virtual_printer import VirtualPrinter

    vp = VirtualPrinter(name="DeleteMe", serial_suffix="391800077")
    db_session.add(vp)
    await db_session.commit()
    vp_id = vp.id
    upload_dir = tmp_path / "uploads" / str(vp_id)
    upload_dir.mkdir(parents=True)
    (upload_dir / "stale.3mf").write_bytes(b"orphaned content")

    def pending(name, path):
        return PendingUpload(filename=name, file_path=str(path), file_size=1, status="pending")

    db_session.add_all(
        [
            pending("a", upload_dir / "stale.3mf"),
            pending("b", upload_dir / "another.3mf"),
            # Unrelated PendingUpload that does NOT belong to this VP — must be left alone.
            pending("other", tmp_path / "uploads" / "99" / "not-mine.3mf"),
        ]
    )
    await db_session.commit()

    fake_manager = MagicMock()
    fake_manager.remove_instance = AsyncMock()
    fake_manager.sync_from_db = AsyncMock()
    fake_manager._base_dir = tmp_path

    with patch(
        "backend.app.services.virtual_printer.virtual_printer_manager",
        fake_manager,
    ):
        await delete_virtual_printer(vp_id=vp_id, db=db_session, _=None, printer_scope=ALL_PRINTERS)

    statuses = dict(
        (
            await db_session.execute(
                select(PendingUpload.filename, PendingUpload.status).execution_options(populate_existing=True)
            )
        ).all()
    )
    assert statuses == {"a": "discarded", "b": "discarded", "other": "pending"}
    assert await db_session.get(VirtualPrinter, vp_id) is None

    # The on-disk upload_dir is gone.
    assert not upload_dir.exists()

    # The running instance was stopped before the row was removed.
    fake_manager.remove_instance.assert_awaited_once_with(vp_id)


@pytest.mark.asyncio
async def test_delete_vp_with_no_orphan_uploads_still_succeeds(tmp_path):
    """A VP with no PendingUpload rows and no upload_dir on disk: the
    cleanup path must be a clean no-op, not raise."""
    vp_id = 88

    vp_row = MagicMock()
    vp_row.id = vp_id
    vp_row.name = "EmptyDelete"

    select_calls = {"i": 0}

    async def fake_execute(query):  # noqa: ARG001
        select_calls["i"] += 1
        result = MagicMock()
        if select_calls["i"] == 1:
            result.scalar_one_or_none = MagicMock(return_value=vp_row)
        elif select_calls["i"] == 2:
            # No PendingUpload rows match.
            scalars = MagicMock()
            scalars.all = MagicMock(return_value=[])
            result.scalars = MagicMock(return_value=scalars)
        return result

    db = AsyncMock()
    db.execute = fake_execute
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    fake_manager = MagicMock()
    fake_manager.remove_instance = AsyncMock()
    fake_manager.sync_from_db = AsyncMock()
    fake_manager._base_dir = tmp_path  # no uploads/<vp_id> exists

    with patch(
        "backend.app.services.virtual_printer.virtual_printer_manager",
        fake_manager,
    ):
        await delete_virtual_printer(vp_id=vp_id, db=db, _=None, printer_scope=ALL_PRINTERS)

    fake_manager.remove_instance.assert_awaited_once_with(vp_id)
    # No directory to remove — and we didn't crash trying to.
    assert not (tmp_path / "uploads" / str(vp_id)).exists()


@pytest.mark.asyncio
async def test_deleting_vp_1_leaves_the_uploads_of_vp_12_alone(tmp_path, db_session):
    """uploads/1 is a prefix of uploads/12/... as a string, not as a folder."""
    from sqlalchemy import select

    from backend.app.api.routes.virtual_printers import drop_virtual_printer
    from backend.app.models.pending_upload import PendingUpload

    fake_manager = MagicMock()
    fake_manager._base_dir = tmp_path
    db_session.add_all(
        [
            PendingUpload(filename="mine", file_path=str(tmp_path / "uploads" / "1" / "a.3mf"), file_size=1),
            PendingUpload(filename="vp 12", file_path=str(tmp_path / "uploads" / "12" / "b.3mf"), file_size=1),
        ]
    )
    await db_session.commit()

    with patch("backend.app.services.virtual_printer.virtual_printer_manager", fake_manager):
        await drop_virtual_printer(db_session, 1)
    await db_session.commit()

    statement = select(PendingUpload.filename, PendingUpload.status).execution_options(populate_existing=True)
    assert dict((await db_session.execute(statement)).all()) == {"mine": "discarded", "vp 12": "pending"}
