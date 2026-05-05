"""Integration tests for the macro webhook endpoint.

POST /api/v1/webhook/macro/{macro_id}/run

Uses async_client (real ASGI + in-memory SQLite).
get_api_key is overridden per-test so no real key hashing is needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.auth import get_api_key

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("backend.app.core.config.settings.macros_dir", str(d))
    return d


def _make_api_key(*, can_run_macros: bool = True):
    """Return a mock APIKey with the given permission flags."""
    key = MagicMock()
    key.can_run_macros = can_run_macros
    key.can_queue = False
    key.can_control_printer = False
    key.can_read_status = False
    key.printer_ids = None
    return key


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _create_file(client, name: str, content: str = "") -> dict:
    resp = await client.post("/api/v1/macros/cfg-files", json={"name": name, "content": content})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get_macro_id(client, name: str) -> int:
    macros = (await client.get("/api/v1/macros")).json()
    return next(m["id"] for m in macros if m["name"] == name)


# ── WT1: webhook-trigger macro accepted ───────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_run_accepted(async_client):
    from backend.app.main import app

    content = "[macro wh_ok]\ntrigger: webhook\nG28\n"
    await _create_file(async_client, "wh_ok", content)
    macro_id = await _get_macro_id(async_client, "wh_ok")

    good_key = _make_api_key(can_run_macros=True)
    app.dependency_overrides[get_api_key] = lambda: good_key

    try:
        with patch("backend.app.api.routes.webhook.asyncio.create_task"):
            resp = await async_client.post(
                f"/api/v1/webhook/macro/{macro_id}/run",
                json={},
            )
    finally:
        app.dependency_overrides.pop(get_api_key, None)

    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["status"] == "accepted"


# ── WT2: wrong trigger_type returns 400 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_wrong_trigger_type(async_client):
    from backend.app.main import app

    content = "[macro wh_manual]\nG28\n"
    await _create_file(async_client, "wh_manual", content)
    macro_id = await _get_macro_id(async_client, "wh_manual")

    good_key = _make_api_key(can_run_macros=True)
    app.dependency_overrides[get_api_key] = lambda: good_key

    try:
        resp = await async_client.post(
            f"/api/v1/webhook/macro/{macro_id}/run",
            json={},
        )
    finally:
        app.dependency_overrides.pop(get_api_key, None)

    assert resp.status_code == 400
    assert "webhook" in resp.json()["detail"].lower()


# ── WT3: macro not found returns 404 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_macro_not_found(async_client):
    from backend.app.main import app

    good_key = _make_api_key(can_run_macros=True)
    app.dependency_overrides[get_api_key] = lambda: good_key

    try:
        resp = await async_client.post("/api/v1/webhook/macro/99999/run", json={})
    finally:
        app.dependency_overrides.pop(get_api_key, None)

    assert resp.status_code == 404


# ── WT4: no API key returns 401 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_no_api_key_returns_401(async_client):
    content = "[macro wh_auth]\ntrigger: webhook\nG28\n"
    await _create_file(async_client, "wh_auth", content)
    macro_id = await _get_macro_id(async_client, "wh_auth")

    # No dependency override — real get_api_key will reject missing header
    resp = await async_client.post(
        f"/api/v1/webhook/macro/{macro_id}/run",
        json={},
    )
    assert resp.status_code in (401, 403)


# ── WT5: run_id is passed to runner (no orphan MacroRun) ──────────────────────


@pytest.mark.asyncio
async def test_webhook_run_id_passed_to_runner(async_client):
    """The webhook route must pass run_id to run_macro() so no second MacroRun is created."""
    from backend.app.main import app

    content = "[macro wh_runid]\ntrigger: webhook\nG28\n"
    await _create_file(async_client, "wh_runid", content)
    macro_id = await _get_macro_id(async_client, "wh_runid")

    good_key = _make_api_key(can_run_macros=True)
    app.dependency_overrides[get_api_key] = lambda: good_key

    mock_run = AsyncMock()

    try:
        with (
            patch("backend.app.api.routes.webhook.macro_runner.run_macro", mock_run),
            patch("backend.app.api.routes.webhook.asyncio.create_task") as mock_task,
        ):
            # Capture the coroutine argument and close it to avoid RuntimeWarning
            mock_task.side_effect = lambda coro: coro.close()
            resp = await async_client.post(
                f"/api/v1/webhook/macro/{macro_id}/run",
                json={},
            )
    finally:
        app.dependency_overrides.pop(get_api_key, None)

    assert resp.status_code == 200
    returned_run_id = resp.json()["run_id"]

    # create_task was called once; inspect the coroutine it received
    mock_task.assert_called_once()
    # The coroutine was built by calling macro_runner.run_macro(...)
    # Verify that mock_run was called with run_id matching the returned run_id
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("run_id") == returned_run_id
