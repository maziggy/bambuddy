"""Printing project labels (#3655) on the network label printer (#3612):
``POST /projects/labels/print``, and the roll format in the download and preview."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import labels
from backend.app.models.project import Project
from backend.app.models.settings import Settings
from backend.app.services.label_renderer import render_labels
from backend.app.services.project_label_renderer import draw_project_label
from backend.tests.integration.test_label_printing_api_3612 import (
    ROLL,
    FakeDriver,
    _admin,
    _bearer,
    _user,
    configure,
    patched,
    print_and_wait,
    wait_for_state,
)

URL = "/api/v1/projects/labels"
PRINT = f"{URL}/print"


@pytest.fixture
async def project(db_session: AsyncSession) -> Project:
    p = Project(name="Banana Slugs 香蕉蛞蝓", color="#FFD700")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


def items(project: Project, count: int = 1) -> list[dict]:
    return [{"project_id": project.id, "quantity": 12, "note": "Order 1042"}] * count


@pytest.mark.integration
class TestProjectPrintEndpoint:
    @pytest.mark.asyncio
    async def test_prints_project_labels_on_the_roll(self, async_client: AsyncClient, db_session, project):
        await configure(db_session)
        driver = FakeDriver()
        with patched(driver), patch.object(labels, "render_labels", wraps=render_labels) as render:
            resp = await async_client.post(PRINT, json={"items": items(project, 2), **ROLL})
            assert resp.status_code == 202, resp.text
            assert set(resp.json()) == {"job_id", "total"} and resp.json()["total"] == 2
            job = await wait_for_state(async_client, resp.json()["job_id"], {"done", "failed"})
        assert job == {"job_id": resp.json()["job_id"], "state": "done", "printed": 2, "total": 2, "error": None}
        # The project drawing, in black and white, one page per label.
        data_list = render.call_args.args[1]
        assert [(d.name, d.quantity, d.note) for d in data_list] == [("Banana Slugs 香蕉蛞蝓", 12, "Order 1042")] * 2
        assert render.call_args.kwargs["draw"] is draw_project_label
        assert render.call_args.kwargs["monochrome"] is True
        assert data_list[0].deeplink_url.endswith(f"/projects/{project.id}")
        # Tape width is the image width; the driver takes the pages lazily.
        assert len(driver.sizes) == 2 and all(abs(width - 554) <= 1 for width, _height in driver.sizes)
        assert driver.is_generator
        assert driver.calls[1][0] == "deliver" and driver.calls[1][1].pages == 2

    @pytest.mark.asyncio
    async def test_prints_only_the_chosen_lines(self, async_client: AsyncClient, db_session, project):
        await configure(db_session)
        with patched(FakeDriver()), patch.object(labels, "render_labels", wraps=render_labels) as render:
            job = await print_and_wait(async_client, {"items": items(project), "fields": ["name"], **ROLL}, PRINT)
        assert job["state"] == "done", job
        assert render.call_args.kwargs["fields"] == frozenset({"name"})
        assert render.call_args.args[1][0].deeplink_url == ""  # no QR chosen, no link built

    @pytest.mark.asyncio
    async def test_prints_over_tcp_end_to_end(self, async_client: AsyncClient, db_session, project):
        from backend.tests.unit.test_brother_ql_3612 import FakePrinter, status_block

        printer = FakePrinter(replies=[status_block()], after_job=[status_block(status_type=0x01)])
        await configure(db_session, label_printer_host=f"127.0.0.1:{printer.port}")
        job = await print_and_wait(async_client, {"items": items(project), **ROLL}, PRINT)
        printer.close()
        assert job["state"] == "done", job
        assert printer.received.endswith(b"\x1a")

    @pytest.mark.asyncio
    async def test_no_printer_configured(self, async_client: AsyncClient, project):
        resp = await async_client.post(PRINT, json={"items": items(project), **ROLL})
        assert resp.status_code == 400
        assert "No label printer" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_only_the_roll_on_the_printers_tape_is_printed(self, async_client: AsyncClient, db_session, project):
        await configure(db_session)
        driver = FakeDriver()
        with patched(driver):
            sheet = await async_client.post(PRINT, json={"items": items(project), "template": "avery_l7160"})
            single = await async_client.post(PRINT, json={"items": items(project), "template": "box_62x29"})
            other_tape = await async_client.post(
                PRINT, json={"items": items(project), "template": "roll", "roll": {"tape": "62", "length_mm": 40}}
            )
        assert sheet.status_code == 400
        assert sheet.json()["detail"] == "Sheet templates can't be printed on a label printer"
        assert single.status_code == 400
        assert other_tape.status_code == 400 and "50 mm" in other_tape.json()["detail"]
        assert driver.calls == []

    @pytest.mark.asyncio
    async def test_limits(self, async_client: AsyncClient, db_session, project):
        await configure(db_session)
        # Counting the live pages collects garbage per page: too slow for 100 labels.
        with patched(FakeDriver(track_alive=False)):
            too_many = await async_client.post(PRINT, json={"items": items(project, 101), **ROLL})
            too_long = await async_client.post(
                PRINT, json={"items": items(project, 21), "template": "roll", "roll": {"tape": "50", "length_mm": 200}}
            )
            hundred = await async_client.post(PRINT, json={"items": items(project, 100), **ROLL})
            assert hundred.status_code == 202, hundred.text
            await wait_for_state(async_client, hundred.json()["job_id"], {"done", "failed"}, timeout=60)
        assert too_many.status_code == 422
        assert too_long.status_code == 422 and "4200 mm of tape" in too_long.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "extra",
        [
            {"template": "roll"},  # no roll
            {"template": "box_62x29", "roll": ROLL["roll"]},  # a roll without the roll template
            {**ROLL, "format": "png"},  # the download's options are not a print's
            {**ROLL, "starting_position": 1},
            {**ROLL, "fields": ["brand"]},  # a spool field
        ],
    )
    async def test_rejects_bad_requests(self, async_client: AsyncClient, extra):
        resp = await async_client.post(PRINT, json={"items": [{"project_id": 1}], **extra})
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_unknown_project_is_404_and_prints_nothing(self, async_client: AsyncClient, db_session, project):
        await configure(db_session)
        driver = FakeDriver()
        with patched(driver):
            resp = await async_client.post(PRINT, json={"items": [{"project_id": project.id + 99}], **ROLL})
        assert resp.status_code == 404
        assert driver.calls == []

    @pytest.mark.asyncio
    async def test_roll_download_and_preview(self, async_client: AsyncClient, project):
        """The roll format is a label stock for project labels too: what the
        dialog previews is what the printer prints."""
        download = await async_client.post(URL, json={"items": items(project, 2), **ROLL})
        assert download.status_code == 200, download.text
        assert download.headers["content-type"] == "application/pdf"
        pngs = await async_client.post(URL, json={"items": items(project, 2), **ROLL, "format": "png"})
        assert pngs.headers["content-type"] == "application/zip"
        preview = await async_client.post(f"{URL}/preview", json={**items(project)[0], **ROLL})
        assert preview.status_code == 200, preview.text
        assert preview.headers["content-type"] == "image/png"
        # A roll has no sheet positions.
        refused = await async_client.post(URL, json={"items": items(project), **ROLL, "starting_position": 2})
        assert refused.status_code == 422


@pytest.mark.integration
class TestProjectPrintPermissions:
    @pytest.mark.asyncio
    async def test_print_needs_projects_update(self, async_client: AsyncClient, db_session, project):
        """Printing uses up tape, so read access is not enough, and the spool
        labels' permission is not the projects'; following the job is a read."""
        await configure(db_session)
        admin = await _admin(async_client, "plpadmin")
        reader = _bearer(await _user(async_client, admin, "plpreader", ["projects:read"]))
        updater = _bearer(await _user(async_client, admin, "plpupdater", ["projects:read", "projects:update"]))
        inventory = _bearer(await _user(async_client, admin, "plpinventory", ["inventory:read", "inventory:update"]))
        nobody = _bearer(await _user(async_client, admin, "plpnobody", ["cost_centers:read_own"]))
        body = {"items": items(project), **ROLL}
        driver = FakeDriver()
        with patched(driver):
            assert (await async_client.post(PRINT, json=body, headers=reader)).status_code == 403
            assert (await async_client.post(PRINT, json=body, headers=inventory)).status_code == 403
            assert (await async_client.post(PRINT, json=body, headers=nobody)).status_code == 403
            assert (await async_client.post(PRINT, json=body)).status_code in (401, 403)
            assert driver.calls == []  # a refused print reaches no printer
            ok = await async_client.post(PRINT, json=body, headers=updater)
            assert ok.status_code == 202, ok.text
            # A reader of either kind of label may follow a job; nobody else.
            job_url = f"/api/v1/labels/print-jobs/{ok.json()['job_id']}"
            assert (await async_client.get(job_url, headers=reader)).status_code == 200
            assert (await async_client.get(job_url, headers=inventory)).status_code == 200
            assert (await async_client.get(job_url, headers=nobody)).status_code == 403
            assert (await async_client.get(job_url)).status_code in (401, 403)
            job = await wait_for_state(async_client, ok.json()["job_id"], {"done", "failed"}, reader)
        assert job["state"] == "done", job

    @pytest.mark.asyncio
    async def test_roll_download_and_preview_stay_at_projects_read(
        self, async_client: AsyncClient, db_session, project
    ):
        await configure(db_session)
        admin = await _admin(async_client, "plpadmin2")
        reader = _bearer(await _user(async_client, admin, "plpreader2", ["projects:read"]))
        download = await async_client.post(URL, json={"items": items(project), **ROLL}, headers=reader)
        preview = await async_client.post(f"{URL}/preview", json={**items(project)[0], **ROLL}, headers=reader)
        assert download.status_code == 200, download.text
        assert preview.status_code == 200, preview.text

    @pytest.mark.asyncio
    async def test_api_key_needs_the_projects_scope_to_print(self, async_client: AsyncClient, db_session, project):
        """projects:update is the Manage Projects scope of an API key; a
        read-only key and a Manage Inventory key can't print project labels."""
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        await configure(db_session)
        keys = {}
        for scope in ("can_read_status", "can_manage_inventory", "can_manage_projects"):
            full_key, key_hash, key_prefix = generate_api_key()
            flags = {"can_queue": False, "can_control_printer": False, "can_read_status": False}
            flags.update(can_manage_inventory=False, can_manage_projects=False)
            flags[scope] = True
            db_session.add(APIKey(name=scope, key_hash=key_hash, key_prefix=key_prefix, enabled=True, **flags))
            keys[scope] = {"X-API-Key": full_key}
        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()
        body = {"items": items(project), **ROLL}
        with patched(FakeDriver()):
            assert (await async_client.post(PRINT, json=body, headers=keys["can_read_status"])).status_code == 403
            assert (await async_client.post(PRINT, json=body, headers=keys["can_manage_inventory"])).status_code == 403
            ok = await async_client.post(PRINT, json=body, headers=keys["can_manage_projects"])
            assert ok.status_code == 202, ok.text
            await wait_for_state(async_client, ok.json()["job_id"], {"done", "failed"}, keys["can_read_status"])
