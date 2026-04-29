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
        """Test non-Docker path - mock _perform_update + _discover_target_release
        to prevent side effects (network call to GitHub releases API + actual
        git/pip subprocesses)."""
        with (
            patch("backend.app.api.routes.updates._is_docker_environment", return_value=False),
            patch(
                "backend.app.api.routes.updates._discover_target_release",
                new_callable=AsyncMock,
                return_value="v9.9.9",
            ),
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

    def test_parse_github_remote_recognises_ssh_https_and_dotgit(self):
        """`_parse_github_remote` must accept the four canonical forms `git
        remote -v` prints; anything else returns None so callers can treat
        it as 'reset to expected URL'."""
        from backend.app.api.routes.updates import _parse_github_remote

        assert _parse_github_remote("git@github.com:maziggy/bambuddy.git") == (
            "maziggy",
            "bambuddy",
        )
        assert _parse_github_remote("git@github.com:maziggy/bambuddy") == (
            "maziggy",
            "bambuddy",
        )
        assert _parse_github_remote("https://github.com/maziggy/bambuddy.git") == (
            "maziggy",
            "bambuddy",
        )
        assert _parse_github_remote("https://github.com/maziggy/bambuddy") == (
            "maziggy",
            "bambuddy",
        )
        # Non-GitHub host → None (we don't claim ownership over arbitrary
        # forge URLs).
        assert _parse_github_remote("git@gitlab.com:maziggy/bambuddy.git") is None
        # Empty / malformed → None.
        assert _parse_github_remote("") is None
        assert _parse_github_remote("not-a-url") is None
        assert _parse_github_remote("https://github.com/maziggy") is None  # no /repo

    @pytest.mark.asyncio
    async def test_perform_update_preserves_ssh_origin_when_pointing_at_correct_repo(self, tmp_path):
        """Regression for the developer-checkout footgun: if origin already
        points at github.com/maziggy/bambuddy via SSH, the updater must
        leave it alone instead of clobbering it with HTTPS. Pre-fix, every
        Apply Update click rewrote `git@github.com:...` to `https://...`,
        breaking subsequent `git push` for any developer testing the
        upgrade flow against their own checkout."""
        from backend.app.api.routes import updates as updates_module

        app_dir = tmp_path / "app"
        data_dir = tmp_path / "app" / "data"
        app_dir.mkdir()
        data_dir.mkdir()
        (app_dir / "requirements.txt").write_text("fastapi\n")

        calls: list[dict] = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append({"args": args, "cwd": kwargs.get("cwd")})
            proc = MagicMock()
            # When the updater asks `git remote get-url origin`, return the
            # SSH URL. Every other subprocess returns successfully with no
            # output.
            if "get-url" in args and "origin" in args:
                proc.communicate = AsyncMock(return_value=(b"git@github.com:maziggy/bambuddy.git\n", b""))
            else:
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
            await updates_module._perform_update("v0.2.4b1")

        # The updater MUST NOT have run `git remote set-url origin <https>`
        # because origin already pointed at the right repo over SSH.
        set_url_calls = [c for c in calls if "set-url" in c["args"] and "origin" in c["args"]]
        assert not set_url_calls, (
            "Updater clobbered an SSH origin pointing at the correct repo. "
            "Captured set-url calls: " + repr([c["args"] for c in set_url_calls])
        )

    @pytest.mark.asyncio
    async def test_perform_update_resets_origin_when_pointing_elsewhere(self, tmp_path):
        """Defensive: if origin points at a fork or unrelated repo (or is
        missing), the updater should still rewrite it to the canonical
        HTTPS URL so subsequent fetch / reset works against the right
        repo. This is the original behaviour that the SSH-preservation
        fix above must NOT regress."""
        from backend.app.api.routes import updates as updates_module
        from backend.app.core.config import GITHUB_REPO

        app_dir = tmp_path / "app"
        data_dir = tmp_path / "app" / "data"
        app_dir.mkdir()
        data_dir.mkdir()
        (app_dir / "requirements.txt").write_text("fastapi\n")

        calls: list[dict] = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append({"args": args, "cwd": kwargs.get("cwd")})
            proc = MagicMock()
            # origin is set to a fork — must be rewritten.
            if "get-url" in args and "origin" in args:
                proc.communicate = AsyncMock(return_value=(b"git@github.com:somefork/bambuddy.git\n", b""))
            else:
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
            await updates_module._perform_update("v0.2.4b1")

        set_url_calls = [c for c in calls if "set-url" in c["args"] and "origin" in c["args"]]
        assert set_url_calls, "Updater must rewrite origin when it points at a fork."
        rewritten_to = set_url_calls[0]["args"][-1]
        assert rewritten_to == f"https://github.com/{GITHUB_REPO}.git", (
            f"Expected origin to be reset to canonical HTTPS URL; got: {rewritten_to}"
        )

    @pytest.mark.asyncio
    async def test_perform_update_resets_to_target_ref_not_hardcoded_main(self, tmp_path):
        """Regression for the hardcoded-`origin/main` limitation: the in-app
        updater must reset to the caller-supplied target ref (typically a
        release tag like `v0.2.4b1` discovered from the GitHub releases API)
        so beta releases that don't live on main can actually be installed.
        Pre-fix, `_perform_update` issued `git reset --hard origin/main`
        verbatim and silently no-op'd whenever the latest release wasn't on
        main — leaving a 0.2.3.x user clicking *Apply Update* stranded on
        0.2.3.x. Also asserts the fetch step uses `--tags` so a tag ref is
        actually resolvable post-fetch."""
        from backend.app.api.routes import updates as updates_module

        app_dir = tmp_path / "app"
        data_dir = tmp_path / "app" / "data"
        app_dir.mkdir()
        data_dir.mkdir()
        (app_dir / "requirements.txt").write_text("fastapi\n")

        calls: list[dict] = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append({"args": args, "cwd": kwargs.get("cwd")})
            proc = MagicMock()
            if "get-url" in args and "origin" in args:
                proc.communicate = AsyncMock(return_value=(b"git@github.com:maziggy/bambuddy.git\n", b""))
            else:
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
            await updates_module._perform_update("v0.2.4b1")

        # Reset target must be the caller-supplied ref, not "origin/main".
        reset_calls = [c for c in calls if "reset" in c["args"] and "--hard" in c["args"]]
        assert reset_calls, "git reset must be invoked"
        reset_target = reset_calls[0]["args"][-1]
        assert reset_target == "v0.2.4b1", (
            f"Expected reset target to be the caller-supplied ref 'v0.2.4b1'; "
            f"got {reset_target!r}. Regression to a hardcoded 'origin/main' "
            "would re-introduce the in-app-updater-can't-install-betas bug."
        )

        # Fetch must include --tags so v0.2.4b1 (a tag) is locally resolvable.
        fetch_calls = [c for c in calls if "fetch" in c["args"]]
        assert fetch_calls
        assert "--tags" in fetch_calls[0]["args"], (
            "Fetch must use --tags so release-tag refs (the production path "
            "for tag-based updates) are resolvable for the subsequent reset. "
            f"Captured fetch call: {fetch_calls[0]['args']}"
        )

    @pytest.mark.asyncio
    async def test_apply_update_passes_discovered_release_to_perform_update(self, async_client: AsyncClient):
        """End-to-end glue: the route handler calls `_discover_target_release`
        to pick the tag (respecting include_beta_updates), then schedules
        `_perform_update` with that tag — not with no arg, not with main."""
        from backend.app.api.routes import updates as updates_module

        captured_ref: list[str] = []

        async def fake_perform_update(target_ref):
            captured_ref.append(target_ref)

        async def fake_discover(_db):
            return "v0.2.4b1"

        with (
            patch.object(updates_module, "_is_docker_environment", return_value=False),
            patch.object(updates_module, "_perform_update", side_effect=fake_perform_update),
            patch.object(updates_module, "_discover_target_release", side_effect=fake_discover),
        ):
            response = await async_client.post("/api/v1/updates/apply")

        assert response.json()["success"] is True
        assert captured_ref == ["v0.2.4b1"], (
            f"apply_update must pass the discovered tag to _perform_update; captured invocations: {captured_ref}"
        )

    @pytest.mark.asyncio
    async def test_apply_update_returns_clear_error_when_no_release_resolves(self, async_client: AsyncClient):
        """If GitHub is unreachable or no release matches the user's channel,
        the route returns a useful error instead of silently kicking off an
        update that can't possibly land. Avoids the previous failure mode
        where in-app update appeared to succeed but did nothing."""
        from backend.app.api.routes import updates as updates_module

        async def fake_discover(_db):
            return None

        # The route guards against a concurrent update via the module-global
        # `_update_status` — reset it so a previous test that left the status
        # mid-flight doesn't short-circuit this one.
        updates_module._update_status = {"status": "idle", "progress": 0, "message": "", "error": None}

        with (
            patch.object(updates_module, "_is_docker_environment", return_value=False),
            patch.object(updates_module, "_discover_target_release", side_effect=fake_discover),
        ):
            response = await async_client.post("/api/v1/updates/apply")

        body = response.json()
        assert body["success"] is False
        assert "release" in body["message"].lower()

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
            await updates_module._perform_update("v0.2.4b1")

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
