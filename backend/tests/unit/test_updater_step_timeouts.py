"""The in-app updater gives up on a step that hangs (TO-C7).

Every git, pip and npm step was awaited with no deadline. A network stall in
git fetch or npm install left the status at "downloading" or "installing" for
good, and every retry answered "Update already in progress" until Bambuddy was
restarted. Steps now have a deadline (git 300 s, pip and npm 900 s); the
process is killed and the update ends with an error, except the optional
frontend build, whose pre-built files ship with the release.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import updates as updates_module


def _fake_exec(hang_on: str, procs: list):
    async def create(*args, **kwargs):
        proc = MagicMock()
        proc.returncode = 0
        proc.killed = False

        def kill():
            proc.killed = True
            proc.returncode = -9

        proc.kill = kill
        proc.terminate = kill
        proc.wait = AsyncMock(return_value=-9)
        if hang_on in args or (hang_on == "npm install" and args[:2] == ("/usr/bin/npm", "install")):

            async def never():
                await asyncio.Event().wait()

            proc.communicate = never
        else:
            proc.communicate = AsyncMock(return_value=(b"git@github.com:maziggy/bambuddy.git\n", b""))
        proc.args = args
        procs.append(proc)
        return proc

    return create


async def _update(tmp_path, hang_on: str, *, npm: bool = False):
    app_dir = tmp_path / "app"
    (app_dir / "frontend").mkdir(parents=True)
    (app_dir / "requirements.txt").write_text("fastapi\n")
    procs: list = []

    def find(name):
        return "/usr/bin/npm" if name == "npm" and npm else ("/usr/bin/git" if name == "git" else None)

    with (
        patch.object(updates_module.settings, "app_dir", app_dir),
        patch.object(updates_module, "_find_executable", side_effect=find),
        patch.object(updates_module.asyncio, "create_subprocess_exec", side_effect=_fake_exec(hang_on, procs)),
        patch.object(updates_module, "GIT_STEP_TIMEOUT_SECONDS", 0.05),
        patch.object(updates_module, "INSTALL_STEP_TIMEOUT_SECONDS", 0.05),
    ):
        await asyncio.wait_for(updates_module._perform_update("v9.9.9"), 5)
    return updates_module._update_status, procs


@pytest.fixture(autouse=True)
def _idle_status():
    updates_module._update_status = {"status": "idle", "progress": 0, "message": "", "error": None}
    yield
    updates_module._update_status = {"status": "idle", "progress": 0, "message": "", "error": None}


@pytest.mark.asyncio
async def test_a_stalled_git_fetch_ends_the_update_with_an_error(tmp_path):
    status, procs = await _update(tmp_path, "fetch")

    assert status["status"] == "error"
    assert "timed out" in (status["error"] or "").lower()
    stuck = [p for p in procs if "fetch" in p.args]
    assert stuck and stuck[0].killed
    stuck[0].wait.assert_awaited()


@pytest.mark.asyncio
async def test_a_stalled_pip_install_ends_the_update_with_an_error(tmp_path):
    status, procs = await _update(tmp_path, "pip")

    assert status["status"] == "error"
    assert [p for p in procs if "pip" in p.args][0].killed
    # The code has already moved: it must not be started without its dependencies.
    assert "before restarting" in status["error"]


@pytest.mark.asyncio
async def test_a_stalled_frontend_build_falls_back_to_the_prebuilt_files(tmp_path):
    status, procs = await _update(tmp_path, "build", npm=True)

    assert status["status"] == "complete"
    assert [p for p in procs if "build" in p.args][0].killed


@pytest.mark.asyncio
async def test_after_a_timeout_the_update_can_be_tried_again(tmp_path):
    status, _ = await _update(tmp_path, "fetch")

    assert status["status"] not in ("downloading", "installing")


def test_the_deadlines_are_generous():
    """Slow links and a Raspberry Pi: git 5 minutes, pip and npm 15."""
    assert updates_module.GIT_STEP_TIMEOUT_SECONDS == 300
    assert updates_module.INSTALL_STEP_TIMEOUT_SECONDS == 900


@pytest.mark.asyncio
async def test_a_stalled_npm_install_falls_back_to_the_prebuilt_files(tmp_path):
    status, procs = await _update(tmp_path, "npm install", npm=True)

    assert status["status"] == "complete"
    assert [p for p in procs if p.args[:2] == ("/usr/bin/npm", "install")][0].killed


@pytest.mark.asyncio
async def test_a_step_in_its_own_session_is_killed_with_its_children(tmp_path):
    """npm and pip start children of their own: killing only the parent would
    leave them holding the pipes, and the wait would never end."""
    import os
    import sys

    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time;"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"open({str(child_pid_file)!r}, 'w').write(str(c.pid));"
        "time.sleep(60)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    for _ in range(100):
        if child_pid_file.exists() and child_pid_file.read_text():
            break
        await asyncio.sleep(0.05)
    child = int(child_pid_file.read_text())

    with pytest.raises(updates_module._StepTimedOut):
        await asyncio.wait_for(updates_module._communicate(process, 0.1, "npm install"), 15)

    assert process.returncode is not None
    for _ in range(100):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        os.kill(child, 9)
        pytest.fail("the step's child outlived it")


@pytest.mark.asyncio
async def test_a_stalled_git_reset_says_the_code_may_be_partly_in_place(tmp_path):
    status, _ = await _update(tmp_path, "reset")

    assert status["status"] == "error"
    assert "partly in place" in status["error"] and "before restarting" in status["error"]


@pytest.mark.asyncio
async def test_a_step_is_asked_to_stop_before_it_is_killed():
    """git removes its lock files on SIGTERM; SIGKILL would leave
    .git/index.lock behind and every retry would fail on it."""
    process = MagicMock(pid=None)
    process.communicate = asyncio.Event().wait
    process.wait = AsyncMock(return_value=-15)

    with pytest.raises(updates_module._StepTimedOut):
        await updates_module._communicate(process, 0.05, "git reset")

    process.terminate.assert_called_once()
    process.kill.assert_not_called()


@pytest.mark.asyncio
async def test_a_step_that_ignores_the_request_is_killed(monkeypatch):
    process = MagicMock(pid=None)
    process.communicate = asyncio.Event().wait
    waits = iter([TimeoutError(), -9])

    async def wait():
        result = next(waits)
        if isinstance(result, Exception):
            raise result
        return result

    process.wait = wait
    monkeypatch.setattr(updates_module, "TERMINATE_GRACE_SECONDS", 0.05)

    with pytest.raises(updates_module._StepTimedOut):
        await updates_module._communicate(process, 0.05, "npm install")

    process.terminate.assert_called_once()
    process.kill.assert_called_once()
