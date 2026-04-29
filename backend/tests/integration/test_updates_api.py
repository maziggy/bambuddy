"""Integration tests for Updates API endpoints."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


class TestUpdatesAPI:
    @pytest.mark.asyncio
    async def test_get_version(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/updates/version")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_apply_update_docker_rejection(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.updates._is_docker_environment", return_value=True):
            response = await async_client.post("/api/v1/updates/apply")
        result = response.json()
        assert result["success"] is False
        assert result["is_docker"] is True

    @pytest.mark.asyncio
    async def test_apply_update_non_docker(self, async_client: AsyncClient):
        """Test non-Docker path - mock _perform_update to prevent side effects."""
        with (
            patch("backend.app.api.routes.updates._is_docker_environment", return_value=False),
            patch("backend.app.api.routes.updates._perform_update", new_callable=AsyncMock),
        ):
            response = await async_client.post("/api/v1/updates/apply")
        assert response.json()["success"] is True

    def test_is_docker_with_dockerenv(self):
        from backend.app.api.routes.updates import _is_docker_environment

        with patch("os.path.exists", return_value=True):
            assert _is_docker_environment() is True

    def test_parse_version(self):
        from backend.app.api.routes.updates import parse_version

        assert parse_version("0.1.5")[:3] == (0, 1, 5)

    def test_is_newer_version(self):
        from backend.app.api.routes.updates import is_newer_version

        assert is_newer_version("0.1.5", "0.1.5b7") is True

    @pytest.mark.asyncio
    async def test_perform_update_runs_pip_in_app_dir_not_data_dir(self, tmp_path):
        """Native install: `requirements.txt` lives at INSTALL_PATH (the source-
        code dir), NOT at DATA_DIR (where systemd sets DATA_DIR=INSTALL_PATH/data).
        Pre-fix, the updater ran `pip install -r requirements.txt` with
        `cwd=settings.base_dir`, which on a native install resolves to the data
        dir — `requirements.txt` isn't there and pip fails with `Could not open
        requirements file`. The fix: pip's cwd is `settings.app_dir` (the source
        tree) so it can actually find the file.

        This test mocks every subprocess so it can capture the cwd of each call
        and assert that the pip step runs in app_dir while git steps continue
        to run in base_dir (their existing behaviour — git walks up to find
        `.git` so that path keeps working)."""
        from backend.app.api.routes import updates as updates_module

        # Set up fake install layout: app_dir has requirements.txt, data_dir is
        # a sibling (mirroring `INSTALL_PATH=/opt/bambuddy`, `DATA_DIR=/opt/bambuddy/data`).
        app_dir = tmp_path / "app"
        data_dir = tmp_path / "app" / "data"
        app_dir.mkdir()
        data_dir.mkdir()
        (app_dir / "requirements.txt").write_text("fastapi\n")

        # Capture every subprocess call's cwd + the executable token.
        calls: list[dict] = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append({"args": args, "cwd": kwargs.get("cwd")})
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            return proc

        with (
            patch.object(updates_module.settings, "base_dir", data_dir),
            patch.object(updates_module.settings, "app_dir", app_dir),
            patch.object(updates_module, "_find_executable", return_value="/usr/bin/git"),
            patch.object(
                updates_module.asyncio,
                "create_subprocess_exec",
                side_effect=fake_create_subprocess_exec,
            ),
        ):
            await updates_module._perform_update()

        # Find the pip invocation (sys.executable + "-m" + "pip" + "install").
        pip_calls = [c for c in calls if "pip" in c["args"] and "install" in c["args"]]
        assert pip_calls, "pip install was never invoked. Captured: " + repr([c["args"] for c in calls])
        pip_cwd = pip_calls[0]["cwd"]
        assert pip_cwd == str(app_dir), (
            f"pip install must run in app_dir ({app_dir}) so it finds "
            f"requirements.txt; got cwd={pip_cwd}. Regression to base_dir "
            f"breaks every native-install upgrade."
        )

        # Sanity check: the requirements.txt that pip would read actually exists
        # at the captured cwd. If this fails the cwd is wrong even if it isn't
        # base_dir — useful diagnostic if someone refactors path handling.
        assert (Path(pip_cwd) / "requirements.txt").exists()
