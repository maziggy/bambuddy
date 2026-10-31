"""A caller limited to some printers sees an archive by its prints (#1727, archive redesign).

Plate 1 of one archive printed on printer A, then plate 2 on B: the archive
names B. A user of A sees the archive by plate 1 and only plate 1 of it: its
outcome, video and finish photos. What changes the whole archive is refused
to them, as it would change B's print too.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import RUN_OPEN, PrintLogEntry
from backend.tests.integration.test_archives_last_per_printer import _admin_token, _auth, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

T1 = datetime(2026, 10, 30, 9)
T2 = T1 + timedelta(hours=1)
DIR = "archives/scoped"
PERMISSIONS = ["archives:read_all", "archives:update_all", "archives:delete_all", "printers:read", "projects:read"]


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    (tmp_path / DIR / "photos").mkdir(parents=True)
    for name in ("a.mp4", "b.mp4", "photos/finish_a.jpg", "photos/finish_b.jpg", "photos/0a1b2c3d.jpg"):
        (tmp_path / DIR / name).write_bytes(b"x")
    return tmp_path


@pytest.fixture
async def plates(async_client, db_session, printer_factory, files):
    """Plate 1 completed on A with its video and photo, then plate 2 failed
    on B with its own; one photo uploaded by hand. Users scoped to A, to B
    and to a printer C that printed nothing."""
    a, b, c = await printer_factory(name="A"), await printer_factory(name="B"), await printer_factory(name="C")
    archive = PrintArchive(
        printer_id=b.id,
        filename="two.gcode.3mf",
        file_path=f"{DIR}/two.gcode.3mf",
        file_size=1,
        print_name="two",
        status="failed",
        started_at=T2,
        timelapse_path=f"{DIR}/b.mp4",
        photos=["finish_a.jpg", "finish_b.jpg", "0a1b2c3d.jpg"],
    )
    db_session.add(archive)
    await db_session.flush()
    first = PrintLogEntry(
        archive_id=archive.id,
        printer_id=a.id,
        plate_id=1,
        status="completed",
        started_at=T1,
        photos=["finish_a.jpg"],
        timelapse_path=f"{DIR}/a.mp4",
    )
    second = PrintLogEntry(
        archive_id=archive.id,
        printer_id=b.id,
        plate_id=2,
        status="failed",
        started_at=T2,
        photos=["finish_b.jpg"],
        timelapse_path=f"{DIR}/b.mp4",
    )
    db_session.add_all([first, second])
    await db_session.commit()
    admin = await _admin_token(async_client)
    users = {}
    for name, printer in (("of_a", a), ("of_b", b), ("of_c", c)):
        jwt, _ = await _user(async_client, admin, name, PERMISSIONS, printer_ids=[printer.id])
        users[name] = _auth(jwt)
    return SimpleNamespace(
        archive=archive.id, a=a.id, b=b.id, c=c.id, first=first.id, second=second.id, admin=_auth(admin), **users
    )


def _url(plates, path=""):
    return f"/api/v1/archives/{plates.archive}{path}"


async def _row(db_session, model, row_id):
    db_session.expire_all()
    return await db_session.get(model, row_id)


class TestSeeing:
    async def test_a_user_of_a_sees_the_archive_by_its_plate(self, async_client, plates):
        listed = (await async_client.get("/api/v1/archives/", headers=plates.of_a)).json()

        assert [a["id"] for a in listed] == [plates.archive]
        shown = listed[0]
        assert (shown["printer_id"], shown["status"], shown["run_id"]) == (plates.a, "completed", plates.first)
        assert shown["timelapse_path"] == f"{DIR}/a.mp4"
        assert shown["photos"] == ["finish_a.jpg"]
        assert shown["run_count"] == 1

    async def test_the_archive_page_shows_their_plate(self, async_client, plates):
        response = await async_client.get(_url(plates), headers=plates.of_a)

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["printer_id"], body["status"], body["run_id"]) == (plates.a, "completed", plates.first)

    async def test_the_other_plate_is_not_theirs(self, async_client, plates):
        response = await async_client.get(_url(plates, f"?run_id={plates.second}"), headers=plates.of_a)

        assert response.status_code == 404

    async def test_its_print_log_lists_their_plate_only(self, async_client, plates):
        runs = (await async_client.get(_url(plates, "/runs"), headers=plates.of_a)).json()

        assert [r["id"] for r in runs["items"]] == [plates.first]

    async def test_a_user_of_b_sees_its_own_and_the_uploads(self, async_client, plates):
        body = (await async_client.get(_url(plates), headers=plates.of_b)).json()

        assert (body["printer_id"], body["status"]) == (plates.b, "failed")
        assert body["timelapse_path"] == f"{DIR}/b.mp4"
        assert body["photos"] == ["finish_b.jpg", "0a1b2c3d.jpg"]

    async def test_a_printer_it_never_printed_on_sees_nothing(self, async_client, plates):
        assert (await async_client.get("/api/v1/archives/", headers=plates.of_c)).json() == []
        assert (await async_client.get(_url(plates), headers=plates.of_c)).status_code == 404

    async def test_a_plate_printing_now_counts(self, async_client, db_session, plates):
        db_session.add(
            PrintLogEntry(
                archive_id=plates.archive, printer_id=plates.c, status=RUN_OPEN, started_at=T2 + timedelta(hours=1)
            )
        )
        await db_session.commit()

        listed = (await async_client.get("/api/v1/archives/", headers=plates.of_c)).json()
        assert [a["id"] for a in listed] == [plates.archive]
        assert (await async_client.get(_url(plates), headers=plates.of_c)).status_code == 200

    async def test_search_finds_it_by_their_plate(self, async_client, plates):
        response = await async_client.get("/api/v1/archives/search?q=two", headers=plates.of_a)

        assert response.status_code == 200, response.text
        assert [a["id"] for a in response.json()] == [plates.archive]
        assert response.json()[0]["photos"] == ["finish_a.jpg"]

    async def test_the_admin_sees_it_whole(self, async_client, plates):
        body = (await async_client.get(_url(plates), headers=plates.admin)).json()

        assert (body["printer_id"], body["run_count"]) == (plates.b, 2)
        assert body["photos"] == ["finish_a.jpg", "finish_b.jpg", "0a1b2c3d.jpg"]


class TestMedia:
    async def test_the_other_plates_video_is_not_theirs(self, async_client, plates):
        assert (await async_client.get(_url(plates, "/timelapse"), headers=plates.of_a)).status_code == 404
        assert (await async_client.get(_url(plates, "/timelapse"), headers=plates.of_b)).status_code == 200

    async def test_the_other_plates_photo_is_not_theirs(self, async_client, plates):
        assert (await async_client.get(_url(plates, "/photos/finish_b.jpg"), headers=plates.of_a)).status_code == 404
        assert (await async_client.get(_url(plates, "/photos/finish_a.jpg"), headers=plates.of_a)).status_code == 200
        assert (await async_client.get(_url(plates, "/photos/0a1b2c3d.jpg"), headers=plates.of_a)).status_code == 200

    async def test_nor_theirs_to_delete(self, async_client, db_session, plates):
        response = await async_client.delete(_url(plates, "/photos/finish_b.jpg"), headers=plates.of_a)

        assert response.status_code == 404
        assert "finish_b.jpg" in (await _row(db_session, PrintArchive, plates.archive)).photos

    async def test_a_finish_photo_on_no_runs_list_is_the_archives_latest(self, async_client, db_session, plates):
        """Logged before runs kept their own photos: the archive's, here B's."""
        archive = await _row(db_session, PrintArchive, plates.archive)
        archive.photos = [*archive.photos, "finish_old.jpg"]
        await db_session.commit()

        assert "finish_old.jpg" not in (await async_client.get(_url(plates), headers=plates.of_a)).json()["photos"]
        assert "finish_old.jpg" in (await async_client.get(_url(plates), headers=plates.of_b)).json()["photos"]

    async def test_a_plate_logged_before_runs_kept_media_shows_none_of_the_others(
        self, async_client, db_session, files, plates
    ):
        """Its run has no video or photo list of its own: the archive's are
        B's latest, and only the photo uploaded by hand is everyone's."""
        first = await _row(db_session, PrintLogEntry, plates.first)
        first.photos, first.timelapse_path = None, None
        await db_session.commit()

        body = (await async_client.get(_url(plates), headers=plates.of_a)).json()
        assert (body["run_id"], body["timelapse_path"]) == (plates.first, None)
        assert body["photos"] == ["0a1b2c3d.jpg"]
        assert (await async_client.get(_url(plates, "/photos/finish_a.jpg"), headers=plates.of_a)).status_code == 404


class TestWholeArchive:
    async def test_deleting_it_is_refused(self, async_client, db_session, plates):
        response = await async_client.delete(_url(plates), headers=plates.of_a)

        assert response.status_code == 403, response.text
        assert (await _row(db_session, PrintArchive, plates.archive)).deleted_at is None

    async def test_also_to_the_user_of_its_own_printer(self, async_client, plates):
        """B is the archive's printer; plate 1 printed on A."""
        assert (await async_client.delete(_url(plates), headers=plates.of_b)).status_code == 403

    async def test_deleting_its_video_is_refused(self, async_client, db_session, plates):
        response = await async_client.delete(_url(plates, "/timelapse"), headers=plates.of_b)

        assert response.status_code == 403
        assert (await _row(db_session, PrintArchive, plates.archive)).timelapse_path == f"{DIR}/b.mp4"

    async def test_its_notes_are_theirs_to_edit(self, async_client, db_session, plates):
        response = await async_client.patch(_url(plates), headers=plates.of_a, json={"notes": "nice", "tags": "x"})

        assert response.status_code == 200, response.text
        assert (await _row(db_session, PrintArchive, plates.archive)).notes == "nice"

    async def test_its_cost_is_not(self, async_client, db_session, plates):
        response = await async_client.patch(_url(plates), headers=plates.of_a, json={"cost": 9.0})

        assert response.status_code == 403
        assert (await _row(db_session, PrintArchive, plates.archive)).cost is None

    async def test_nor_its_outcome_without_their_run(self, async_client, db_session, plates):
        response = await async_client.patch(_url(plates), headers=plates.of_a, json={"user_verdict": "good"})

        assert response.status_code == 403

    async def test_their_plates_outcome_is(self, async_client, db_session, plates):
        response = await async_client.patch(
            _url(plates), headers=plates.of_a, json={"user_verdict": "good", "run_id": plates.first}
        )

        assert response.status_code == 200, response.text
        assert (await _row(db_session, PrintLogEntry, plates.first)).user_verdict == "good"

    async def test_the_admin_may_delete_it(self, async_client, db_session, plates):
        response = await async_client.delete(_url(plates), headers=plates.admin)

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            ("post", "/timelapse/select?filename=v.mp4", {}),
            ("post", "/timelapse/upload", {"files": {"file": ("v.mp4", b"x", "video/mp4")}}),
            ("post", "/timelapse/process", {"data": {"speed": "2"}}),
            ("delete", "/timelapse/job", {}),
            ("post", "/source", {"files": {"file": ("s.3mf", b"x", "application/octet-stream")}}),
            ("delete", "/source", {}),
            ("post", "/f3d", {"files": {"file": ("d.f3d", b"x", "application/octet-stream")}}),
            ("delete", "/f3d", {}),
        ],
    )
    async def test_its_file_and_video_are_not_theirs_to_change(self, async_client, plates, method, path, kwargs):
        """B's user: the archive's own printer, but plate 1 printed on A."""
        response = await getattr(async_client, method)(_url(plates, path), headers=plates.of_b, **kwargs)

        assert response.status_code == 403, response.text

    async def test_scanning_for_its_video_is_not_theirs(self, async_client, db_session, plates):
        """A scan attaches a video to the archive: only without one."""
        archive = await _row(db_session, PrintArchive, plates.archive)
        archive.timelapse_path = None
        await db_session.commit()

        response = await async_client.post(_url(plates, "/timelapse/scan"), headers=plates.of_b)

        assert response.status_code == 403, response.text


class TestTheOtherPlatesVideo:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/timelapse/info"),
            ("get", "/timelapse/thumbnails"),
            ("post", "/media-download-token"),
        ],
    )
    async def test_is_missing_for_them(self, async_client, plates, method, path):
        response = await getattr(async_client, method)(_url(plates, path), headers=plates.of_a)

        assert response.status_code == 404, response.text

    async def test_is_not_offered_as_attached(self, async_client, plates):
        response = await async_client.get(_url(plates, "/printer-media"), headers=plates.of_a)

        assert response.status_code == 200, response.text
        assert response.json()["local_timelapse"] is None


class TestReviewFixes:
    async def test_a_null_run_id_is_no_run(self, async_client, db_session, plates):
        response = await async_client.patch(
            _url(plates), headers=plates.of_a, json={"run_id": None, "status": "completed", "failure_reason": "x"}
        )

        assert response.status_code == 403, response.text
        assert (await _row(db_session, PrintArchive, plates.archive)).status == "failed"

    async def test_its_project_page_is_its_file(self, async_client, plates):
        response = await async_client.patch(_url(plates, "/project-page"), headers=plates.of_a, json={"title": "x"})

        assert response.status_code == 403, response.text

    async def test_compare_and_similar_leave_it_out(self, async_client, db_session, plates):
        """Both show each archive's latest print as it is: B's."""
        other = PrintArchive(
            printer_id=plates.a, filename="two.gcode.3mf", file_path=f"{DIR}/o.3mf", file_size=1, print_name="two"
        )
        db_session.add(other)
        await db_session.commit()
        other_id = other.id

        compare = await async_client.get(
            f"/api/v1/archives/compare?archive_ids={plates.archive},{other_id}", headers=plates.of_a
        )
        assert compare.status_code == 404, compare.text
        similar = (await async_client.get(f"/api/v1/archives/{other_id}/similar", headers=plates.of_a)).json()
        assert plates.archive not in [s["archive"]["id"] for s in similar]

    async def test_the_project_previews_leave_it_out(self, async_client, db_session, plates):
        from backend.app.models.project import Project

        project = Project(name="P")
        db_session.add(project)
        await db_session.flush()
        (await db_session.get(PrintArchive, plates.archive)).project_id = project.id
        await db_session.commit()

        projects = (await async_client.get("/api/v1/projects/", headers=plates.of_a)).json()
        assert [p.get("archives", []) for p in projects] == [[]]

    async def test_its_photo_responses_show_theirs(self, async_client, plates):
        uploaded = await async_client.post(
            _url(plates, "/photos"), headers=plates.of_a, files={"file": ("p.jpg", b"\xff\xd8\xff", "image/jpeg")}
        )

        assert uploaded.status_code == 200, uploaded.text
        assert "finish_b.jpg" not in uploaded.json()["photos"]
        deleted = await async_client.delete(_url(plates, "/photos/0a1b2c3d.jpg"), headers=plates.of_a)
        assert deleted.status_code == 200, deleted.text
        assert "finish_b.jpg" not in deleted.json()["photos"]

    async def test_printer_media_does_not_name_the_other_printer(self, async_client, plates):
        response = await async_client.get(_url(plates, "/printer-media"), headers=plates.of_a)

        assert response.json()["printer_id"] is None

    async def test_its_energy_and_time_are_their_prints(self, async_client, db_session, plates):
        archive = await _row(db_session, PrintArchive, plates.archive)
        archive.energy_kwh, archive.wear_cost = 5.0, 2.0
        archive.status, archive.completed_at, archive.print_time_seconds = "completed", T2 + timedelta(hours=1), 3600
        first = await db_session.get(PrintLogEntry, plates.first)
        first.energy_kwh, first.wear_cost = 1.0, 0.5
        await db_session.commit()

        body = (await async_client.get(_url(plates), headers=plates.of_a)).json()
        assert (body["energy_kwh"], body["wear_cost"]) == (1.0, 0.5)
        assert body["actual_time_seconds"] is None
        whole = (await async_client.get(_url(plates), headers=plates.admin)).json()
        assert (whole["energy_kwh"], whole["wear_cost"]) == (5.0, 2.0)


async def test_queueing_it_is_theirs(db_session, plates, test_engine):
    """It prints on their printer (archive redesign)."""
    from unittest.mock import patch

    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.api.routes.print_queue import _assert_can_queue_archive
    from backend.app.core.printer_scope import PrinterScope

    archive = await db_session.get(PrintArchive, plates.archive)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("backend.app.core.database.async_session", maker):
        await _assert_can_queue_archive(archive, None, PrinterScope(frozenset({plates.a})))
        with pytest.raises(HTTPException) as refused:
            await _assert_can_queue_archive(archive, None, PrinterScope(frozenset({plates.c})))
    assert refused.value.status_code == 404


class TestLiveUpdates:
    async def test_reach_the_printers_its_prints_ran_on(self, db_session, plates, test_engine):
        from unittest.mock import patch

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from backend.app.core import websocket

        db_session.add(
            PrintLogEntry(
                archive_id=plates.archive, printer_id=plates.c, status=RUN_OPEN, started_at=T2 + timedelta(hours=1)
            )
        )
        await db_session.commit()
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("backend.app.core.database.async_session", maker):
            assert await websocket._archive_printer_ids(plates.archive) == (plates.b, {plates.a, plates.b, plates.c})


async def test_the_project_list_shows_their_plate(async_client, db_session, plates):
    from backend.app.models.project import Project

    project = Project(name="P")
    db_session.add(project)
    await db_session.flush()
    archive = await db_session.get(PrintArchive, plates.archive)
    archive.project_id = project.id
    project_id = project.id
    await db_session.commit()

    response = await async_client.get(f"/api/v1/projects/{project_id}/archives", headers=plates.of_a)

    assert response.status_code == 200, response.text
    assert [(a["id"], a["printer_id"], a["photos"]) for a in response.json()] == [
        (plates.archive, plates.a, ["finish_a.jpg"])
    ]
