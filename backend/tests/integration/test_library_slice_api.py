"""Integration tests for the slice-via-API flow.

Routes under test:
- POST /library/files/{id}/slice  (returns 202 + job_id; bg task does the work)
- POST /archives/{id}/slice        (same shape; result lands in archives table)
- GET /slice-jobs/{id}             (poll for terminal state)

The synchronous validation paths (404 missing source, 400 wrong file type)
are tested directly. The bg-task paths poll until the job finishes and then
assert on the captured state.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from collections.abc import Callable

import httpx
import pytest
from httpx import AsyncClient

from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile
from backend.app.models.local_preset import LocalPreset
from backend.app.models.settings import Settings as SettingsModel
from backend.app.services import slicer_api as slicer_api_module
from backend.app.services.slice_dispatch import slice_dispatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_3mf_with_settings(settings_payload: dict | None = None) -> bytes:
    """Build a tiny in-memory 3MF zip with all the embedded-config files
    that real-world Bambu Studio / OrcaSlicer 3MFs ship with.

    The strip-before-forwarding helper has to remove ALL of these (not
    just `project_settings.config`) — leftover entries reference printer
    / filament IDs from the original slice and trip the CLI's input
    validation when a different `--load-settings` triplet is supplied.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps(settings_payload or {"prime_tower_brim_width": "-1"}),
        )
        zf.writestr("Metadata/model_settings.config", "<config><object id='1'/></config>")
        zf.writestr(
            "Metadata/slice_info.config",
            "<config><plate><metadata key='filament' value='GFL00'/></plate></config>",
        )
        zf.writestr("Metadata/cut_information.xml", "<cut><part id='1'/></cut>")
    return buf.getvalue()


def _install_mock_sidecar(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Pin a MockTransport-backed httpx client onto the slicer_api singleton
    so per-request `SlicerApiService` instances reuse it instead of opening
    a real connection."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0)
    slicer_api_module.set_shared_http_client(client)
    return client


async def _wait_for_job(client: AsyncClient, job_id: int, timeout: float = 5.0) -> dict:
    """Poll `/api/v1/slice-jobs/{id}` until the job hits a terminal state.

    The dispatcher runs work as an asyncio task on the same event loop, so
    poll-with-sleep here is enough — a few yields and the task finishes.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/api/v1/slice-jobs/{job_id}")
        if r.status_code != 200:
            raise AssertionError(f"slice-jobs poll failed: {r.status_code} {r.text}")
        body = r.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"slice job {job_id} did not finish in {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def slice_test_setup(db_session, tmp_path):
    """Source LibraryFile + 3 LocalPresets + preferred_slicer=orcaslicer."""
    storage_dir = tmp_path / "library" / "files"
    storage_dir.mkdir(parents=True, exist_ok=True)
    src_path = storage_dir / "Cube.stl"
    src_path.write_bytes(b"solid Cube\nendsolid\n")

    original_base_dir = app_settings.base_dir
    app_settings.base_dir = tmp_path

    src_file = LibraryFile(
        filename="Cube.stl",
        file_path=str(src_path.relative_to(tmp_path)),
        file_type="stl",
        file_size=src_path.stat().st_size,
    )
    db_session.add(src_file)

    presets = {}
    for kind in ("printer", "process", "filament"):
        p = LocalPreset(
            name=f"Test {kind}",
            preset_type=kind,
            source="orcaslicer",
            setting=json.dumps({"name": f"Test {kind}", "type": kind}),
        )
        db_session.add(p)
        presets[kind] = p

    db_session.add(SettingsModel(key="preferred_slicer", value="orcaslicer"))
    await db_session.commit()

    for p in presets.values():
        await db_session.refresh(p)
    await db_session.refresh(src_file)

    yield {
        "src_file_id": src_file.id,
        "printer_id": presets["printer"].id,
        "process_id": presets["process"].id,
        "filament_id": presets["filament"].id,
        "tmp_path": tmp_path,
    }

    app_settings.base_dir = original_base_dir
    slicer_api_module.set_shared_http_client(None)


# ---------------------------------------------------------------------------
# POST /library/files/{id}/slice — synchronous validation paths
# ---------------------------------------------------------------------------


class TestSliceValidation:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_404_when_source_missing(self, async_client: AsyncClient, slice_test_setup):
        _install_mock_sidecar(lambda r: httpx.Response(200, content=b""))
        response = await async_client.post(
            "/api/v1/library/files/999999/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_400_for_wrong_file_type(self, async_client: AsyncClient, db_session, slice_test_setup):
        gcode_path = slice_test_setup["tmp_path"] / "library" / "files" / "out.gcode"
        gcode_path.write_bytes(b"; gcode\n")
        gfile = LibraryFile(
            filename="out.gcode",
            file_path=str(gcode_path.relative_to(slice_test_setup["tmp_path"])),
            file_type="gcode",
            file_size=10,
        )
        db_session.add(gfile)
        await db_session.commit()
        await db_session.refresh(gfile)

        _install_mock_sidecar(lambda r: httpx.Response(200, content=b""))
        response = await async_client.post(
            f"/api/v1/library/files/{gfile.id}/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 400
        assert "STL, 3MF, or STEP" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /library/files/{id}/slice — async dispatch + bg job
# ---------------------------------------------------------------------------


class TestSliceLibraryFile:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_happy_path_returns_202_then_job_completes_with_library_file(
        self, async_client: AsyncClient, slice_test_setup
    ):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(
                status_code=200,
                content=b"PK\x03\x04 fake-3mf",
                headers={
                    "x-print-time-seconds": "656",
                    "x-filament-used-g": "0.94",
                    "x-filament-used-mm": "302.5",
                },
            )

        _install_mock_sidecar(handler)

        response = await async_client.post(
            f"/api/v1/library/files/{slice_test_setup['src_file_id']}/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert body["status_url"].startswith("/api/v1/slice-jobs/")

        final = await _wait_for_job(async_client, body["job_id"])
        assert final["status"] == "completed", final
        assert final["result"]["library_file_id"] != slice_test_setup["src_file_id"]
        assert final["result"]["print_time_seconds"] == 656
        assert captured["url"].endswith("/slice")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_preset_id_surfaces_as_failed_job_with_status_400(
        self, async_client: AsyncClient, slice_test_setup
    ):
        _install_mock_sidecar(lambda r: httpx.Response(200, content=b""))
        response = await async_client.post(
            f"/api/v1/library/files/{slice_test_setup['src_file_id']}/slice",
            json={
                # Swap printer/filament — both exist but wrong preset_type.
                "printer_preset_id": slice_test_setup["filament_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["printer_id"],
            },
        )
        assert response.status_code == 202

        final = await _wait_for_job(async_client, response.json()["job_id"])
        assert final["status"] == "failed"
        assert final["error_status"] == 400
        assert "preset_type" in (final["error_detail"] or "")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_preferred_slicer_fails_with_400(
        self, async_client: AsyncClient, db_session, slice_test_setup
    ):
        await db_session.execute(
            SettingsModel.__table__.update().where(SettingsModel.key == "preferred_slicer").values(value="prusaslicer")
        )
        await db_session.commit()

        _install_mock_sidecar(lambda r: httpx.Response(200, content=b""))
        response = await async_client.post(
            f"/api/v1/library/files/{slice_test_setup['src_file_id']}/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 202
        final = await _wait_for_job(async_client, response.json()["job_id"])
        assert final["status"] == "failed"
        assert final["error_status"] == 400
        assert "preferred_slicer" in (final["error_detail"] or "")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sidecar_unreachable_fails_with_502(self, async_client: AsyncClient, slice_test_setup):
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        _install_mock_sidecar(handler)
        response = await async_client.post(
            f"/api/v1/library/files/{slice_test_setup['src_file_id']}/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 202
        final = await _wait_for_job(async_client, response.json()["job_id"])
        assert final["status"] == "failed"
        assert final["error_status"] == 502
        assert "unreachable" in (final["error_detail"] or "").lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_3mf_falls_back_to_embedded_settings_on_cli_failure(
        self, async_client: AsyncClient, db_session, slice_test_setup
    ):
        # When the slicer CLI fails on the --load-settings path (segfault
        # on complex H2D models), Bambuddy retries with no profile triplet
        # so the CLI uses the file's embedded settings.
        src_3mf_path = slice_test_setup["tmp_path"] / "library" / "files" / "complex.3mf"
        src_3mf_path.write_bytes(_make_3mf_with_settings({"prime_tower_brim_width": "-1"}))
        threemf = LibraryFile(
            filename="complex.3mf",
            file_path=str(src_3mf_path.relative_to(slice_test_setup["tmp_path"])),
            file_type="3mf",
            file_size=src_3mf_path.stat().st_size,
        )
        db_session.add(threemf)
        await db_session.commit()
        await db_session.refresh(threemf)

        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            # First call: profile triplet present → simulate CLI 5xx
            if call_count["n"] == 1:
                return httpx.Response(
                    status_code=500,
                    json={"message": "Failed to slice the model"},
                )
            # Retry: no profile triplet → succeed with embedded settings
            return httpx.Response(
                status_code=200,
                content=b"PK\x03\x04 fake-3mf",
                headers={
                    "x-print-time-seconds": "100",
                    "x-filament-used-g": "1.0",
                    "x-filament-used-mm": "100",
                },
            )

        _install_mock_sidecar(handler)
        response = await async_client.post(
            f"/api/v1/library/files/{threemf.id}/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 202

        final = await _wait_for_job(async_client, response.json()["job_id"])
        assert final["status"] == "completed", final
        assert final["result"]["used_embedded_settings"] is True
        assert call_count["n"] == 2  # primary + fallback retry

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stl_does_not_fall_back_on_cli_failure(self, async_client: AsyncClient, slice_test_setup):
        # STL has no embedded settings — the CLI 5xx is terminal.
        call_count = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(
                status_code=500,
                json={"message": "Failed to slice the model"},
            )

        _install_mock_sidecar(handler)
        response = await async_client.post(
            f"/api/v1/library/files/{slice_test_setup['src_file_id']}/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 202
        final = await _wait_for_job(async_client, response.json()["job_id"])
        assert final["status"] == "failed"
        assert final["error_status"] == 502
        assert call_count["n"] == 1  # No retry for STL

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_3mf_input_forwarded_unmodified_to_sidecar(
        self, async_client: AsyncClient, db_session, slice_test_setup
    ):
        # 3MF input must be forwarded to the sidecar verbatim — every
        # Metadata/*.config the source carries (project_settings,
        # model_settings, slice_info, cut_information) is needed by the
        # CLI to find plate definitions and baseline config; an earlier
        # version of this code stripped them and caused the CLI to
        # silently exit immediately after "Initializing StaticPrintConfigs"
        # for every 3MF slice. --load-settings overrides the specific
        # fields the user changed; the rest comes from the embedded data.
        src_3mf_path = slice_test_setup["tmp_path"] / "library" / "files" / "real.3mf"
        src_3mf_path.write_bytes(_make_3mf_with_settings({"prime_tower_brim_width": "-1"}))
        threemf = LibraryFile(
            filename="real.3mf",
            file_path=str(src_3mf_path.relative_to(slice_test_setup["tmp_path"])),
            file_type="3mf",
            file_size=src_3mf_path.stat().st_size,
        )
        db_session.add(threemf)
        await db_session.commit()
        await db_session.refresh(threemf)

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(
                status_code=200,
                content=b"PK\x03\x04 fake-3mf",
                headers={
                    "x-print-time-seconds": "1",
                    "x-filament-used-g": "0",
                    "x-filament-used-mm": "0",
                },
            )

        _install_mock_sidecar(handler)
        response = await async_client.post(
            f"/api/v1/library/files/{threemf.id}/slice",
            json={
                "printer_preset_id": slice_test_setup["printer_id"],
                "process_preset_id": slice_test_setup["process_id"],
                "filament_preset_id": slice_test_setup["filament_id"],
            },
        )
        assert response.status_code == 202
        final = await _wait_for_job(async_client, response.json()["job_id"])
        assert final["status"] == "completed", final

        # Recover the embedded zip from the multipart body and assert ALL
        # the source's Metadata/*.config files are still present — the
        # opposite of the previous (broken) "strip everything" test.
        body = captured["body"]
        pk = body.find(b"PK\x03\x04")
        assert pk >= 0, "3MF body not found in multipart payload"
        with zipfile.ZipFile(io.BytesIO(body[pk:]), "r") as zin:
            names = set(zin.namelist())
        assert "Metadata/project_settings.config" in names
        assert "Metadata/model_settings.config" in names
        assert "Metadata/slice_info.config" in names
        assert "Metadata/cut_information.xml" in names
        assert "3D/3dmodel.model" in names


# ---------------------------------------------------------------------------
# GET /slice-jobs/{id}
# ---------------------------------------------------------------------------


class TestSliceJobs:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_job_returns_404(self, async_client: AsyncClient):
        # Sweep dispatcher state so a fresh ID is unknown.
        slice_dispatch._jobs.clear()
        r = await async_client.get("/api/v1/slice-jobs/999999")
        assert r.status_code == 404
