"""What the archive page shows of each print (archive redesign P4).

The page lists an archive's prints with what is each print's own: its note,
its finish photos and its video. Plates of one archive keep their own videos,
so each print's video has a route of its own; a caller limited to some
printers gets only their prints' media.
"""

from __future__ import annotations

import pytest

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.tests.integration.test_archive_scope_per_run import DIR, _url, files, plates  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _runs(async_client, plates, headers) -> dict[int, dict]:
    response = await async_client.get(_url(plates, "/runs"), headers=headers)
    assert response.status_code == 200, response.text
    return {r["id"]: r for r in response.json()["items"]}


async def test_each_print_carries_its_own_media_and_note(async_client, db_session, plates):
    first = await db_session.get(PrintLogEntry, plates.first)
    first.notes = "brim lifted"
    await db_session.commit()

    runs = await _runs(async_client, plates, plates.admin)

    assert (runs[plates.first]["photos"], runs[plates.first]["timelapse_path"], runs[plates.first]["notes"]) == (
        ["finish_a.jpg"],
        f"{DIR}/a.mp4",
        "brim lifted",
    )
    assert runs[plates.second]["photos"] == ["finish_b.jpg"]


async def test_a_photo_removed_from_the_archive_is_no_prints_any_more(async_client, db_session, plates):
    archive = await db_session.get(PrintArchive, plates.archive)
    archive.photos = ["finish_b.jpg", "0a1b2c3d.jpg"]
    await db_session.commit()

    runs = await _runs(async_client, plates, plates.admin)

    assert runs[plates.first]["photos"] == []
    assert runs[plates.second]["photos"] == ["finish_b.jpg"]


async def test_a_print_without_a_photo_list_of_its_own_says_so(async_client, db_session, plates):
    # Logged before prints kept their own: null, so the page shows the
    # archive's photos for it; a print with an empty list of its own: [].
    first = await db_session.get(PrintLogEntry, plates.first)
    first.photos = None
    second = await db_session.get(PrintLogEntry, plates.second)
    second.photos = []
    await db_session.commit()

    runs = await _runs(async_client, plates, plates.admin)

    assert runs[plates.first]["photos"] is None
    assert runs[plates.second]["photos"] == []


async def test_each_print_plays_its_own_video(async_client, files, plates):
    (files / DIR / "a.mp4").write_bytes(b"plate one")
    (files / DIR / "b.mp4").write_bytes(b"plate two")

    first = await async_client.get(_url(plates, f"/runs/{plates.first}/timelapse"), headers=plates.admin)
    second = await async_client.get(_url(plates, f"/runs/{plates.second}/timelapse"), headers=plates.admin)

    assert (first.status_code, first.content) == (200, b"plate one")
    assert (second.status_code, second.content) == (200, b"plate two")


async def test_a_print_outside_the_callers_printers_plays_nothing(async_client, plates):
    assert (
        await async_client.get(_url(plates, f"/runs/{plates.first}/timelapse"), headers=plates.of_a)
    ).status_code == 200
    assert (
        await async_client.get(_url(plates, f"/runs/{plates.second}/timelapse"), headers=plates.of_a)
    ).status_code == 404


async def test_a_print_of_another_archive_plays_nothing(async_client, db_session, plates):
    other = PrintArchive(filename="o.3mf", file_path=f"{DIR}/o.3mf", file_size=1, print_name="o")
    db_session.add(other)
    await db_session.commit()

    response = await async_client.get(
        f"/api/v1/archives/{other.id}/runs/{plates.first}/timelapse", headers=plates.admin
    )

    assert response.status_code == 404


async def test_a_video_outside_the_data_dir_is_not_served(async_client, db_session, files, plates):
    outside = files.parent / "secret.mp4"
    outside.write_bytes(b"no")
    first = await db_session.get(PrintLogEntry, plates.first)
    first.timelapse_path = "../secret.mp4"
    await db_session.commit()

    response = await async_client.get(_url(plates, f"/runs/{plates.first}/timelapse"), headers=plates.admin)

    assert response.status_code == 404


async def test_a_print_gets_its_own_note(async_client, db_session, plates):
    response = await async_client.patch(
        f"/api/v1/print-log/{plates.first}", json={"notes": "  brim lifted  "}, headers=plates.admin
    )
    assert response.status_code == 200, response.text
    assert response.json()["notes"] == "brim lifted"

    cleared = await async_client.patch(f"/api/v1/print-log/{plates.first}", json={"notes": ""}, headers=plates.admin)
    assert cleared.json()["notes"] is None
    # The file's note is the archive's own, untouched.
    archive = await db_session.get(PrintArchive, plates.archive)
    await db_session.refresh(archive)
    assert archive.notes is None


async def test_a_print_outside_the_callers_printers_takes_no_note(async_client, plates):
    response = await async_client.patch(f"/api/v1/print-log/{plates.second}", json={"notes": "x"}, headers=plates.of_a)

    assert response.status_code == 404


async def test_the_print_log_names_the_deleted_archives_of_its_prints(async_client, db_session, plates):
    archive = await db_session.get(PrintArchive, plates.archive)
    archive.deleted_at = archive.created_at
    await db_session.commit()

    response = await async_client.get("/api/v1/print-log/", headers=plates.admin)

    assert response.json()["deleted_archive_ids"] == [plates.archive]
