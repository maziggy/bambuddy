"""Deleting a run does not lose the charge waiting on it (#3581).

A charge that waits for its run's energy is kept on the run row. The print
log's clear and delete, and an archive deleted with its statistics (by hand
or by the purge), remove run rows: each writes the waiting charge first. So
does what takes a run's archive away and leaves the run: deleting a printer
with its archives, deleting a user with their items, and merging archives.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from backend.app.models.archive import PrintArchive
from backend.app.models.finance import WalletTransaction
from backend.app.models.print_log import PrintLogEntry
from backend.app.services.finance_billing import defer_print_charge
from backend.tests.unit.services.test_billing_energy_wear_3581 import shop  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
async def waiting(db_session, shop):  # noqa: F811
    run = await db_session.get(PrintLogEntry, shop["run"])
    defer_print_charge(run, shop["archive"], {"run_entry_id": run.id, "run_status": "completed", "wear_cost": 0.25})
    await db_session.commit()
    return shop


async def _state(db_session) -> tuple[list[tuple[float, str]], int]:
    """The charges written, and how many runs are left."""
    db_session.expire_all()
    charges = (await db_session.scalars(select(WalletTransaction).order_by(WalletTransaction.id))).all()
    runs = await db_session.scalar(select(func.count(PrintLogEntry.id)))
    return [(c.amount, c.print_run_id) for c in charges], runs


CHARGED_AND_GONE = ([(-1.45, "run-1")], 0)


async def test_clearing_the_print_log(async_client: AsyncClient, db_session, waiting):
    assert (await async_client.delete("/api/v1/print-log/")).status_code == 200
    assert await _state(db_session) == CHARGED_AND_GONE


async def test_deleting_its_print_log_entry(async_client: AsyncClient, db_session, waiting):
    assert (await async_client.delete(f"/api/v1/print-log/{waiting['run']}")).status_code == 200
    assert await _state(db_session) == CHARGED_AND_GONE


async def test_deleting_its_archive_with_its_statistics(async_client: AsyncClient, db_session, waiting):
    response = await async_client.delete(f"/api/v1/archives/{waiting['archive']}", params={"purge_stats": "true"})
    assert response.status_code == 200, response.text
    assert await _state(db_session) == CHARGED_AND_GONE


async def test_a_run_with_nothing_waiting_is_deleted_as_before(async_client: AsyncClient, db_session, shop):  # noqa: F811
    assert (await async_client.delete("/api/v1/print-log/")).status_code == 200
    assert await _state(db_session) == ([], 0)


async def _charged_and_no_longer_waiting(db_session, run_id: int, archive_id: int) -> None:
    """The charge is written, for the archive it was printed from, and the
    run, which stays, waits for nothing."""
    db_session.expire_all()
    [charge] = (await db_session.scalars(select(WalletTransaction))).all()
    assert (charge.amount, charge.print_run_id, charge.description) == (
        -1.45,
        "run-1",
        "Print charge: Benchy (filament 1.20 + wear 0.25; energy not available)",
    )
    run = await db_session.get(PrintLogEntry, run_id)
    assert run is not None and run.pending_charge is None


async def test_the_purge_deleting_its_archive_with_its_statistics(async_client: AsyncClient, db_session, waiting):
    archive = await db_session.get(PrintArchive, waiting["archive"])
    archive.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    await db_session.commit()

    response = await async_client.post("/api/v1/archives/purge", json={"older_than_days": 365, "purge_stats": True})

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 1
    assert await _state(db_session) == CHARGED_AND_GONE


async def test_deleting_its_printer_with_its_archives(async_client: AsyncClient, db_session, waiting, printer_factory):
    printer = await printer_factory(name="P")
    (await db_session.get(PrintArchive, waiting["archive"])).printer_id = printer.id
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/printers/{printer.id}")

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert await db_session.get(PrintArchive, waiting["archive"]) is None
    await _charged_and_no_longer_waiting(db_session, waiting["run"], waiting["archive"])


async def test_deleting_its_user_with_their_items(async_client: AsyncClient, db_session, waiting):
    response = await async_client.delete(f"/api/v1/users/{waiting['user']}?delete_items=true")

    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert await db_session.get(PrintArchive, waiting["archive"]) is None
    await _charged_and_no_longer_waiting(db_session, waiting["run"], waiting["archive"])


async def test_merging_its_archive_into_another(async_client: AsyncClient, db_session, shop, tmp_path, monkeypatch):  # noqa: F811
    """The run moves to the archive that stays: charged first, for the one
    it was printed from."""
    from backend.app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    first = await db_session.get(PrintArchive, shop["archive"])
    again = PrintArchive(
        filename=first.filename,
        file_path="archive/1/again/benchy.3mf",
        file_size=1,
        print_name="Benchy",
        status="completed",
        cost=7.0,
        created_by_id=shop["user"],
        cost_center_id=first.cost_center_id,
        content_hash="b" * 64,
    )
    first.file_path = "archive/1/first/benchy.3mf"
    first.content_hash = "b" * 64
    db_session.add(again)
    await db_session.flush()
    run = PrintLogEntry(archive_id=again.id, status="completed", billing_run_id="run-2")
    db_session.add(run)
    await db_session.flush()
    defer_print_charge(run, again.id, {"run_entry_id": run.id, "run_status": "completed", "wear_cost": 0.25})
    for folder in ("first", "again"):
        (tmp_path / "archive" / "1" / folder).mkdir(parents=True)
        (tmp_path / "archive" / "1" / folder / "benchy.3mf").write_bytes(b"3mf")
    await db_session.commit()
    again_id, run_id = again.id, run.id

    response = await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [shop["archive"]]})

    assert response.status_code == 200, response.text
    assert response.json()["merged"] == [{"survivor_id": shop["archive"], "merged_ids": [again_id]}]
    db_session.expire_all()
    [charge] = (await db_session.scalars(select(WalletTransaction))).all()
    assert (charge.amount, charge.print_run_id, charge.print_archive_id) == (-7.25, "run-2", again_id)
    run = await db_session.get(PrintLogEntry, run_id)
    assert (run.archive_id, run.pending_charge) == (shop["archive"], None)


async def test_a_merge_whose_charge_fails_half_way_still_merges(
    async_client: AsyncClient,
    db_session,
    shop,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    """The charge fails after writing to its run and is taken back, which
    leaves the merge's copy of the run to be read again: the merge read it
    before, and went on with a run it could no longer read (a 500)."""
    from backend.app.core.config import settings as app_settings
    from backend.app.services import finance_billing

    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    first = await db_session.get(PrintArchive, shop["archive"])
    again = PrintArchive(
        filename=first.filename,
        file_path="archive/1/again/benchy.3mf",
        file_size=1,
        print_name="Benchy",
        status="completed",
        cost=7.0,
        created_by_id=shop["user"],
        cost_center_id=first.cost_center_id,
        content_hash="b" * 64,
    )
    first.file_path = "archive/1/first/benchy.3mf"
    first.content_hash = "b" * 64
    db_session.add(again)
    await db_session.flush()
    run = PrintLogEntry(archive_id=again.id, status="completed", billing_run_id="run-2")
    db_session.add(run)
    await db_session.flush()
    defer_print_charge(run, again.id, {"run_entry_id": run.id, "run_status": "completed"})
    for folder in ("first", "again"):
        (tmp_path / "archive" / "1" / folder).mkdir(parents=True)
        (tmp_path / "archive" / "1" / folder / "benchy.3mf").write_bytes(b"3mf")
    await db_session.commit()
    again_id, run_id = again.id, run.id

    async def fails_half_way(db, archive_id, **charge):
        (await db.get(PrintLogEntry, charge["run_entry_id"])).billing_run_id = "half-written"
        await db.flush()
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", fails_half_way)
    monkeypatch.setattr(finance_billing, "report_print_charge_failed", AsyncMock())

    response = await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [shop["archive"]]})

    assert response.status_code == 200, response.text
    assert response.json()["merged"] == [{"survivor_id": shop["archive"], "merged_ids": [again_id]}]
    db_session.expire_all()
    run = await db_session.get(PrintLogEntry, run_id)
    assert (run.archive_id, run.billing_run_id) == (shop["archive"], "run-2")
    assert run.pending_charge is not None, "still waiting, for the next startup"
