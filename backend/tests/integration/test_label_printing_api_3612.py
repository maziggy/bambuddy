"""Printing spool labels on a network label printer (#3612): endpoints, settings, ui-flags."""

from __future__ import annotations

import asyncio
import gc
import inspect
import secrets
import threading
import time
import weakref
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.services.label_printers.base import (
    PreparedJob,
    PrinterDidNotFinish,
    PrinterReportedError,
    PrinterStatus,
    PrinterUnreachable,
    WrongMedia,
)

_PW = "Aa1!" + secrets.token_urlsafe(12)  # pragma: allowlist secret
ROLL = {"template": "roll", "roll": {"tape": "50", "length_mm": 40}}


class FakeDriver:
    """Takes the pages like the real driver: one at a time, dropping each."""

    def __init__(
        self,
        error: Exception | None = None,
        *,
        delay: float = 0.0,
        timeout: float = 30.0,
        gate: threading.Event | None = None,
        confirm: int | None = None,
        track_alive: bool = True,
    ):
        self.error = error
        self.delay = delay
        self.timeout = timeout
        self.gate = gate  # when set, deliver waits for it
        self.confirm = confirm  # labels reported as confirmed (through on_progress) before the end; None = silent
        self.track_alive = track_alive  # gc.collect per page is slow: off for the 100-label test
        self.lock_timeouts: list[float | None] = []
        self.order: list[str] = []  # hosts, in the order the deliveries started
        self.abandoned = threading.Event()
        self.calls: list[tuple] = []
        self.sizes: list[tuple[int, int]] = []
        self.max_alive = 0
        self.active = self.max_active = 0
        self.is_generator = False
        self._lock = threading.Lock()

    def prepare(self, images, config, *, cut=True):
        self.is_generator = inspect.isgenerator(images)
        refs, pages = [], 0
        iterator = iter(images)
        while (image := next(iterator, None)) is not None:
            refs.append(weakref.ref(image))
            if self.track_alive:
                gc.collect()
                self.max_alive = max(self.max_alive, sum(r() is not None for r in refs))
            self.sizes.append(image.size)
            pages += 1
            del image
        self.calls.append(("prepare", config, cut))
        return PreparedJob(b"job", pages, 10 * pages)

    def test_job(self, config):
        self.test_thread = threading.current_thread()
        self.calls.append(("test", config))
        return PreparedJob(b"test", 1, 10)

    def deliver(self, job, config, abandon=None, on_progress=None, lock_timeout=None):
        self.calls.append(("deliver", job, config))
        self.lock_timeouts.append(lock_timeout)
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.order.append(config.host)
        try:
            if self.gate is not None:
                self.gate.wait(10)
            if on_progress is not None:
                for n in range(1, (self.confirm or 0) + 1):
                    on_progress(n)
            time.sleep(self.delay)
            if abandon is not None and abandon.is_set():
                self.abandoned.set()
            if self.error:
                raise self.error
            return PrinterStatus(50, "continuous", [])
        finally:
            with self._lock:
                self.active -= 1

    def timeout_s(self, job):
        return self.timeout

    def max_timeout_s(self, job):
        return self.timeout


@pytest.fixture
async def spool(db_session: AsyncSession):
    s = Spool(material="PLA", brand="Polymaker", color_name="Red", rgba="FF0000FF", label_weight=1000, weight_used=0)
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)
    return s


async def make_spools(db_session: AsyncSession, n: int) -> list[int]:
    spools = [Spool(material="PLA", brand="B", label_weight=1000, weight_used=0) for _ in range(n)]
    db_session.add_all(spools)
    await db_session.commit()
    return [sp.id for sp in spools]


async def configure(db_session: AsyncSession, **overrides):
    values = {
        "label_printer_type": "brother_ql",
        "label_printer_host": "192.168.1.50:9101",
        "label_printer_model": "QL-820NWB",
        "label_printer_tape": "50",
    } | overrides
    for key, value in values.items():
        db_session.add(Settings(key=key, value=value))
    await db_session.commit()


def patched(driver):
    return patch("backend.app.api.routes.labels.get_driver", return_value=driver)


PRINT = "/api/v1/inventory/labels/print"


async def wait_for_state(async_client: AsyncClient, job_id: str, states: set[str], headers=None, timeout=10.0):
    """Poll the job until it is in one of ``states``; returns the last body."""
    deadline = time.monotonic() + timeout
    while True:
        resp = await async_client.get(f"/api/v1/labels/print-jobs/{job_id}", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["state"] in states:
            return body
        assert time.monotonic() < deadline, f"job stuck in {body}"
        await asyncio.sleep(0.02)


async def print_and_wait(async_client: AsyncClient, body: dict, url: str = PRINT, timeout: float = 10.0):
    resp = await async_client.post(url, json=body)
    assert resp.status_code == 202, resp.text
    return await wait_for_state(async_client, resp.json()["job_id"], {"done", "failed"}, timeout=timeout)


async def set_host(db_session: AsyncSession, host: str):
    await db_session.execute(update(Settings).where(Settings.key == "label_printer_host").values(value=host))
    await db_session.commit()


@pytest.mark.integration
class TestPrintEndpoint:
    @pytest.mark.asyncio
    async def test_prints_roll_labels(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        driver = FakeDriver()
        with patched(driver):
            resp = await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})
            assert resp.status_code == 202, resp.text
            assert set(resp.json()) == {"job_id", "total"} and resp.json()["total"] == 1
            assert len(resp.json()["job_id"]) == 32
            job = await wait_for_state(async_client, resp.json()["job_id"], {"done", "failed"})
        assert job == {"job_id": resp.json()["job_id"], "state": "done", "printed": 1, "total": 1, "error": None}
        _, config, _ = driver.calls[0]
        # Tape width is the image width after the quarter turn.
        width, height = driver.sizes[0]
        assert abs(width - 554) <= 1
        assert height > width // 20
        assert (config.host, config.port, config.tape) == ("192.168.1.50", 9101, "50")
        assert driver.calls[1][0] == "deliver" and driver.calls[1][1].pages == 1

    @pytest.mark.asyncio
    async def test_pages_are_taken_lazily_one_at_a_time(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        spools = [Spool(material="PLA", brand="B", label_weight=1000, weight_used=0) for _ in range(6)]
        db_session.add_all(spools)
        await db_session.commit()
        driver = FakeDriver()
        with patched(driver):
            job = await print_and_wait(async_client, {"spool_ids": [sp.id for sp in spools], **ROLL})
        assert job["state"] == "done" and job["printed"] == 6
        assert driver.is_generator  # not a list of pre-rendered images
        assert len(driver.sizes) == 6
        assert driver.max_alive == 1  # a page image was gone before the next one was made

    @pytest.mark.asyncio
    async def test_tape_cap_is_422(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        body = {"spool_ids": list(range(1, 22)), "template": "roll", "roll": {"tape": "50", "length_mm": 200}}
        with patched(FakeDriver()):
            resp = await async_client.post("/api/v1/inventory/labels/print", json=body)
        assert resp.status_code == 422
        assert "4200 mm" in resp.json()["detail"] and "4000 mm" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_hundred_40mm_labels_are_within_the_cap(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        body = {
            "spool_ids": await make_spools(db_session, 100),
            "template": "roll",
            "roll": {"tape": "50", "length_mm": 40},
        }
        # Cheap stand-ins for the PDF rendering (it took many seconds for 100 pages):
        # this test is about the cap check and the page count reaching the driver.
        driver = FakeDriver(track_alive=False)
        with (
            patched(driver),
            patch("backend.app.api.routes.labels.render_labels", return_value=b"%PDF"),
            patch(
                "backend.app.api.routes.labels.iter_pdf_images",
                side_effect=lambda *a, **k: (Image.new("L", (40, 10), 255) for _ in range(100)),
            ),
        ):
            job = await print_and_wait(async_client, body, timeout=10)
        assert job["state"] == "done" and job["total"] == 100
        assert len(driver.sizes) == 100

    @pytest.mark.asyncio
    async def test_no_printer_configured(self, async_client: AsyncClient, spool):
        resp = await async_client.post("/api/v1/inventory/labels/print", json={"spool_ids": [spool.id], **ROLL})
        assert resp.status_code == 400
        assert "No label printer" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_sheet_template_refused(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        resp = await async_client.post(
            "/api/v1/inventory/labels/print", json={"spool_ids": [spool.id], "template": "avery_l7160"}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Sheet templates can't be printed on a label printer"

    @pytest.mark.asyncio
    async def test_other_tape_refused(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        body = {"spool_ids": [spool.id], "template": "roll", "roll": {"tape": "62", "length_mm": 40}}
        resp = await async_client.post("/api/v1/inventory/labels/print", json=body)
        assert resp.status_code == 400
        assert "50 mm" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_limits(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        resp = await async_client.post(
            "/api/v1/inventory/labels/print", json={"spool_ids": list(range(1, 102)), **ROLL}
        )
        assert resp.status_code == 422
        resp = await async_client.post(
            "/api/v1/inventory/labels/print",
            json={"spool_ids": [1], "template": "roll", "roll": {"tape": "50", "length_mm": 5}},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "status", "text"),
        [
            (PrinterUnreachable("192.168.1.50:9101: refused"), 502, "Label printer not reachable"),
            (WrongMedia("50 mm", "62 mm"), 409, "The printer has 62 mm tape loaded, the settings say 50 mm"),
            (PrinterReportedError(["cover open", "no tape loaded"]), 502, "Label printer reports: cover open, no tape"),
            (PrinterDidNotFinish("2 of 5"), 504, "did not confirm all labels; check the printer before printing again"),
        ],
    )
    async def test_failure_is_a_failed_job_with_a_mapped_message(
        self, async_client: AsyncClient, db_session, spool, error, status, text
    ):
        await configure(db_session)
        with patched(FakeDriver(error)):
            job = await print_and_wait(async_client, {"spool_ids": [spool.id], **ROLL})
        assert job["state"] == "failed" and job["printed"] == 0
        assert text in job["error"]
        # The print route needs only inventory:update: it must not reveal the printer's address.
        assert "192.168.1.50" not in job["error"] and "9101" not in job["error"]

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_a_generic_failed_job(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        with patched(FakeDriver(RuntimeError("secret 192.168.1.50 detail"))):
            job = await print_and_wait(async_client, {"spool_ids": [spool.id], **ROLL})
        assert job["state"] == "failed" and job["error"] == "Label printing failed"

    @pytest.mark.asyncio
    async def test_time_limit_is_failed_without_the_address(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        driver = FakeDriver(delay=0.5, timeout=0.1)
        with patched(driver):
            job = await print_and_wait(async_client, {"spool_ids": [spool.id], **ROLL})
        assert job["state"] == "failed"
        assert "check the printer before printing again" in job["error"]
        assert "192.168.1.50" not in job["error"]
        await asyncio.sleep(0.6)
        assert driver.abandoned.is_set()  # the thread was told to give up

    @pytest.mark.asyncio
    async def test_job_lifecycle_queued_printing_done(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        gate = threading.Event()
        driver = FakeDriver(gate=gate)
        with patched(driver):
            first = (await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})).json()
            await wait_for_state(async_client, first["job_id"], {"printing"})
            second = (
                await async_client.post(PRINT, json={"spool_ids": await make_spools(db_session, 2), **ROLL})
            ).json()
            # The second waits behind the first: queued, nothing printed yet.
            queued = await wait_for_state(async_client, second["job_id"], {"queued"})
            assert queued["printed"] == 0 and queued["total"] == 2
            gate.set()
            assert (await wait_for_state(async_client, first["job_id"], {"done"}))["printed"] == 1
            assert (await wait_for_state(async_client, second["job_id"], {"done"}))["printed"] == 2

    @pytest.mark.asyncio
    async def test_jobs_to_one_printer_run_in_order_one_at_a_time(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        gate = threading.Event()
        driver = FakeDriver(gate=gate, delay=0.05)
        with patched(driver):
            ids = []
            for n in (1, 2, 3):
                resp = await async_client.post(PRINT, json={"spool_ids": await make_spools(db_session, n), **ROLL})
                ids.append(resp.json()["job_id"])
            gate.set()
            for job_id in ids:
                await wait_for_state(async_client, job_id, {"done"})
        assert driver.max_active == 1
        # Delivered in submission order: the jobs' page counts 1, 2, 3.
        assert [call[1].pages for call in driver.calls if call[0] == "deliver"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_jobs_to_different_printers_run_in_parallel(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session, label_printer_host="192.168.1.50")
        gate = threading.Event()
        driver = FakeDriver(gate=gate)
        with patched(driver):
            a = (await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})).json()
            await set_host(db_session, "192.168.1.51")
            b = (await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})).json()
            # Both are delivering at once, though neither has finished.
            await wait_for_state(async_client, a["job_id"], {"printing"})
            await wait_for_state(async_client, b["job_id"], {"printing"})
            deadline = time.monotonic() + 5
            while driver.active < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert driver.active == 2
            gate.set()
            await wait_for_state(async_client, a["job_id"], {"done"})
            await wait_for_state(async_client, b["job_id"], {"done"})
        assert sorted(driver.order) == ["192.168.1.50", "192.168.1.51"]

    @pytest.mark.asyncio
    async def test_failed_job_does_not_block_the_next(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        gate = threading.Event()
        bad = FakeDriver(PrinterUnreachable("x"), gate=gate)
        with patched(bad):
            first = (await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})).json()
            second = (await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})).json()
            gate.set()
            assert (await wait_for_state(async_client, first["job_id"], {"failed"}))["error"]
            assert (await wait_for_state(async_client, second["job_id"], {"failed", "done"}))["state"] == "failed"
        assert len([c for c in bad.calls if c[0] == "deliver"]) == 2

    @pytest.mark.asyncio
    async def test_printed_advances_with_the_printers_confirmations_and_is_kept_on_failure(
        self, async_client: AsyncClient, db_session
    ):
        await configure(db_session)
        ids = await make_spools(db_session, 3)
        driver = FakeDriver(PrinterReportedError(["cover open"]), confirm=2)
        with patched(driver):
            job = await print_and_wait(async_client, {"spool_ids": ids, **ROLL})
        assert (job["state"], job["printed"], job["total"]) == ("failed", 2, 3)

    @pytest.mark.asyncio
    async def test_silent_printer_shows_zero_printed_until_done_then_total(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        gate = threading.Event()
        driver = FakeDriver(gate=gate)  # never calls on_progress
        with patched(driver):
            resp = await async_client.post(PRINT, json={"spool_ids": await make_spools(db_session, 3), **ROLL})
            running = await wait_for_state(async_client, resp.json()["job_id"], {"printing"})
            assert running["printed"] == 0
            gate.set()
            done = await wait_for_state(async_client, resp.json()["job_id"], {"done"})
        assert done["printed"] == 3

    @pytest.mark.asyncio
    async def test_silent_printer_failure_leaves_printed_at_zero(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        with patched(FakeDriver(PrinterDidNotFinish("x"))):
            job = await print_and_wait(async_client, {"spool_ids": await make_spools(db_session, 3), **ROLL})
        assert (job["state"], job["printed"]) == ("failed", 0)

    @pytest.mark.asyncio
    async def test_too_many_jobs_waiting_for_one_printer_is_503(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        gate = threading.Event()
        with patched(FakeDriver(gate=gate)):
            first = await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})
            await wait_for_state(async_client, first.json()["job_id"], {"printing"})
            for _ in range(20):  # 20 may wait behind the running one
                resp = await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})
                assert resp.status_code == 202, resp.text
            resp = await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})
            assert resp.status_code == 503
            assert resp.json()["detail"] == "The label printer has too many jobs waiting; try again later"
            # Another printer has its own queue.
            await set_host(db_session, "192.168.1.51")
            other = await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})
            assert other.status_code == 202
            gate.set()
            await wait_for_state(async_client, first.json()["job_id"], {"done"})

    @pytest.mark.asyncio
    async def test_unknown_job_id_is_404(self, async_client: AsyncClient):
        resp = await async_client.get("/api/v1/labels/print-jobs/" + "0" * 32)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_job_survives_the_request_that_made_it(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        gate = threading.Event()
        driver = FakeDriver(gate=gate)
        with patched(driver):
            resp = await async_client.post(PRINT, json={"spool_ids": [spool.id], **ROLL})
            # The response is back and nothing awaits the job: it must still run to the end.
            gc.collect()
            gate.set()
            job = await wait_for_state(async_client, resp.json()["job_id"], {"done"})
        assert job["printed"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("key", "value", "text"),
        [
            ("label_printer_tape", "99", "tape"),
            ("label_printer_model", "QL-1", "model"),
            ("label_printer_type", "dymo", "type"),
            ("label_printer_host", "169.254.169.254", "Label printer address"),
            ("label_printer_host", "[::1", "Label printer address"),
        ],
    )
    async def test_invalid_saved_settings_are_400_not_500(
        self, async_client: AsyncClient, db_session, spool, key, value, text
    ):
        await configure(db_session, **{key: value})
        with patched(FakeDriver()):
            resp = await async_client.post("/api/v1/inventory/labels/print", json={"spool_ids": [spool.id], **ROLL})
            test = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 400, resp.text
        assert text in resp.json()["detail"]
        assert test.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_spool(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        with patched(FakeDriver()):
            resp = await async_client.post("/api/v1/inventory/labels/print", json={"spool_ids": [99999], **ROLL})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_spoolman_print_is_queued_too(self, async_client: AsyncClient, db_session):
        from backend.app.services.label_renderer import LabelData

        await configure(db_session)
        data = [LabelData(spool_id=7, name="S", material="PLA", brand="B", deeplink_url="http://x/7")]
        driver = FakeDriver()
        with patched(driver), patch("backend.app.api.routes.labels._spoolman_label_data", return_value=data):
            job = await print_and_wait(async_client, {"spool_ids": [7], **ROLL}, "/api/v1/spoolman/labels/print")
        assert job["state"] == "done" and job["total"] == 1

    @pytest.mark.asyncio
    async def test_spoolman_print_without_spoolman(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        resp = await async_client.post("/api/v1/spoolman/labels/print", json={"spool_ids": [1], **ROLL})
        assert resp.status_code == 400  # Spoolman is not enabled

    @pytest.mark.asyncio
    async def test_roll_download_and_preview(self, async_client: AsyncClient, spool):
        resp = await async_client.post("/api/v1/inventory/labels", json={"spool_ids": [spool.id], **ROLL})
        assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
        resp = await async_client.post(
            "/api/v1/inventory/labels/preview", json={"spool_id": spool.id, "monochrome": True, **ROLL}
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    @pytest.mark.asyncio
    async def test_roll_needs_its_spec(self, async_client: AsyncClient, spool):
        resp = await async_client.post("/api/v1/inventory/labels", json={"spool_ids": [spool.id], "template": "roll"})
        assert resp.status_code == 422
        resp = await async_client.post(
            "/api/v1/inventory/labels", json={"spool_ids": [spool.id], "template": "box_62x29", "roll": ROLL["roll"]}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_prints_over_tcp_end_to_end(self, async_client: AsyncClient, db_session, spool):
        from backend.tests.unit.test_brother_ql_3612 import FakePrinter, status_block

        printer = FakePrinter(replies=[status_block()], after_job=[status_block(status_type=0x01)])
        await configure(db_session, label_printer_host=f"127.0.0.1:{printer.port}")
        job = await print_and_wait(async_client, {"spool_ids": [spool.id], **ROLL})
        printer.close()
        assert job["state"] == "done", job
        assert printer.received.endswith(b"\x1a")


async def _admin(async_client: AsyncClient, username: str) -> str:
    await async_client.post(
        "/api/v1/auth/setup", json={"auth_enabled": True, "admin_username": username, "admin_password": _PW}
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": _PW})
    return login.json()["access_token"]


async def _user(async_client: AsyncClient, admin: str, username: str, permissions: list[str]) -> str:
    headers = {"Authorization": f"Bearer {admin}"}
    grp = await async_client.post(
        "/api/v1/groups/", headers=headers, json={"name": f"g_{username}", "permissions": permissions}
    )
    await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": username, "password": _PW, "role": "user", "group_ids": [grp.json()["id"]]},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": _PW})
    return login.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
class TestPermissions:
    @pytest.mark.asyncio
    async def test_print_needs_inventory_update(self, async_client: AsyncClient, db_session, spool):
        """Printing uses up tape, so read access is not enough; following the job is a read."""
        await configure(db_session)
        admin = await _admin(async_client, "lpadmin")
        reader = _bearer(await _user(async_client, admin, "lpreader", ["inventory:read"]))
        updater = _bearer(await _user(async_client, admin, "lpupdater1", ["inventory:update"]))
        nobody = _bearer(await _user(async_client, admin, "lpnobody", ["cost_centers:read_own"]))
        body = {"spool_ids": [spool.id], **ROLL}
        driver = FakeDriver()
        with patched(driver):
            denied = await async_client.post(PRINT, json=body, headers=reader)
            no_perm = await async_client.post(PRINT, json=body, headers=nobody)
            anon = await async_client.post(PRINT, json=body)
            assert denied.status_code == 403
            assert no_perm.status_code == 403
            assert anon.status_code in (401, 403)
            assert driver.calls == []  # a refused print reaches no printer
            ok = await async_client.post(PRINT, json=body, headers=updater)
            assert ok.status_code == 202, ok.text
            # The jobs route stays at inventory:read.
            job_url = f"/api/v1/labels/print-jobs/{ok.json()['job_id']}"
            got = await async_client.get(job_url, headers=reader)
            job_no_perm = await async_client.get(job_url, headers=nobody)
            anon_job = await async_client.get(job_url)
            job = await wait_for_state(async_client, ok.json()["job_id"], {"done", "failed"}, reader)
        assert got.status_code == 200, got.text
        assert job_no_perm.status_code == 403
        assert anon_job.status_code in (401, 403)
        assert job["state"] == "done", job

    @pytest.mark.asyncio
    async def test_spoolman_print_needs_inventory_update(self, async_client: AsyncClient, db_session):
        from backend.app.services.label_renderer import LabelData

        await configure(db_session)
        admin = await _admin(async_client, "lpadmin4")
        reader = _bearer(await _user(async_client, admin, "lpreader4", ["inventory:read"]))
        updater = _bearer(await _user(async_client, admin, "lpupdater4", ["inventory:update"]))
        data = [LabelData(spool_id=7, name="S", material="PLA", brand="B", deeplink_url="http://x/7")]
        url = "/api/v1/spoolman/labels/print"
        body = {"spool_ids": [7], **ROLL}
        with patched(FakeDriver()), patch("backend.app.api.routes.labels._spoolman_label_data", return_value=data):
            denied = await async_client.post(url, json=body, headers=reader)
            ok = await async_client.post(url, json=body, headers=updater)
            assert denied.status_code == 403
            assert ok.status_code == 202, ok.text
            await wait_for_state(async_client, ok.json()["job_id"], {"done", "failed"}, reader)

    @pytest.mark.asyncio
    async def test_download_and_preview_stay_at_inventory_read(self, async_client: AsyncClient, db_session, spool):
        await configure(db_session)
        admin = await _admin(async_client, "lpadmin5")
        reader = _bearer(await _user(async_client, admin, "lpreader5", ["inventory:read"]))
        download = await async_client.post(
            "/api/v1/inventory/labels", json={"spool_ids": [spool.id], **ROLL}, headers=reader
        )
        preview = await async_client.post(
            "/api/v1/inventory/labels/preview", json={"spool_id": spool.id, **ROLL}, headers=reader
        )
        assert download.status_code == 200, download.text
        assert download.headers["content-type"] == "application/pdf"
        assert preview.status_code == 200, preview.text

    @pytest.mark.asyncio
    async def test_api_key_needs_the_inventory_scope_to_print(self, async_client: AsyncClient, db_session, spool):
        """inventory:update is the Manage Inventory scope of an API key; a read-only key can't print."""
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        await configure(db_session)
        keys = {}
        for scope in ("can_read_status", "can_manage_inventory"):
            full_key, key_hash, key_prefix = generate_api_key()
            flags = {"can_queue": False, "can_control_printer": False, "can_read_status": False}
            flags.update(can_manage_inventory=False)
            flags[scope] = True
            db_session.add(APIKey(name=scope, key_hash=key_hash, key_prefix=key_prefix, enabled=True, **flags))
            keys[scope] = {"X-API-Key": full_key}
        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()
        body = {"spool_ids": [spool.id], **ROLL}
        with patched(FakeDriver()):
            denied = await async_client.post(PRINT, json=body, headers=keys["can_read_status"])
            ok = await async_client.post(PRINT, json=body, headers=keys["can_manage_inventory"])
            assert denied.status_code == 403
            assert ok.status_code == 202, ok.text
            await wait_for_state(async_client, ok.json()["job_id"], {"done", "failed"}, keys["can_read_status"])

    @pytest.mark.asyncio
    async def test_test_endpoint_needs_settings_update(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        admin = await _admin(async_client, "lpadmin2")
        reader = await _user(async_client, admin, "lpreader2", ["settings:read", "inventory:read"])
        updater = await _user(async_client, admin, "lpupdater", ["settings:update"])
        with patch("backend.app.api.routes.settings.get_driver", return_value=FakeDriver()):
            denied = await async_client.post(
                "/api/v1/settings/label-printer/test", headers={"Authorization": f"Bearer {reader}"}
            )
            ok = await async_client.post(
                "/api/v1/settings/label-printer/test", headers={"Authorization": f"Bearer {updater}"}
            )
        assert denied.status_code == 403
        assert ok.status_code == 200, ok.text

    @pytest.mark.asyncio
    async def test_ui_flags_reach_a_user_without_settings_read(self, async_client: AsyncClient, db_session):
        await configure(db_session, label_printer_tape="62")
        admin = await _admin(async_client, "lpadmin3")
        user = await _user(async_client, admin, "lpflags", ["inventory:read"])
        resp = await async_client.get("/api/v1/settings/ui-flags", headers={"Authorization": f"Bearer {user}"})
        assert resp.json()["label_printer_configured"] is True
        assert resp.json()["label_printer_tape"] == "62"
        assert "192.168.1.50" not in resp.text


@pytest.mark.integration
class TestTestEndpoint:
    @pytest.mark.asyncio
    async def test_uses_saved_settings_and_returns_status(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        driver = FakeDriver()
        with patch("backend.app.api.routes.settings.get_driver", return_value=driver):
            resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"]["media_width_mm"] == 50
        assert driver.calls[0][1].host == "192.168.1.50"
        assert driver.calls[1][0] == "deliver"

    @pytest.mark.asyncio
    async def test_is_synchronous_and_prepares_off_the_event_loop(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        driver = FakeDriver()
        with patch("backend.app.api.routes.settings.get_driver", return_value=driver):
            resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 200  # the answer, not a job id
        assert driver.test_thread is not threading.main_thread()

    @pytest.mark.asyncio
    async def test_does_not_wait_for_the_lock(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        driver = FakeDriver()
        with patch("backend.app.api.routes.settings.get_driver", return_value=driver):
            resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 200
        assert driver.lock_timeouts == [0]  # a busy printer fails at once, not after a wait

    @pytest.mark.asyncio
    async def test_busy_when_a_print_job_holds_the_printer(self, async_client: AsyncClient, db_session):
        from backend.app.services.label_printers.brother_ql import _printer_lock

        await configure(db_session)  # 192.168.1.50:9101, the real driver
        with _printer_lock("192.168.1.50", 9101):
            started = time.monotonic()
            resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 503
        assert "busy" in resp.json()["detail"]
        assert time.monotonic() - started < 3

    @pytest.mark.asyncio
    async def test_short_bound(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        with patch("backend.app.api.routes.settings.get_driver", return_value=FakeDriver(delay=0.5, timeout=0.1)):
            resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 504

    @pytest.mark.asyncio
    async def test_unsaved_values_win(self, async_client: AsyncClient):
        driver = FakeDriver()
        body = {"type": "brother_ql", "host": "10.0.0.7", "tape": "62", "model": "QL-810W"}
        with patch("backend.app.api.routes.settings.get_driver", return_value=driver):
            resp = await async_client.post("/api/v1/settings/label-printer/test", json=body)
        assert resp.status_code == 200, resp.text
        config = driver.calls[0][1]
        assert (config.host, config.port, config.tape, config.model) == ("10.0.0.7", 9100, "62", "QL-810W")

    @pytest.mark.asyncio
    async def test_nothing_configured(self, async_client: AsyncClient):
        resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_bad_values_refused(self, async_client: AsyncClient):
        for body in ({"host": "169.254.169.254"}, {"tape": "99"}, {"model": "QL-1"}, {"type": "x"}):
            resp = await async_client.post("/api/v1/settings/label-printer/test", json=body)
            assert resp.status_code == 422, body

    @pytest.mark.asyncio
    async def test_error_mapping(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        with patch("backend.app.api.routes.settings.get_driver", return_value=FakeDriver(WrongMedia("50 mm", "62 mm"))):
            resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_test_endpoint_may_name_the_address(self, async_client: AsyncClient, db_session):
        await configure(db_session)
        with patch(
            "backend.app.api.routes.settings.get_driver", return_value=FakeDriver(PrinterUnreachable("refused"))
        ):
            resp = await async_client.post("/api/v1/settings/label-printer/test")
        assert resp.status_code == 502
        assert "192.168.1.50:9101" in resp.json()["detail"]


@pytest.mark.integration
class TestSettingsValidation:
    @pytest.mark.asyncio
    async def test_saves_and_reports_configured(self, async_client: AsyncClient):
        resp = await async_client.put(
            "/api/v1/settings/",
            json={
                "label_printer_type": "brother_ql",
                "label_printer_host": "label-printer.local:9100",
                "label_printer_model": "QL-810W",
                "label_printer_tape": "62",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["label_printer_configured"] is True
        flags = (await async_client.get("/api/v1/settings/ui-flags")).json()
        assert flags["label_printer_configured"] is True
        assert flags["label_printer_tape"] == "62"

    @pytest.mark.asyncio
    async def test_not_configured_by_default(self, async_client: AsyncClient):
        flags = (await async_client.get("/api/v1/settings/ui-flags")).json()
        assert flags["label_printer_configured"] is False
        assert flags["label_printer_tape"] == ""
        assert (await async_client.get("/api/v1/settings/")).json()["label_printer_configured"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body",
        [
            {"label_printer_host": "169.254.169.254"},
            {"label_printer_host": "169.254.1.1:9100"},
            {"label_printer_host": "metadata.google.internal"},
            {"label_printer_host": "host:99999"},
            {"label_printer_host": "http://host"},
            {"label_printer_tape": "99"},
            {"label_printer_model": "QL-1"},
            {"label_printer_type": "dymo"},
        ],
    )
    async def test_bad_values_are_422(self, async_client: AsyncClient, body):
        resp = await async_client.put("/api/v1/settings/", json=body)
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_empty_model_and_tape_show_the_defaults(self, async_client: AsyncClient, db_session):
        await configure(db_session, label_printer_model="", label_printer_tape="")
        body = (await async_client.get("/api/v1/settings/")).json()
        assert (body["label_printer_model"], body["label_printer_tape"]) == ("QL-820NWB", "50")

    @pytest.mark.asyncio
    async def test_malformed_address_message_names_the_field(self, async_client: AsyncClient):
        resp = await async_client.put("/api/v1/settings/", json={"label_printer_host": "[::1"})
        assert resp.status_code == 422
        assert "Label printer address" in resp.text

    @pytest.mark.asyncio
    async def test_type_alone_is_not_configured(self, async_client: AsyncClient):
        await async_client.put("/api/v1/settings/", json={"label_printer_type": "brother_ql"})
        flags = (await async_client.get("/api/v1/settings/ui-flags")).json()
        assert flags["label_printer_configured"] is False
