"""Duplicate archives of one file merge into one (archive redesign P2).

Same file, owner and project: the oldest archive stays and takes the others'
runs, references, photos and videos; the others stay soft-deleted with an
alias, their money untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.models.archive import PrintArchive
from backend.app.models.archive_alias import ArchiveAlias
from backend.app.models.finance import WalletTransaction
from backend.app.models.print_log import RUN_OPEN, PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project
from backend.app.models.project_bom import ProjectBOMItem

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

HASH = "a" * 64
T1 = datetime(2026, 9, 1, 9)


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    return tmp_path


def _dir(files, name):
    path = files / "archive" / "1" / name
    (path / "photos").mkdir(parents=True, exist_ok=True)
    (path / "file.3mf").write_bytes(b"3mf")
    return path


@pytest.fixture
async def dupes(db_session, printer_factory, files):
    """X and Y printed once each, Z printed before runs were logged; W is the
    same file in another project, V the same file deleted."""
    printer = await printer_factory(name="A")
    project = Project(name="Other")
    db_session.add(project)
    await db_session.flush()

    def archive(name, **fields):
        folder = _dir(files, name)
        row = PrintArchive(
            printer_id=printer.id,
            filename="part.3mf",
            print_name="part",
            file_path=str((folder / "file.3mf").relative_to(files)),
            file_size=3,
            content_hash=HASH,
            thumbnail_path=f"archive/1/{name}/thumb.png",
            **({"status": "completed"} | fields),
        )
        db_session.add(row)
        return row, folder

    x, x_dir = archive("x", notes="first", tags="red", started_at=T1, completed_at=T1 + timedelta(hours=1))
    y, y_dir = archive(
        "y",
        notes="second",
        tags="blue, red",
        is_favorite=True,
        started_at=T1 + timedelta(days=1),
        completed_at=T1 + timedelta(days=1, hours=1),
        confirm_requested=True,
        confirm_token="tok-y",
        photos=["finish_y.jpg"],
        timelapse_path="archive/1/y/y.mp4",
    )
    z, z_dir = archive(
        "z",
        status="failed",
        failure_reason="spaghetti",
        started_at=T1 + timedelta(days=2),
        completed_at=T1 + timedelta(days=2, hours=1),
        photos=["finish_z.jpg"],
        timelapse_path="archive/1/z/z.mp4",
        filament_used_grams=12.0,
    )
    w, _ = archive("w", project_id=project.id)
    v, _ = archive("v", deleted_at=datetime(2026, 10, 1))
    await db_session.flush()
    (y_dir / "photos" / "finish_y.jpg").write_bytes(b"y")
    (y_dir / "y.mp4").write_bytes(b"yy")
    (z_dir / "photos" / "finish_z.jpg").write_bytes(b"z")
    (z_dir / "z.mp4").write_bytes(b"zz")

    r1 = PrintLogEntry(
        archive_id=x.id, printer_id=printer.id, status="completed", started_at=T1, completed_at=T1 + timedelta(hours=1)
    )
    r2 = PrintLogEntry(
        archive_id=y.id,
        printer_id=printer.id,
        status="completed",
        started_at=T1 + timedelta(days=1),
        completed_at=T1 + timedelta(days=1, hours=1),
        photos=["finish_y.jpg"],
        timelapse_path="archive/1/y/y.mp4",
        thumbnail_path="archive/1/y/thumb.png",
    )
    item = PrintQueueItem(printer_id=printer.id, archive_id=y.id, status="completed", position=1)
    bom = ProjectBOMItem(project_id=project.id, name="part", archive_id=y.id)
    charge = WalletTransaction(transaction_type="print_charge", amount=-1.0, print_archive_id=y.id)
    db_session.add_all([r1, r2, item, bom, charge])
    await db_session.commit()
    return SimpleNamespace(
        x=x.id,
        y=y.id,
        z=z.id,
        w=w.id,
        v=v.id,
        r1=r1.id,
        r2=r2.id,
        item=item.id,
        bom=bom.id,
        charge=charge.id,
        x_dir=x_dir,
        y_dir=y_dir,
        z_dir=z_dir,
        printer=printer.id,
    )


async def _row(db_session, model, row_id):
    db_session.expire_all()
    return await db_session.get(model, row_id)


async def _runs(db_session, archive_id):
    db_session.expire_all()
    return list(
        await db_session.scalars(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id).order_by(PrintLogEntry.started_at)
        )
    )


async def test_the_preview_shows_the_group(async_client, dupes):
    response = await async_client.get("/api/v1/archives/merge-preview")

    assert response.status_code == 200, response.text
    [group] = response.json()["groups"]
    assert group["survivor"]["id"] == dupes.x
    assert [(m["id"], m["run_count"]) for m in group["merged"]] == [(dupes.y, 1), (dupes.z, 0)]
    assert group["skipped"] is None


async def test_merging_keeps_the_oldest_with_every_print(async_client, db_session, dupes):
    response = await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert response.status_code == 200, response.text
    assert response.json() == {"merged": [{"survivor_id": dupes.x, "merged_ids": [dupes.y, dupes.z]}], "skipped": []}

    runs = await _runs(db_session, dupes.x)
    assert [r.status for r in runs] == ["completed", "completed", "failed"]
    from_z = runs[2]
    assert (from_z.failure_reason, from_z.filament_used_grams, from_z.photos) == ("spaghetti", 12.0, ["finish_z.jpg"])
    archive = await _row(db_session, PrintArchive, dupes.x)
    # The file's description, and its latest print.
    assert (archive.notes, archive.tags, archive.is_favorite) == ("first\n\nsecond", "red, blue", True)
    assert (archive.status, archive.failure_reason) == ("failed", "spaghetti")
    assert archive.photos == ["finish_y.jpg", "finish_z.jpg"]


async def test_the_others_stay_deleted_with_their_money(async_client, db_session, dupes):
    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    for merged in (dupes.y, dupes.z):
        assert (await _row(db_session, PrintArchive, merged)).deleted_at is not None
        assert (await db_session.get(ArchiveAlias, merged)).archive_id == dupes.x
    assert (await _row(db_session, WalletTransaction, dupes.charge)).print_archive_id == dupes.y
    assert (await _row(db_session, PrintArchive, dupes.w)).deleted_at is None


async def test_their_references_move(async_client, db_session, dupes):
    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert (await _row(db_session, PrintQueueItem, dupes.item)).archive_id == dupes.x
    assert (await _row(db_session, ProjectBOMItem, dupes.bom)).archive_id == dupes.x


async def test_their_photos_and_videos_move_into_its_folder(async_client, db_session, dupes):
    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert (dupes.x_dir / "photos" / "finish_y.jpg").read_bytes() == b"y"
    assert (dupes.x_dir / "y.mp4").read_bytes() == b"yy"
    r2 = await _row(db_session, PrintLogEntry, dupes.r2)
    assert (r2.timelapse_path, r2.thumbnail_path) == ("archive/1/x/y.mp4", "archive/1/x/thumb.png")
    runs = await _runs(db_session, dupes.x)
    assert runs[2].timelapse_path == "archive/1/x/z.mp4"
    assert (await _row(db_session, PrintArchive, dupes.x)).timelapse_path == "archive/1/x/z.mp4"
    assert not dupes.y_dir.exists() and not dupes.z_dir.exists()


async def test_a_merged_id_finds_the_archive(async_client, dupes):
    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    response = await async_client.get(f"/api/v1/archives/{dupes.y}")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == dupes.x


async def test_its_one_tap_link_still_answers_its_print(async_client, db_session, dupes):
    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    response = await async_client.post("/api/v1/archives/confirm/tok-y/good")

    assert response.status_code == 200, response.text
    assert (await _row(db_session, PrintLogEntry, dupes.r2)).user_verdict == "good"


async def test_a_group_at_work_waits(async_client, db_session, dupes):
    db_session.add(PrintLogEntry(archive_id=dupes.y, printer_id=dupes.printer, status=RUN_OPEN, started_at=T1))
    await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]
    assert group["skipped"] == "busy"
    response = await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})
    assert response.json() == {"merged": [], "skipped": [{"survivor_id": dupes.x, "reason": "busy"}]}
    assert (await _row(db_session, PrintArchive, dupes.y)).deleted_at is None


async def test_a_group_just_printed_waits(async_client, db_session, dupes):
    """Its timelapse and finish photo may still be on their way."""
    from backend.app.utils.local_time import utcnow_naive

    run = await _row(db_session, PrintLogEntry, dupes.r2)
    run.completed_at = utcnow_naive() - timedelta(minutes=5)
    await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]
    assert group["skipped"] == "busy"


async def test_a_queued_group_waits(async_client, db_session, dupes):
    db_session.add(PrintQueueItem(printer_id=dupes.printer, archive_id=dupes.z, status="pending", position=2))
    await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]
    assert group["skipped"] == "busy"


async def test_a_survivor_printed_before_runs_keeps_its_print(async_client, db_session, dupes):
    await db_session.delete(await _row(db_session, PrintLogEntry, dupes.r1))
    await db_session.commit()

    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    runs = await _runs(db_session, dupes.x)
    assert [r.started_at for r in runs] == [T1, T1 + timedelta(days=1), T1 + timedelta(days=2)]


async def test_a_failed_merge_leaves_everything(async_client, db_session, dupes):
    with patch("backend.app.services.print_log.mirror_latest_run", side_effect=RuntimeError("boom")):
        response = await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert response.json() == {"merged": [], "skipped": [{"survivor_id": dupes.x, "reason": "failed"}]}

    assert (await _row(db_session, PrintArchive, dupes.y)).deleted_at is None
    assert (await _row(db_session, PrintLogEntry, dupes.r2)).archive_id == dupes.y
    assert not (dupes.x_dir / "y.mp4").exists()
    assert (dupes.y_dir / "y.mp4").exists()


async def test_merging_needs_delete_all(async_client, db_session, dupes):
    from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

    admin = await _admin_token(async_client)
    jwt, _ = await _user(async_client, admin, "editor", ["archives:read_all", "archives:update_all"])

    response = await async_client.post("/api/v1/archives/merge", headers=_auth(jwt), json={"survivor_ids": [dupes.x]})

    assert response.status_code == 403


async def test_a_scoped_user_merges_only_archives_wholly_theirs(async_client, db_session, printer_factory, dupes):
    from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

    other = await printer_factory(name="B")
    db_session.add(PrintLogEntry(archive_id=dupes.z, printer_id=other.id, status="completed", started_at=T1))
    await db_session.commit()
    admin = await _admin_token(async_client)
    jwt, _ = await _user(
        async_client,
        admin,
        "scoped",
        ["archives:read_all", "archives:update_all", "archives:delete_all"],
        printer_ids=[dupes.printer],
    )

    [group] = (await async_client.get("/api/v1/archives/merge-preview", headers=_auth(jwt))).json()["groups"]

    assert [m["id"] for m in group["merged"]] == [dupes.y]


async def test_a_group_tracked_by_spoolman_waits(async_client, db_session, dupes):
    from backend.app.models.active_print_spoolman import ActivePrintSpoolman

    db_session.add(ActivePrintSpoolman(printer_id=dupes.printer, archive_id=dupes.y, ams_trays={}))
    await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]
    assert group["skipped"] == "busy"


async def test_a_group_printing_waits(async_client, db_session, dupes):
    (await _row(db_session, PrintArchive, dupes.z)).status = "printing"
    await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]
    assert group["skipped"] == "busy"


async def test_an_archive_merged_later_passes_on_its_aliases(db_session, dupes):
    """Y went into X; X later goes into O: Y's id finds O in one step."""
    from backend.app.services.archive_merge import merge_group, resolve_alias

    assert await merge_group(db_session, dupes.x, [dupes.y]) is None
    # W takes X in, as a merge keeping W would (same file, same project now).
    w = await _row(db_session, PrintArchive, dupes.w)
    w.project_id = None
    await db_session.commit()
    assert await merge_group(db_session, dupes.w, [dupes.x]) is None

    assert await resolve_alias(db_session, dupes.y) == dupes.w
    assert await resolve_alias(db_session, dupes.x) == dupes.w


async def test_a_video_only_its_archive_held_goes_with_its_print(async_client, db_session, dupes):
    """Y's print was logged before runs kept their own video: Y alone held it."""
    r2 = await _row(db_session, PrintLogEntry, dupes.r2)
    r2.timelapse_path, r2.photos = None, None
    await db_session.commit()

    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert (await _row(db_session, PrintLogEntry, dupes.r2)).timelapse_path == "archive/1/x/y.mp4"


async def test_the_survivors_own_old_video_stays_with_its_print(async_client, db_session, files, dupes):
    (dupes.x_dir / "x.mp4").write_bytes(b"xx")
    (await _row(db_session, PrintArchive, dupes.x)).timelapse_path = "archive/1/x/x.mp4"
    await db_session.commit()

    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert (await _row(db_session, PrintLogEntry, dupes.r1)).timelapse_path == "archive/1/x/x.mp4"


async def test_the_survivors_own_one_tap_link_answers_its_own_print(async_client, db_session, dupes):
    x = await _row(db_session, PrintArchive, dupes.x)
    x.confirm_requested, x.confirm_token = True, "tok-x"
    await db_session.commit()

    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})
    response = await async_client.post("/api/v1/archives/confirm/tok-x/reject")

    assert response.status_code == 200, response.text
    assert (await _row(db_session, PrintLogEntry, dupes.r1)).user_verdict == "reject"
    assert (await _row(db_session, PrintLogEntry, dupes.r2)).user_verdict is None


async def test_a_folder_shared_with_the_survivor_stays(async_client, db_session, files, dupes):
    """Archived in the same second under the same name: one folder."""
    y = await _row(db_session, PrintArchive, dupes.y)
    y.file_path = "archive/1/x/file.3mf"
    await db_session.commit()

    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert (dupes.x_dir / "file.3mf").exists()
    # Nothing of the survivor's for a later purge of the merged row to remove.
    assert (await _row(db_session, PrintArchive, dupes.y)).file_path == ""


async def test_a_folder_another_archive_uses_stays(async_client, db_session, files, dupes):
    other = PrintArchive(filename="o.3mf", file_path="archive/1/y/other.3mf", file_size=1, created_by_id=None)
    db_session.add(other)
    await db_session.commit()

    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert dupes.y_dir.exists()


async def test_different_source_files_wait_for_the_user(async_client, db_session, files, dupes):
    for archive_id, name, content in ((dupes.x, "x", b"one"), (dupes.y, "y", b"two")):
        (files / "archive" / "1" / name / "src.3mf").write_bytes(content)
        (await _row(db_session, PrintArchive, archive_id)).source_3mf_path = f"archive/1/{name}/src.3mf"
        await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]

    assert group["skipped"] == "conflicting_files"


async def test_the_same_source_file_twice_merges(async_client, db_session, files, dupes):
    for archive_id, name in ((dupes.x, "x"), (dupes.y, "y")):
        (files / "archive" / "1" / name / "src.3mf").write_bytes(b"same")
        (await _row(db_session, PrintArchive, archive_id)).source_3mf_path = f"archive/1/{name}/src.3mf"
        await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]

    assert group["skipped"] is None


@pytest.mark.parametrize("held_by", ["dispatch", "active_print", "pipeline", "timelapse_job"])
async def test_an_archive_held_by_work_in_flight_waits(async_client, db_session, dupes, held_by, monkeypatch):
    from backend.app import main as main_module
    from backend.app.services import archive_work, timelapse_jobs

    if held_by == "dispatch":
        archive_work.hold(dupes.y)
    elif held_by == "active_print":
        monkeypatch.setitem(main_module._active_prints, (dupes.printer, "part.3mf"), dupes.y)
    elif held_by == "pipeline":
        from backend.app.models.pipeline_run import PipelineRun

        db_session.add(PipelineRun(source_archive_id=dupes.y, status="slicing"))
        await db_session.commit()
    else:
        monkeypatch.setitem(timelapse_jobs._jobs, dupes.y, SimpleNamespace(status=timelapse_jobs.RUNNING))

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]

    assert group["skipped"] == "busy"


async def test_a_held_archive_is_left_alone_even_after_the_preview(db_session, dupes):
    """Held between the group search and its merge: a dispatch chose it."""
    from backend.app.services import archive_merge, archive_work

    real_find = archive_merge.find_groups

    async def find_then_hold(db, visible):
        groups = await real_find(db, visible)
        archive_work.hold(dupes.y)
        return groups

    with patch.object(archive_merge, "find_groups", find_then_hold):
        merged, skipped = await archive_merge.merge(db_session, [dupes.x], [])

    assert (merged, skipped) == ([], [{"survivor_id": dupes.x, "reason": "busy"}])


async def test_an_edit_made_after_the_preview_is_kept(db_session, test_engine, dupes):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.services.archive_merge import merge_group

    x = await _row(db_session, PrintArchive, dupes.x)
    assert x.notes == "first"
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as other:
        (await other.get(PrintArchive, dupes.x)).notes = "edited"
        await other.commit()

    assert await merge_group(db_session, dupes.x, [dupes.y, dupes.z]) is None
    assert (await _row(db_session, PrintArchive, dupes.x)).notes == "edited\n\nsecond"


async def test_a_dispatch_leaves_an_archive_being_merged(db_session, dupes):
    from backend.app.services import archive_work

    with archive_work.merging([dupes.y]) as marked:
        assert marked
        with archive_work.merging([dupes.y]) as again:
            assert not again


async def test_a_group_that_got_to_work_after_the_preview_waits(db_session, dupes):
    """Queued between the group search and its merge."""
    from backend.app.services import archive_merge

    real_find = archive_merge.find_groups

    async def find_then_queue(db, visible):
        groups = await real_find(db, visible)
        db.add(PrintQueueItem(printer_id=dupes.printer, archive_id=dupes.z, status="pending", position=3))
        await db.commit()
        return groups

    with patch.object(archive_merge, "find_groups", find_then_queue):
        merged, skipped = await archive_merge.merge(db_session, [dupes.x], [])

    assert (merged, skipped) == ([], [{"survivor_id": dupes.x, "reason": "busy"}])


async def test_a_failing_group_leaves_the_next_to_merge(db_session, files, dupes):
    from backend.app.services import archive_merge

    first = PrintArchive(filename="o.3mf", file_path="archive/1/o1/file.3mf", file_size=1, content_hash="b" * 64)
    second = PrintArchive(filename="o.3mf", file_path="archive/1/o2/file.3mf", file_size=1, content_hash="b" * 64)
    db_session.add_all([first, second])
    await db_session.commit()
    first_id, second_id = first.id, second.id
    real = archive_merge._merge
    calls = []

    async def fail_the_first(db, survivor_id, merged_ids, files_):
        calls.append(survivor_id)
        if len(calls) == 1:
            await db.flush()
            raise RuntimeError("boom")
        return await real(db, survivor_id, merged_ids, files_)

    with patch.object(archive_merge, "_merge", fail_the_first):
        merged, skipped = await archive_merge.merge(db_session, [dupes.x, first_id], [])

    assert skipped == [{"survivor_id": dupes.x, "reason": "failed"}]
    assert merged == [{"survivor_id": first_id, "merged_ids": [second_id]}]


async def test_a_video_attached_by_hand_to_an_unprinted_archive_stays(async_client, db_session, files, dupes):
    """Y was uploaded, never printed, and given a video by hand: the group's
    only video, so the archive keeps it."""
    await db_session.delete(await _row(db_session, PrintLogEntry, dupes.r2))
    await db_session.commit()
    for archive_id in (dupes.y, dupes.z):
        (await _row(db_session, PrintArchive, archive_id)).status = "archived"
        await db_session.commit()
    (await _row(db_session, PrintArchive, dupes.z)).timelapse_path = None
    await db_session.commit()

    response = await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})
    assert response.json()["skipped"] == [], response.text

    assert (await _row(db_session, PrintArchive, dupes.x)).timelapse_path == "archive/1/x/y.mp4"
    assert (dupes.x_dir / "y.mp4").exists()


async def test_a_video_attached_by_hand_beside_another_waits(async_client, db_session, files, dupes):
    """Z's print brings its own video: the archive could show only one."""
    await db_session.delete(await _row(db_session, PrintLogEntry, dupes.r2))
    await db_session.commit()
    (await _row(db_session, PrintArchive, dupes.y)).status = "archived"
    await db_session.commit()

    [group] = (await async_client.get("/api/v1/archives/merge-preview")).json()["groups"]
    response = await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    assert group["skipped"] == "conflicting_files"
    assert response.json()["skipped"] == [{"survivor_id": dupes.x, "reason": "conflicting_files"}]


async def test_the_survivors_lost_file_comes_from_a_duplicate(async_client, db_session, files, dupes):
    (dupes.x_dir / "file.3mf").unlink()

    await async_client.post("/api/v1/archives/merge", json={"survivor_ids": [dupes.x]})

    archive = await _row(db_session, PrintArchive, dupes.x)
    assert (files / archive.file_path).read_bytes() == b"3mf"


async def test_a_copy_that_fails_leaves_no_partial_file(files):
    from backend.app.services.archive_merge import _Files

    source = files / "big.mp4"
    source.write_bytes(b"x" * 100)
    target_dir = files / "target"

    def partial_copy(src, dst):
        Path(dst).write_bytes(b"x")
        raise OSError("disk full")

    with (
        patch("backend.app.services.archive_merge.os.link", side_effect=OSError("cross-device")),
        patch("backend.app.services.archive_merge.shutil.copy2", side_effect=partial_copy),
        pytest.raises(OSError),
    ):
        _Files().place(source, target_dir)

    assert list(target_dir.iterdir()) == []


async def test_an_avi_conversion_holds_its_archive_from_the_spawn(db_session, files, dupes):
    from backend.app.services import archive_work
    from backend.app.services.archive import ArchiveService

    spawned = []
    with (
        patch("backend.app.services.archive.spawn_background_task", side_effect=lambda c, **_: spawned.append(c)),
        patch("backend.app.services.archive._announce_timelapse"),
    ):
        assert await ArchiveService(db_session).attach_timelapse(dupes.x, b"avi", "v.avi")

    assert archive_work.is_held(dupes.x)
    for coro in spawned:
        coro.close()
