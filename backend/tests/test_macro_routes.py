"""Integration tests for the macro API routes.

Uses async_client (real ASGI + in-memory SQLite).
The macro runner's create_task is patched in tests that trigger runs so
background execution does not race with assertions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("backend.app.core.config.settings.macros_dir", str(d))
    return d


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _create_file(client, name: str, content: str = "") -> dict:
    resp = await client.post("/api/v1/macros/cfg-files", json={"name": name, "content": content})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── RT1–RT6: Cfg file CRUD ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_cfg_file(async_client, tmp_path):
    from pathlib import Path

    from backend.app.core.config import settings

    data = await _create_file(async_client, "preheat", "[macro preheat]\nM140 S60\n")

    assert data["name"] == "preheat"
    assert data["file_path"].endswith(".cfg")
    assert data["parse_error"] is None
    # File must exist on disk
    assert Path(settings.macros_dir, data["file_path"]).exists()


@pytest.mark.asyncio
async def test_create_cfg_file_duplicate_name(async_client):
    await _create_file(async_client, "duplicate")
    resp = await async_client.post("/api/v1/macros/cfg-files", json={"name": "duplicate"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_cfg_file(async_client):
    created = await _create_file(async_client, "home")
    resp = await async_client.get(f"/api/v1/macros/cfg-files/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]
    assert resp.json()["name"] == "home"


@pytest.mark.asyncio
async def test_get_cfg_file_not_found(async_client):
    resp = await async_client.get("/api/v1/macros/cfg-files/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_cfg_file_content(async_client):
    content = "[macro content_test]\nG28\n"
    created = await _create_file(async_client, "content_test", content)
    resp = await async_client.get(f"/api/v1/macros/cfg-files/{created['id']}/content")
    assert resp.status_code == 200
    assert resp.json()["content"] == content


@pytest.mark.asyncio
async def test_save_cfg_file_updates_disk_and_db(async_client, tmp_path):
    from pathlib import Path

    from backend.app.core.config import settings

    created = await _create_file(async_client, "updatable", "[macro updatable]\nG28\n")

    new_content = "[macro updatable]\nM140 S80\nWAIT --seconds=1\n"
    resp = await async_client.put(
        f"/api/v1/macros/cfg-files/{created['id']}",
        json={"content": new_content},
    )
    assert resp.status_code == 200
    # File path unchanged (in-place rewrite)
    assert resp.json()["file_path"] == created["file_path"]
    # Content on disk updated
    full = Path(settings.macros_dir, created["file_path"])
    assert full.read_text(encoding="utf-8") == new_content


@pytest.mark.asyncio
async def test_delete_cfg_file(async_client, tmp_path):
    from pathlib import Path

    from backend.app.core.config import settings

    created = await _create_file(async_client, "deletable", "[macro deletable]\nG28\n")
    full = Path(settings.macros_dir, created["file_path"])
    assert full.exists()

    resp = await async_client.delete(f"/api/v1/macros/cfg-files/{created['id']}")
    assert resp.status_code == 200
    assert not full.exists()

    # Confirm DB row gone
    resp2 = await async_client.get(f"/api/v1/macros/cfg-files/{created['id']}")
    assert resp2.status_code == 404


# ── RT7–RT9: Macro read ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_macros(async_client):
    await _create_file(async_client, "list_a", "[macro list_a]\nG28\n")
    await _create_file(async_client, "list_b", "[macro list_b]\nM84\n")

    resp = await async_client.get("/api/v1/macros")
    assert resp.status_code == 200
    names = [m["name"] for m in resp.json()]
    assert "list_a" in names
    assert "list_b" in names


@pytest.mark.asyncio
async def test_get_macro(async_client):
    await _create_file(async_client, "single", "[macro single]\nG28\n")

    macros = (await async_client.get("/api/v1/macros")).json()
    macro = next(m for m in macros if m["name"] == "single")

    resp = await async_client.get(f"/api/v1/macros/{macro['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "single"
    assert resp.json()["trigger_type"] == "manual"


@pytest.mark.asyncio
async def test_get_macro_not_found(async_client):
    resp = await async_client.get("/api/v1/macros/99999")
    assert resp.status_code == 404


# ── RT10–RT14: Run lifecycle ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_macro_returns_pending_run(async_client):
    await _create_file(async_client, "runnable", "[macro runnable]\nG28\n")
    macros = (await async_client.get("/api/v1/macros")).json()
    macro = next(m for m in macros if m["name"] == "runnable")

    with patch("backend.app.api.routes.macros.macro_runner.run_macro", new=AsyncMock(return_value=1)):
        resp = await async_client.post(f"/api/v1/macros/{macro['id']}/run", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["macro_id"] == macro["id"]
    assert data["status"] in ("pending", "running")
    assert data["trigger"] == "manual"


@pytest.mark.asyncio
async def test_get_run(async_client):
    await _create_file(async_client, "run_get", "[macro run_get]\nG28\n")
    macros = (await async_client.get("/api/v1/macros")).json()
    macro = next(m for m in macros if m["name"] == "run_get")

    with patch("backend.app.api.routes.macros.macro_runner.run_macro", new=AsyncMock(return_value=1)):
        run_resp = await async_client.post(f"/api/v1/macros/{macro['id']}/run", json={})
    run_id = run_resp.json()["id"]

    resp = await async_client.get(f"/api/v1/macros/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


@pytest.mark.asyncio
async def test_list_runs(async_client):
    await _create_file(async_client, "multi_run", "[macro multi_run]\nG28\n")
    macros = (await async_client.get("/api/v1/macros")).json()
    macro = next(m for m in macros if m["name"] == "multi_run")

    with patch("backend.app.api.routes.macros.macro_runner.run_macro", new=AsyncMock(return_value=1)):
        await async_client.post(f"/api/v1/macros/{macro['id']}/run", json={})
        await async_client.post(f"/api/v1/macros/{macro['id']}/run", json={})

    resp = await async_client.get(f"/api/v1/macros/{macro['id']}/runs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_cancel_active_run(async_client):
    await _create_file(async_client, "cancellable", "[macro cancellable]\nG28\n")
    macros = (await async_client.get("/api/v1/macros")).json()
    macro = next(m for m in macros if m["name"] == "cancellable")

    with patch("backend.app.api.routes.macros.macro_runner.run_macro", new=AsyncMock(return_value=1)):
        run_resp = await async_client.post(f"/api/v1/macros/{macro['id']}/run", json={})
    run_id = run_resp.json()["id"]

    # cancel_run returns False (no live task) but the route still marks it done
    with patch("backend.app.api.routes.macros.macro_runner.cancel_run", return_value=False):
        resp = await async_client.post(f"/api/v1/macros/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_cancel_finished_run_returns_409(async_client):
    from datetime import datetime, timezone

    from backend.app.models.macro import MacroRun

    await _create_file(async_client, "done_run", "[macro done_run]\nG28\n")
    macros = (await async_client.get("/api/v1/macros")).json()
    macro = next(m for m in macros if m["name"] == "done_run")

    with patch("backend.app.api.routes.macros.macro_runner.run_macro", new=AsyncMock(return_value=1)):
        run_resp = await async_client.post(f"/api/v1/macros/{macro['id']}/run", json={})
    run_id = run_resp.json()["id"]

    # Manually mark as finished via the DB
    from sqlalchemy import update as sa_update

    from backend.app.core.database import async_session

    async with async_session() as db:
        await db.execute(
            sa_update(MacroRun)
            .where(MacroRun.id == run_id)
            .values(status="success", finished_at=datetime.now(timezone.utc))
        )
        await db.commit()

    resp = await async_client.post(f"/api/v1/macros/runs/{run_id}/cancel")
    assert resp.status_code == 409


# ── RT15–RT16: Utility endpoints ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gcode_whitelist(async_client):
    resp = await async_client.get("/api/v1/macros/gcode-whitelist")
    assert resp.status_code == 200
    whitelist = resp.json()
    assert isinstance(whitelist, list)
    assert len(whitelist) > 0
    assert "G28" in whitelist
    assert all(isinstance(s, str) for s in whitelist)


@pytest.mark.asyncio
async def test_function_catalogue(async_client):
    resp = await async_client.get("/api/v1/macros/functions")
    assert resp.status_code == 200
    catalogue = resp.json()
    assert isinstance(catalogue, list)
    names = [f["name"] for f in catalogue]
    for expected in ("NOTIFY", "WAIT", "PRINTER_PAUSE", "MACRO"):
        assert expected in names, f"{expected} missing from function catalogue"
    # Each entry has required fields
    for fn in catalogue:
        assert "name" in fn
        assert "description" in fn
        assert "args" in fn
        assert "allowed_in_embed" in fn


# ── RT17–RT18: exec terminal ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exec_gcode_line(async_client):
    mock_client = MagicMock()
    mock_client.state.connected = True
    mock_client.state.state = "IDLE"
    mock_client.state.hms_errors = []
    mock_client.state.temperatures = {}
    mock_client.send_gcode = MagicMock(return_value=True)

    with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = mock_client
        resp = await async_client.post(
            "/api/v1/macros/exec",
            json={"line": "G28", "printer_id": 1},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_exec_run_macro_syntax(async_client):
    await _create_file(async_client, "terminal_macro", "[macro terminal_macro]\nG28\n")

    with patch("backend.app.api.routes.macros.macro_runner.run_macro", new=AsyncMock(return_value=1)):
        resp = await async_client.post(
            "/api/v1/macros/exec",
            json={"line": "run: terminal_macro"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data.get("run_id") is not None


@pytest.mark.asyncio
async def test_exec_run_macro_not_found(async_client):
    resp = await async_client.post(
        "/api/v1/macros/exec",
        json={"line": "run: nonexistent_macro_xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert "not found" in resp.json()["log"].lower()


# ── Extra: parse error surface ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_cron_surfaces_in_parse_error(async_client):
    pytest.importorskip("croniter", reason="cron validation requires croniter")
    content = "[macro bad_cron]\ntrigger: schedule\ncron: not a cron\nG28\n"
    data = await _create_file(async_client, "bad_cron", content)
    assert data["parse_error"] is not None
    assert "cron" in data["parse_error"].lower() or "not a cron" in data["parse_error"]


@pytest.mark.asyncio
async def test_cfg_list_returns_all_files(async_client):
    await _create_file(async_client, "cfg_a")
    await _create_file(async_client, "cfg_b")

    resp = await async_client.get("/api/v1/macros/cfg-files")
    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()]
    assert "cfg_a" in names
    assert "cfg_b" in names
