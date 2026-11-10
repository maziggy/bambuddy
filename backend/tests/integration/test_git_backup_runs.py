"""A Git backup keeps each print of an archive (archive redesign P3).

Statistics and the print log count prints, not archives: a backup with
archives only restored them empty. The backup now also writes each print and
which archive went into which, and the restore attaches the prints to their
restored archives, recognises an archive printed again since the backup, and
follows a merged archive to the one it went into.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import delete, func, select

from backend.app.models.archive import PrintArchive
from backend.app.models.archive_alias import ArchiveAlias
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, RUN_OPEN, PrintLogEntry
from backend.app.models.user import User
from backend.app.schemas.github_backup import RestoreCategory
from backend.app.services.github_backup import github_backup_service
from backend.app.services.github_restore import (
    ALIASES_PATH,
    ARCHIVES_PATH,
    RUNS_PATH,
    GitHubRestoreService,
    _CategoryTally,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

T1 = datetime(2026, 10, 1, 9)
T2 = datetime(2026, 10, 5, 9)
T3 = datetime(2026, 10, 9, 9)


async def _backup(db) -> dict:
    files: dict = {}
    await github_backup_service._collect_archives(db, files)
    # As read back out of the repository.
    return json.loads(json.dumps(files))


async def _restore(db, files: dict, overwrite: bool = False) -> _CategoryTally:
    results = await GitHubRestoreService()._apply(db, files, [RestoreCategory.ARCHIVES], overwrite)
    return results[RestoreCategory.ARCHIVES.value]


async def _all_runs(db) -> list[PrintLogEntry]:
    return list(
        (
            await db.execute(
                select(PrintLogEntry).order_by(PrintLogEntry.started_at).execution_options(**{INCLUDE_OPEN_RUNS: True})
            )
        )
        .scalars()
        .all()
    )


async def _wipe(db) -> None:
    """A rebuilt instance: no archives, prints or merges.

    Open runs too: a plain delete of runs leaves them out (_hide_open_runs),
    and one left behind ties with its restored copy on started_at.
    """
    for model in (PrintLogEntry, ArchiveAlias, PrintArchive):
        await db.execute(delete(model).execution_options(**{INCLUDE_OPEN_RUNS: True}))
    await db.commit()


@pytest.fixture
async def printed(db_session, printer_factory):
    """A file printed twice on two plates, with a merged duplicate."""
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    planter = PrintArchive(
        printer_id=b.id,
        filename="planter.3mf",
        file_path="archives/t/planter.3mf",
        file_size=1,
        content_hash="hash-planter",
        print_name="Planter",
        status="failed",
        started_at=T2,
        created_at=T1,
    )
    merged = PrintArchive(
        printer_id=a.id,
        filename="planter.3mf",
        file_path="",
        file_size=1,
        content_hash="hash-planter",
        print_name="Planter",
        status="completed",
        started_at=T1,
        created_at=T2,
        deleted_at=T2,
    )
    db_session.add_all([planter, merged])
    await db_session.flush()
    db_session.add(ArchiveAlias(merged_archive_id=merged.id, archive_id=planter.id))
    db_session.add_all(
        [
            PrintLogEntry(
                archive_id=planter.id,
                printer_id=a.id,
                printer_name="A",
                plate_id=1,
                status="completed",
                started_at=T1,
                filament_used_grams=12.0,
                cost=0.5,
                user_verdict="good",
                user_verdict_source="dialog",
                confirm_token="secret-token",
                created_at=T1,
            ),
            PrintLogEntry(
                archive_id=planter.id,
                printer_id=b.id,
                printer_name="B",
                plate_id=2,
                status="failed",
                started_at=T2,
                failure_reason="spaghetti",
                created_at=T2,
            ),
        ]
    )
    await db_session.commit()
    return {"a": a.id, "b": b.id, "planter": planter.id, "merged": merged.id}


async def test_the_backup_keeps_each_print_and_merge(db_session, printed):
    files = await _backup(db_session)

    runs = files[RUNS_PATH]["runs"]
    assert sorted((r["archive_id"], r["plate_id"], r["status"]) for r in runs) == [
        (printed["planter"], 1, "completed"),
        (printed["planter"], 2, "failed"),
    ]
    assert files[ALIASES_PATH]["aliases"] == [
        {"merged_archive_id": printed["merged"], "archive_id": printed["planter"]}
    ]
    # The one-tap confirm token is a credential.
    assert "secret-token" not in json.dumps(files)


async def test_a_rebuilt_instance_gets_its_prints_back(db_session, printed):
    files = await _backup(db_session)
    await _wipe(db_session)

    tally = await _restore(db_session, files)

    archives = (await db_session.execute(select(PrintArchive))).scalars().all()
    # The merged one is not a row of its own.
    assert [a.print_name for a in archives] == ["Planter"]
    runs = await _all_runs(db_session)
    assert [(r.archive_id, r.plate_id, r.printer_name, r.status, r.user_verdict) for r in runs] == [
        (archives[0].id, 1, "A", "completed", "good"),
        (archives[0].id, 2, "B", "failed", None),
    ]
    assert (runs[0].filament_used_grams, runs[0].cost, runs[0].created_at) == (12.0, 0.5, T1)
    assert runs[0].confirm_token is None
    # 2 archives (1 followed) and 2 prints.
    assert (tally.restored, tally.skipped, tally.failed) == (3, 1, 0)


async def test_the_archive_takes_its_latest_prints_outcome(db_session, printed):
    files = await _backup(db_session)
    await _wipe(db_session)
    # The backup's archive claims the older print's outcome.
    files[ARCHIVES_PATH]["archives"][0].update(status="completed", started_at=str(T1))

    await _restore(db_session, files)

    [archive] = (await db_session.execute(select(PrintArchive))).scalars().all()
    assert (archive.status, archive.started_at, archive.failure_reason) == ("failed", T2, "spaghetti")


async def test_restoring_twice_adds_nothing(db_session, printed):
    files = await _backup(db_session)
    await _wipe(db_session)
    await _restore(db_session, files)

    tally = await _restore(db_session, files)

    assert await db_session.scalar(select(func.count(PrintArchive.id))) == 1
    assert len(await _all_runs(db_session)) == 2
    assert (tally.restored, tally.skipped) == (0, 4)


async def test_an_archive_printed_again_since_the_backup_is_recognised(db_session, printed):
    """Its own start is the newer print's now, so it no longer matches it."""
    files = await _backup(db_session)
    archive = await db_session.get(PrintArchive, printed["planter"])
    archive.started_at = T3
    db_session.add(PrintLogEntry(archive_id=archive.id, printer_id=printed["a"], status="completed", started_at=T3))
    await db_session.commit()

    await _restore(db_session, files)

    assert await db_session.scalar(select(func.count(PrintArchive.id))) == 2  # the planter and its merged one
    assert len(await _all_runs(db_session)) == 3


async def test_what_pointed_at_a_merged_archive_follows_it(db_session, printed):
    files = await _backup(db_session)
    await _wipe(db_session)
    id_map: dict[int, int] = {}

    await GitHubRestoreService()._restore_archives(
        db_session,
        files[ARCHIVES_PATH],
        False,
        _CategoryTally(),
        id_map,
        runs_payload=files[RUNS_PATH],
        aliases_payload=files[ALIASES_PATH],
    )

    assert id_map[printed["merged"]] == id_map[printed["planter"]]


async def test_a_print_running_at_the_backup_comes_back_cancelled(db_session, printed):
    db_session.add(
        PrintLogEntry(archive_id=printed["planter"], printer_id=printed["a"], status=RUN_OPEN, started_at=T3)
    )
    await db_session.commit()
    files = await _backup(db_session)
    await _wipe(db_session)

    await _restore(db_session, files)

    last = (await _all_runs(db_session))[-1]
    assert (last.started_at, last.status, last.failure_reason) == (T3, "cancelled", "noStatusUpdate")


async def test_overwrite_makes_a_print_match_the_backup(db_session, printed):
    files = await _backup(db_session)
    run = (await _all_runs(db_session))[0]
    run.cost, run.user_verdict = 9.0, "reject"
    await db_session.commit()

    await _restore(db_session, files, overwrite=True)

    await db_session.refresh(run)
    assert (run.cost, run.user_verdict) == (0.5, "good")


async def test_overwrite_leaves_a_print_running_now_to_its_print(db_session, printed):
    run = PrintLogEntry(archive_id=printed["planter"], printer_id=printed["a"], status=RUN_OPEN, started_at=T3)
    db_session.add(run)
    await db_session.commit()
    files = await _backup(db_session)
    # Even a backup that says it ended (another instance's, say).
    for entry in files[RUNS_PATH]["runs"]:
        entry["status"] = "completed"

    await _restore(db_session, files, overwrite=True)

    await db_session.refresh(run)
    assert run.status == RUN_OPEN


async def test_an_owner_this_instance_lacks_keeps_the_local_one(db_session, printed):
    owner = User(username="alice", role="operator")
    db_session.add(owner)
    await db_session.flush()
    run = (await _all_runs(db_session))[0]
    run.created_by_id = owner.id
    await db_session.commit()
    files = await _backup(db_session)
    for entry in files[RUNS_PATH]["runs"]:
        entry["owner_username"] = "bob"

    await _restore(db_session, files, overwrite=True)

    await db_session.refresh(run)
    assert run.created_by_id == owner.id


async def test_the_preview_counts_the_prints(db_session, printed):
    files = await _backup(db_session)

    count, detail = await GitHubRestoreService()._count_items(db_session, RestoreCategory.ARCHIVES, files)

    assert (count, detail.code, detail.params) == (4, "archivesRunsCount", {"count": 2})


async def test_the_restore_reads_the_new_files():
    paths = GitHubRestoreService()._category_paths(
        RestoreCategory.ARCHIVES, [ARCHIVES_PATH, RUNS_PATH, ALIASES_PATH, "settings/app_settings.json"]
    )

    assert paths == [ARCHIVES_PATH, RUNS_PATH, ALIASES_PATH]


async def test_a_backup_from_before_a_merge_finds_the_archive_it_went_into(db_session, printer_factory):
    """Not the merged row, which kept its own start but none of its prints."""
    printer = await printer_factory(name="A")
    first, second = (
        PrintArchive(
            printer_id=printer.id,
            filename="hook.3mf",
            file_path="archives/t/hook.3mf",
            file_size=1,
            content_hash="hash-hook",
            status="completed",
            started_at=start,
        )
        for start in (T1, T2)
    )
    db_session.add_all([first, second])
    await db_session.flush()
    db_session.add_all(
        [
            PrintLogEntry(archive_id=first.id, printer_id=printer.id, status="completed", started_at=T1),
            PrintLogEntry(archive_id=second.id, printer_id=printer.id, status="completed", started_at=T2),
        ]
    )
    await db_session.commit()
    files = await _backup(db_session)
    assert files[ALIASES_PATH]["aliases"] == []
    # Merged since: the prints went into the second, the first (found
    # first) is an alias.
    for run in await _all_runs(db_session):
        run.archive_id = second.id
    first.deleted_at = T3
    db_session.add(ArchiveAlias(merged_archive_id=first.id, archive_id=second.id))
    await db_session.commit()

    await _restore(db_session, files)

    runs = await _all_runs(db_session)
    assert [(r.archive_id, r.started_at) for r in runs] == [(second.id, T1), (second.id, T2)]
    assert await db_session.scalar(select(func.count(PrintArchive.id))) == 2


async def _two_of_one_file(db, printer_id, *, second_printed: bool = True):
    first, second = (
        PrintArchive(
            printer_id=printer_id,
            filename="hook.3mf",
            file_path="archives/t/hook.3mf",
            file_size=1,
            content_hash="hash-hook",
            status="completed" if start else "archived",
            started_at=start,
            notes=notes,
            created_at=created,
        )
        for start, notes, created in ((T1, "first", T1), (T2 if second_printed else None, "second", T2))
    )
    db.add_all([first, second])
    await db.flush()
    db.add(PrintLogEntry(archive_id=first.id, printer_id=printer_id, status="completed", started_at=T1))
    if second_printed:
        db.add(PrintLogEntry(archive_id=second.id, printer_id=printer_id, status="completed", started_at=T2))
    await db.commit()
    return first, second


async def _merge_into(db, survivor, merged, notes=None) -> None:
    """As the merge leaves them: prints moved, the merged row an alias."""
    for run in await _all_runs(db):
        run.archive_id = survivor.id
    merged.deleted_at = T3
    if notes:
        survivor.notes = notes
    db.add(ArchiveAlias(merged_archive_id=merged.id, archive_id=survivor.id))
    await db.commit()


async def test_a_never_printed_archive_merged_since_stays_merged(db_session, printer_factory):
    printer = await printer_factory(name="A")
    printed, upload = await _two_of_one_file(db_session, printer.id, second_printed=False)
    files = await _backup(db_session)
    await _merge_into(db_session, printed, upload)

    await _restore(db_session, files)

    live = (await db_session.execute(select(PrintArchive).where(PrintArchive.deleted_at.is_(None)))).scalars().all()
    assert [a.id for a in live] == [printed.id]


async def test_overwrite_leaves_what_a_merge_joined(db_session, printer_factory):
    """Both archives of the backup are the one here now."""
    printer = await printer_factory(name="A")
    first, second = await _two_of_one_file(db_session, printer.id)
    files = await _backup(db_session)
    await _merge_into(db_session, first, second, notes="first\nsecond")

    await _restore(db_session, files, overwrite=True)

    await db_session.refresh(first)
    # The first's own entry is restored over it; the second's goes to it,
    # without writing its fields over the first's.
    assert first.notes == "first"
    assert len(await _all_runs(db_session)) == 2


async def test_an_archive_without_a_start_printed_since_is_recognised(db_session, printer_factory):
    printer = await printer_factory(name="A")
    _, upload = await _two_of_one_file(db_session, printer.id, second_printed=False)
    files = await _backup(db_session)
    upload.started_at = T3
    db_session.add(PrintLogEntry(archive_id=upload.id, printer_id=printer.id, status="completed", started_at=T3))
    await db_session.commit()

    await _restore(db_session, files)

    assert await db_session.scalar(select(func.count(PrintArchive.id))) == 2


async def test_a_print_running_at_the_backup_keeps_its_figures_from_its_end(db_session, printed):
    run = PrintLogEntry(archive_id=printed["planter"], printer_id=printed["a"], status=RUN_OPEN, started_at=T3)
    db_session.add(run)
    await db_session.commit()
    files = await _backup(db_session)
    run.status, run.cost, run.duration_seconds = "completed", 1.2, 3600
    await db_session.commit()

    await _restore(db_session, files, overwrite=True)

    await db_session.refresh(run)
    assert (run.status, run.cost, run.duration_seconds) == ("completed", 1.2, 3600)


async def test_a_deleted_users_prints_go_to_no_one_with_their_name(db_session, printed):
    """The print log keeps the name of who started it; the owner went."""
    run = (await _all_runs(db_session))[0]
    run.created_by_id, run.created_by_username = None, "alice"
    await db_session.commit()
    files = await _backup(db_session)
    await _wipe(db_session)
    db_session.add(User(username="alice", role="operator"))
    await db_session.commit()

    await _restore(db_session, files)

    restored = (await _all_runs(db_session))[0]
    assert (restored.created_by_id, restored.created_by_username) == (None, "alice")


async def test_a_merged_archive_purged_since_is_found_by_its_prints(db_session, printer_factory):
    """Its row is gone, its alias and prints are in the one it went into."""
    printer = await printer_factory(name="A")
    first, second = await _two_of_one_file(db_session, printer.id)
    files = await _backup(db_session)
    await _merge_into(db_session, first, second, notes="first\nsecond")
    await db_session.delete(second)
    await db_session.commit()

    await _restore(db_session, files, overwrite=True)

    await db_session.refresh(first)
    assert await db_session.scalar(select(func.count(PrintArchive.id))) == 1
    assert first.notes == "first"


async def test_a_backup_from_before_a_merge_into_the_newer_overwrites_only_it(db_session, printer_factory):
    printer = await printer_factory(name="A")
    first, second = await _two_of_one_file(db_session, printer.id)
    files = await _backup(db_session)
    await _merge_into(db_session, second, first, notes="second\nfirst")

    await _restore(db_session, files, overwrite=True)

    await db_session.refresh(second)
    assert second.notes == "second"
