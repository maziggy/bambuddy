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


@pytest.mark.asyncio
async def test_delete_vp_marks_orphan_pending_uploads_discarded(tmp_path):
    """A VP with PendingUpload rows pointing at its upload_dir: after
    DELETE, those rows must be flipped to ``discarded`` and the on-disk
    directory must be gone."""
    vp_id = 77
    upload_dir = tmp_path / "uploads" / str(vp_id)
    upload_dir.mkdir(parents=True)
    (upload_dir / "stale.3mf").write_bytes(b"orphaned content")

    # Build PendingUpload-like mocks. The route mutates `.status`.
    pending_a = MagicMock()
    pending_a.file_path = str(upload_dir / "stale.3mf")
    pending_a.status = "pending"
    pending_b = MagicMock()
    pending_b.file_path = str(upload_dir / "another.3mf")
    pending_b.status = "pending"

    # Unrelated PendingUpload that does NOT belong to this VP — must
    # be left alone.
    other_pending = MagicMock()
    other_pending.file_path = str(tmp_path / "uploads" / "99" / "not-mine.3mf")
    other_pending.status = "pending"

    # Mock VP row.
    vp_row = MagicMock()
    vp_row.id = vp_id
    vp_row.name = "DeleteMe"

    # Mock DB session with the route's two .execute() calls + flush + commit.
    select_calls = {"i": 0}

    async def fake_execute(query):  # noqa: ARG001
        """Return the VP row on the first call (vp lookup) and the
        in-range PendingUpload rows on the second call (orphan query).
        Third call is the DELETE which doesn't need a result."""
        select_calls["i"] += 1
        result = MagicMock()
        if select_calls["i"] == 1:
            result.scalar_one_or_none = MagicMock(return_value=vp_row)
        elif select_calls["i"] == 2:
            scalars = MagicMock()
            scalars.all = MagicMock(return_value=[pending_a, pending_b])
            result.scalars = MagicMock(return_value=scalars)
        return result

    db = AsyncMock()
    db.execute = fake_execute
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    # Mock the manager: remove_instance, _base_dir, sync_from_db.
    fake_manager = MagicMock()
    fake_manager.remove_instance = AsyncMock()
    fake_manager.sync_from_db = AsyncMock()
    fake_manager._base_dir = tmp_path

    with patch(
        "backend.app.services.virtual_printer.virtual_printer_manager",
        fake_manager,
    ):
        await delete_virtual_printer(vp_id=vp_id, db=db, _=None)

    # Both in-range PendingUpload rows must be flipped to "discarded".
    assert pending_a.status == "discarded"
    assert pending_b.status == "discarded"
    # The unrelated row was never returned from the query — left alone.
    assert other_pending.status == "pending"


@pytest.mark.asyncio
async def test_delete_vp_leaves_archived_pending_uploads_untouched(tmp_path):
    """Already-archived VP uploads must not be overwritten to discarded."""
    vp_id = 77
    upload_dir = tmp_path / "uploads" / str(vp_id)
    upload_dir.mkdir(parents=True)

    archived = MagicMock()
    archived.file_path = str(upload_dir / "done.3mf")
    archived.status = "archived"

    vp_row = MagicMock()
    vp_row.id = vp_id
    vp_row.name = "DeleteMe"

    select_calls = {"i": 0}

    async def fake_execute(query):  # noqa: ARG001
        select_calls["i"] += 1
        result = MagicMock()
        if select_calls["i"] == 1:
            result.scalar_one_or_none = MagicMock(return_value=vp_row)
        elif select_calls["i"] == 2:
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
    fake_manager._base_dir = tmp_path

    with patch(
        "backend.app.services.virtual_printer.virtual_printer_manager",
        fake_manager,
    ):
        await delete_virtual_printer(vp_id=vp_id, db=db, _=None)

    assert archived.status == "archived"

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
        await delete_virtual_printer(vp_id=vp_id, db=db, _=None)

    fake_manager.remove_instance.assert_awaited_once_with(vp_id)
    # No directory to remove — and we didn't crash trying to.
    assert not (tmp_path / "uploads" / str(vp_id)).exists()
