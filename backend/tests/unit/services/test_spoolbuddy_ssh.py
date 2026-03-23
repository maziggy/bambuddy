"""Unit tests for SpoolBuddy SSH update service."""

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
    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path

        ssh_dir = tmp_path / "spoolbuddy" / "ssh"

        async def fake_keygen(*args, **kwargs):
            # Simulate ssh-keygen creating the files
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "id_ed25519").write_text("PRIVATE")
            (ssh_dir / "id_ed25519.pub").write_text("PUBLIC")
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_keygen) as mock_exec:
            priv, pub = await get_or_create_keypair()

            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert "ssh-keygen" in args
            assert "-t" in args
            assert "ed25519" in args


@pytest.mark.asyncio
async def test_get_or_create_keypair_raises_on_failure(tmp_path):
    with patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings:
        mock_settings.base_dir = tmp_path

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"keygen error"))
        mock_proc.returncode = 1

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(RuntimeError, match="ssh-keygen failed"),
        ):
            await get_or_create_keypair()


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


def test_detect_branch_from_git(tmp_path):
    (tmp_path / ".git").mkdir()
    with (
        patch("backend.app.services.spoolbuddy_ssh.settings") as mock_settings,
        patch("subprocess.run") as mock_run,
    ):
        mock_settings.base_dir = tmp_path
        mock_run.return_value = MagicMock(returncode=0, stdout="dev\n")
        assert detect_current_branch() == "dev"


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


@pytest.mark.asyncio
async def test_run_ssh_command_success(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        rc, stdout, stderr = await _run_ssh_command("10.0.0.1", "echo hello", key_file)

    assert rc == 0
    assert stdout == "hello\n"
    assert stderr == ""
    args = mock_exec.call_args[0]
    assert "spoolbuddy@10.0.0.1" in args
    assert "echo hello" in args
    assert "BatchMode=yes" in args


@pytest.mark.asyncio
async def test_run_ssh_command_failure(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Connection refused"))
    mock_proc.returncode = 255

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        rc, stdout, stderr = await _run_ssh_command("10.0.0.1", "echo hello", key_file)

    assert rc == 255
    assert "Connection refused" in stderr


@pytest.mark.asyncio
async def test_run_ssh_command_timeout(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("KEY")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.kill = MagicMock()

    async def fake_wait_for(coro, timeout):
        # Consume the coroutine to avoid warning
        coro.close()
        raise TimeoutError

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("backend.app.services.spoolbuddy_ssh.asyncio.wait_for", side_effect=fake_wait_for),
    ):
        rc, stdout, stderr = await _run_ssh_command("10.0.0.1", "sleep 999", key_file, timeout=1)

    assert rc == -1
    assert "timed out" in stderr
    mock_proc.kill.assert_called_once()


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
