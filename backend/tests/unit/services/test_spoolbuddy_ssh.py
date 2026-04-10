"""Unit tests for SpoolBuddy SSH update service."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.spoolbuddy_ssh import (
    _get_ssh_key_dir,
    _run_ssh_command,
    detect_current_branch,
    get_or_create_keypair,
    get_public_key,
    perform_ssh_update,
)

# -- _get_ssh_key_dir ---------------------------------------------------------


def test_get_ssh_key_dir_creates_directory(tmp_path):
    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path
        key_dir = _get_ssh_key_dir()
        assert key_dir == tmp_path / "spoolbuddy" / "ssh"
        assert key_dir.exists()


def test_get_ssh_key_dir_returns_existing(tmp_path):
    ssh_dir = tmp_path / "spoolbuddy" / "ssh"
    ssh_dir.mkdir(parents=True)
    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path
        assert _get_ssh_key_dir() == ssh_dir


# -- get_or_create_keypair -----------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_keypair_returns_existing(tmp_path):
    ssh_dir = tmp_path / "spoolbuddy" / "ssh"
    ssh_dir.mkdir(parents=True)
    priv = ssh_dir / "id_ed25519"
    pub = ssh_dir / "id_ed25519.pub"
    priv.write_text("PRIVATE")
    pub.write_text("PUBLIC")

    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path
        result = await get_or_create_keypair()
        assert result == (priv, pub)


@pytest.mark.asyncio
async def test_get_or_create_keypair_generates_new(tmp_path):
    """Key generation runs in-process via `cryptography` — no ssh-keygen subprocess.

    This matters in Docker: when the container runs under an arbitrary PUID
    that isn't in /etc/passwd, `ssh-keygen` aborts with "no user exists for uid
    <N>". Generating the keypair in-process avoids the getpwuid() lookup.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path

        priv, pub = await get_or_create_keypair()

        assert priv.exists()
        assert pub.exists()
        # Private key permissions — no world/group access
        assert (priv.stat().st_mode & 0o077) == 0

        # Public key is a valid OpenSSH ed25519 key with our comment
        pub_text = pub.read_text()
        assert pub_text.startswith("ssh-ed25519 ")
        assert pub_text.rstrip().endswith("bambuddy-spoolbuddy")

        # Private key is a valid OpenSSH-format ed25519 key we can load back
        loaded = serialization.load_ssh_private_key(priv.read_bytes(), password=None)
        assert isinstance(loaded, ed25519.Ed25519PrivateKey)


@pytest.mark.asyncio
async def test_get_or_create_keypair_does_not_shell_out(tmp_path):
    """Regression guard: must not invoke any subprocess (fixes Docker PUID bug)."""
    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch("asyncio.create_subprocess_exec") as mock_exec,
    ):
        mock_settings.base_dir = tmp_path
        await get_or_create_keypair()
        mock_exec.assert_not_called()


# -- get_public_key ------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_public_key(tmp_path):
    ssh_dir = tmp_path / "spoolbuddy" / "ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_ed25519").write_text("PRIVATE")
    (ssh_dir / "id_ed25519.pub").write_text("ssh-ed25519 AAAA bambuddy-spoolbuddy\n")

    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path
        key = await get_public_key()
        assert key == "ssh-ed25519 AAAA bambuddy-spoolbuddy"


# -- detect_current_branch ----------------------------------------------------


def test_detect_branch_from_git_head(tmp_path):
    """Read branch directly from .git/HEAD — no subprocess."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/dev\n")

    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch("asyncio.create_subprocess_exec") as mock_exec,
        patch("subprocess.run") as mock_run,
    ):
        mock_settings.base_dir = tmp_path
        assert detect_current_branch() == "dev"
        # Regression guard: must not shell out (fails with getpwuid under
        # arbitrary Docker PUIDs if ever reintroduced).
        mock_exec.assert_not_called()
        mock_run.assert_not_called()


def test_detect_branch_worktree_gitdir_file(tmp_path):
    """Git worktrees store a `gitdir:` pointer instead of a dir — follow it."""
    real_git_dir = tmp_path / "real-git"
    real_git_dir.mkdir()
    (real_git_dir / "HEAD").write_text("ref: refs/heads/feature-x\n")
    (tmp_path / ".git").write_text(f"gitdir: {real_git_dir}\n")

    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path
        assert detect_current_branch() == "feature-x"


def test_detect_branch_detached_head_falls_back(tmp_path):
    """Detached HEAD (raw commit hash) should fall through to the env var."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("deadbeef1234\n")

    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch.dict(os.environ, {"GIT_BRANCH": "release"}),
    ):
        mock_settings.base_dir = tmp_path
        assert detect_current_branch() == "release"


def test_detect_branch_env_fallback(tmp_path):
    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch.dict(os.environ, {"GIT_BRANCH": "staging"}),
    ):
        mock_settings.base_dir = tmp_path
        assert detect_current_branch() == "staging"


def test_detect_branch_default_main(tmp_path):
    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch.dict(os.environ, {}, clear=True),
    ):
        mock_settings.base_dir = tmp_path
        # Remove GIT_BRANCH if present
        os.environ.pop("GIT_BRANCH", None)
        assert detect_current_branch() == "main"


# -- _run_ssh_command ----------------------------------------------------------
#
# _run_ssh_command uses asyncssh (pure Python) rather than the OpenSSH `ssh`
# binary. Both `ssh` and `ssh-keygen` call getpwuid(getuid()) during startup
# and abort with "No user exists for uid <N>" when the container runs under
# an arbitrary PUID that is not listed in /etc/passwd — asyncssh avoids the
# subprocess entirely.


@pytest.mark.asyncio
async def test_run_ssh_command_success(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    mock_result = MagicMock()
    mock_result.stdout = "hello\n"
    mock_result.stderr = ""
    mock_result.exit_status = 0

    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.app.services.spoolbuddy_ssh.asyncssh.connect", return_value=mock_conn) as mock_connect:
        rc, stdout, stderr = await _run_ssh_command("10.0.0.1", "echo hello", key_file)

    assert rc == 0
    assert stdout == "hello\n"
    assert stderr == ""
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["host"] == "10.0.0.1"
    assert kwargs["username"] == "spoolbuddy"
    assert kwargs["client_keys"] == [str(key_file)]
    # Host-key verification is disabled (equivalent to StrictHostKeyChecking=no)
    assert kwargs["known_hosts"] is None
    # ~/.ssh/config loading is disabled — HOME may not resolve under arbitrary
    # Docker PUIDs.
    assert kwargs["config"] == []
    mock_conn.run.assert_awaited_once()
    run_args = mock_conn.run.call_args
    assert run_args.args[0] == "echo hello"
    # check=False — we handle non-zero exit codes ourselves
    assert run_args.kwargs.get("check") is False


@pytest.mark.asyncio
async def test_run_ssh_command_no_subprocess(tmp_path):
    """Regression guard: _run_ssh_command must not spawn any subprocess.

    The whole point of switching to asyncssh is to avoid `ssh`/`ssh-keygen`
    calling getpwuid() inside Docker containers with arbitrary PUIDs.
    """
    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    mock_result = MagicMock()
    mock_result.stdout = ""
    mock_result.stderr = ""
    mock_result.exit_status = 0

    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.app.services.spoolbuddy_ssh.asyncssh.connect", return_value=mock_conn),
        patch("asyncio.create_subprocess_exec") as mock_exec,
    ):
        await _run_ssh_command("10.0.0.1", "echo hi", key_file)

    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_run_ssh_command_connection_failure(tmp_path):
    """Connection errors should surface as rc=255 with the asyncssh message."""
    import asyncssh

    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    with patch(
        "backend.app.services.spoolbuddy_ssh.asyncssh.connect",
        side_effect=asyncssh.Error(code=0, reason="Connection refused"),
    ):
        rc, stdout, stderr = await _run_ssh_command("10.0.0.1", "echo hello", key_file)

    assert rc == 255
    assert stdout == ""
    assert "Connection refused" in stderr


@pytest.mark.asyncio
async def test_run_ssh_command_os_error(tmp_path):
    """OS-level connection errors (DNS, route) also map to rc=255."""
    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    with patch(
        "backend.app.services.spoolbuddy_ssh.asyncssh.connect",
        side_effect=OSError("Network is unreachable"),
    ):
        rc, _, stderr = await _run_ssh_command("10.0.0.1", "echo hello", key_file)

    assert rc == 255
    assert "Network is unreachable" in stderr


@pytest.mark.asyncio
async def test_run_ssh_command_timeout(tmp_path):
    """asyncio.timeout should convert long-running commands into rc=-1."""
    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    # asyncssh.connect() returns a _ConnectionManager synchronously; the hang
    # must happen inside __aenter__ so the surrounding asyncio.timeout can
    # cancel it.
    mock_conn = AsyncMock()

    async def hang_enter():
        await asyncio.sleep(10)

    mock_conn.__aenter__ = AsyncMock(side_effect=hang_enter)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.app.services.spoolbuddy_ssh.asyncssh.connect", return_value=mock_conn):
        rc, _, stderr = await _run_ssh_command("10.0.0.1", "sleep 999", key_file, timeout=0.05)

    assert rc == -1
    assert "timed out" in stderr


# -- perform_ssh_update --------------------------------------------------------


def _make_update_mocks(tmp_path):
    """Create common mocks for perform_ssh_update tests."""
    mock_db_device = MagicMock()
    mock_db_device.update_status = None
    mock_db_device.update_message = None
    mock_db_device.pending_command = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_device

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_ws = MagicMock()
    mock_ws.broadcast = AsyncMock()

    return mock_db_device, mock_ctx, mock_ws


@pytest.mark.asyncio
async def test_perform_ssh_update_success(tmp_path):
    """Full update flow: all SSH commands succeed."""
    ssh_dir = tmp_path / "spoolbuddy" / "ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_ed25519").write_text("PRIVATE")
    (ssh_dir / "id_ed25519.pub").write_text("PUBLIC")

    ssh_calls = []

    async def mock_ssh(ip, cmd, key, timeout=60):
        ssh_calls.append(cmd)
        return 0, "ok", ""

    _, mock_ctx, mock_ws = _make_update_mocks(tmp_path)

    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch("backend.app.services.spoolbuddy_ssh._run_ssh_command", side_effect=mock_ssh),
        patch("backend.app.services.spoolbuddy_ssh.detect_current_branch", return_value="dev"),
        patch("backend.app.core.database.async_session", return_value=mock_ctx),
        patch("backend.app.api.routes.spoolbuddy.ws_manager", mock_ws),
    ):
        mock_settings.base_dir = tmp_path
        await perform_ssh_update("sb-test", "10.0.0.1")

    # Should have run: echo ok, git fetch, git checkout+reset, pip install,
    # systemctl restart, find (SW cleanup), systemctl restart getty
    assert len(ssh_calls) == 7
    assert "echo ok" in ssh_calls[0]
    assert "fetch" in ssh_calls[1]
    assert "checkout" in ssh_calls[2]
    assert "pip" in ssh_calls[3]
    assert "spoolbuddy.service" in ssh_calls[4]
    assert "Service Worker" in ssh_calls[5]
    assert "getty" in ssh_calls[6]

    assert mock_ws.broadcast.call_count >= 4


@pytest.mark.asyncio
async def test_perform_ssh_update_ssh_failure(tmp_path):
    """SSH connectivity check fails — should set error status."""
    ssh_dir = tmp_path / "spoolbuddy" / "ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_ed25519").write_text("PRIVATE")
    (ssh_dir / "id_ed25519.pub").write_text("PUBLIC")

    async def mock_ssh(ip, cmd, key, timeout=60):
        if "echo ok" in cmd:
            return 255, "", "Connection refused"
        return 0, "", ""

    mock_device, mock_ctx, mock_ws = _make_update_mocks(tmp_path)

    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch("backend.app.services.spoolbuddy_ssh._run_ssh_command", side_effect=mock_ssh),
        patch("backend.app.services.spoolbuddy_ssh.detect_current_branch", return_value="main"),
        patch("backend.app.core.database.async_session", return_value=mock_ctx),
        patch("backend.app.api.routes.spoolbuddy.ws_manager", mock_ws),
    ):
        mock_settings.base_dir = tmp_path
        await perform_ssh_update("sb-test", "10.0.0.1")

    # Should broadcast error status
    error_broadcasts = [c for c in mock_ws.broadcast.call_args_list if c[0][0].get("update_status") == "error"]
    assert len(error_broadcasts) >= 1
    assert "SSH connection failed" in error_broadcasts[0][0][0]["update_message"]


@pytest.mark.asyncio
async def test_perform_ssh_update_git_fetch_failure(tmp_path):
    """Git fetch fails — should set error and stop."""
    ssh_dir = tmp_path / "spoolbuddy" / "ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_ed25519").write_text("PRIVATE")
    (ssh_dir / "id_ed25519.pub").write_text("PUBLIC")

    ssh_calls = []

    async def mock_ssh(ip, cmd, key, timeout=60):
        ssh_calls.append(cmd)
        if "fetch" in cmd:
            return 1, "", "fatal: could not read from remote"
        return 0, "ok", ""

    _, mock_ctx, mock_ws = _make_update_mocks(tmp_path)

    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch("backend.app.services.spoolbuddy_ssh._run_ssh_command", side_effect=mock_ssh),
        patch("backend.app.services.spoolbuddy_ssh.detect_current_branch", return_value="main"),
        patch("backend.app.core.database.async_session", return_value=mock_ctx),
        patch("backend.app.api.routes.spoolbuddy.ws_manager", mock_ws),
    ):
        mock_settings.base_dir = tmp_path
        await perform_ssh_update("sb-test", "10.0.0.1")

    # Should stop after git fetch — no checkout, pip, restart
    assert len(ssh_calls) == 2  # echo ok + git fetch
    assert not any("checkout" in c for c in ssh_calls)
