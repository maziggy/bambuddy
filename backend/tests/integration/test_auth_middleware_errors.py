"""The auth middleware fails closed on its own probe, and only there.

The middleware asks the database whether authentication is on. If that probe
fails it must answer 503 and never fall through to "auth off" (the fail-open
bypass the probe used to have). Pre-fix the request itself also ran inside that
``try``, so with authentication off any error in any route was answered 503
"Authentication service temporarily unavailable" and logged as an auth-probe
failure, instead of surfacing as the 500 it is.
"""

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from backend.app.main import app

PROBE_PATH = "/api/v1/__test_auth_middleware"


@pytest.fixture
async def client(async_client) -> AsyncIterator[AsyncClient]:
    """A client that receives the 500 instead of re-raising it, and a test route.

    Depends on async_client for the test database wiring it sets up.
    """
    calls = {"n": 0}

    async def route(crash: bool = False):
        calls["n"] += 1
        if crash:
            raise RuntimeError("route failed")
        return {"ok": True}

    # In front of everything else: the SPA's catch-all route would match first.
    app.router.routes.insert(0, APIRoute(PROBE_PATH, route, methods=["GET"]))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as c:
            c.calls = calls  # type: ignore[attr-defined]
            yield c
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != PROBE_PATH]


class TestFailsClosedOnTheProbe:
    @pytest.mark.asyncio
    async def test_a_failing_probe_answers_503_and_never_reaches_the_route(self, client):
        with patch("backend.app.core.auth.is_auth_enabled", AsyncMock(side_effect=OSError("too many open files"))):
            response = await client.get(PROBE_PATH)

        assert response.status_code == 503
        assert response.json() == {"detail": "Authentication service temporarily unavailable"}
        assert client.calls["n"] == 0


class TestRouteErrorsAreNotAuthErrors:
    @pytest.mark.asyncio
    async def test_with_auth_off_a_route_error_is_a_500(self, client, caplog):
        with (
            patch("backend.app.core.auth.is_auth_enabled", AsyncMock(return_value=False)),
            caplog.at_level(logging.ERROR),
        ):
            response = await client.get(PROBE_PATH, params={"crash": "true"})

        assert response.status_code == 500
        assert "Authentication service" not in response.text
        assert "auth-probe" not in caplog.text
        assert client.calls["n"] == 1

    @pytest.mark.asyncio
    async def test_with_auth_off_a_working_route_still_answers(self, client):
        with patch("backend.app.core.auth.is_auth_enabled", AsyncMock(return_value=False)):
            response = await client.get(PROBE_PATH)

        assert response.status_code == 200
        assert response.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_with_auth_on_a_route_error_is_a_500(self, client):
        await client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "mwadmin", "admin_password": "AdminPass1!"},
        )
        login = await client.post("/api/v1/auth/login", json={"username": "mwadmin", "password": "AdminPass1!"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = await client.get(PROBE_PATH, params={"crash": "true"}, headers=headers)

        assert response.status_code == 500
        assert "Authentication service" not in response.text


class TestRouteErrorsReachTheLogFile:
    def test_uvicorns_error_log_goes_to_the_log_file_too(self):
        """An unhandled route error is logged by uvicorn on ``uvicorn.error``.

        Only ``uvicorn.access`` used to get the bambuddy.log handler, so such an
        error never reached the file a support package is built from.
        """
        from backend.app.main import _attach_file_handler_to_uvicorn

        handler = logging.NullHandler()
        try:
            _attach_file_handler_to_uvicorn(handler)
            assert handler in logging.getLogger("uvicorn.error").handlers
            assert handler in logging.getLogger("uvicorn.access").handlers
        finally:
            for name in ("uvicorn.error", "uvicorn.access"):
                logger = logging.getLogger(name)
                if handler in logger.handlers:
                    logger.removeHandler(handler)

    def test_the_app_wires_uvicorns_error_log_to_bambuddy_log(self, tmp_path):
        """End to end: with file logging on (the default), importing the app
        attaches bambuddy.log to uvicorn.error, and a record there lands in it."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        env = {
            **os.environ,
            "LOG_TO_FILE": "true",
            "LOG_DIR": str(tmp_path),
            "DATA_DIR": str(tmp_path),
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
            "PYTHONPATH": str(repo),
        }
        # uvicorn configures its loggers before it imports the app, and its
        # config stops uvicorn.error short of the root logger; without that the
        # record would reach the root's file handler whatever the app does.
        script = (
            "import logging, logging.config\n"
            "from uvicorn.config import LOGGING_CONFIG\n"
            "logging.config.dictConfig(LOGGING_CONFIG)\n"
            "import backend.app.main\n"
            "logging.getLogger('uvicorn.error').error('Exception in ASGI application: marker-7f3a')\n"
            "for h in logging.getLogger().handlers: h.flush()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert "marker-7f3a" in (tmp_path / "bambuddy.log").read_text(encoding="utf-8")


class TestTokenCheckErrors:
    """With login on, a database error while checking a token is not a bad token.

    It used to answer 401 "Invalid token", and the frontend treats a 401 from
    /auth/me as final: it signs the user out and deletes the token. It now
    fails closed the way the probe does, with a logged 503 the frontend retries.
    """

    @staticmethod
    async def _login(client) -> dict[str, str]:
        await client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "tkadmin", "admin_password": "AdminPass1!"},
        )
        login = await client.post("/api/v1/auth/login", json={"username": "tkadmin", "password": "AdminPass1!"})
        assert login.status_code == 200, login.text
        return {"Authorization": f"Bearer {login.json()['access_token']}"}

    @pytest.mark.asyncio
    async def test_a_database_error_is_a_logged_503_not_a_401(self, client, caplog):
        headers = await self._login(client)
        with (
            patch("backend.app.core.auth.is_jti_revoked", AsyncMock(side_effect=OSError("pool timed out"))),
            caplog.at_level(logging.ERROR),
        ):
            response = await client.get(PROBE_PATH, headers=headers)

        assert response.status_code == 503
        assert response.json() == {"detail": "Authentication service temporarily unavailable"}
        assert client.calls["n"] == 0
        assert "pool timed out" in caplog.text

    @pytest.mark.asyncio
    async def test_a_bad_token_is_still_a_401(self, client):
        await self._login(client)

        response = await client.get(PROBE_PATH, headers={"Authorization": "Bearer not-a-jwt"})

        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid token"}
        assert client.calls["n"] == 0

    @pytest.mark.asyncio
    async def test_a_valid_token_still_reaches_the_route(self, client):
        headers = await self._login(client)

        response = await client.get(PROBE_PATH, headers=headers)

        assert response.status_code == 200
        assert client.calls["n"] == 1
