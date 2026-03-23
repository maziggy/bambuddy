"""SSH-based update service for SpoolBuddy devices.

Instead of the daemon updating itself (fragile: permission issues, self-modifying
code, hardcoded branch), Bambuddy SSHes into the SpoolBuddy Pi and drives the
update remotely: git fetch/checkout, pip install, systemctl restart.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

SSH_USER = "spoolbuddy"
DEFAULT_INSTALL_PATH = "/opt/bambuddy"


def _get_ssh_key_dir() -> Path:
    """Return (and create if needed) the directory for SpoolBuddy SSH keys."""
    key_dir = settings.base_dir / "spoolbuddy" / "ssh"
    if not key_dir.exists():
        key_dir.mkdir(mode=0o700, parents=True)
    return key_dir


async def get_or_create_keypair() -> tuple[Path, Path]:
    """Return (private_key_path, public_key_path), generating if missing."""
    key_dir = _get_ssh_key_dir()
    private_key = key_dir / "id_ed25519"
    public_key = key_dir / "id_ed25519.pub"

    if private_key.exists() and public_key.exists():
        return private_key, public_key

    logger.info("Generating SSH keypair for SpoolBuddy updates")
    proc = await asyncio.create_subprocess_exec(
        "ssh-keygen",
        "-t",
        "ed25519",
        "-f",
        str(private_key),
        "-N",
        "",  # no passphrase
        "-C",
        "bambuddy-spoolbuddy",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ssh-keygen failed: {stderr.decode()[:200]}")

    private_key.chmod(0o600)
    logger.info("SSH keypair generated at %s", key_dir)
    return private_key, public_key


async def get_public_key() -> str:
    """Return the SSH public key content for pairing."""
    _, public_key = await get_or_create_keypair()
    return public_key.read_text().strip()


def detect_current_branch() -> str:
    """Detect the git branch Bambuddy is running on.

    For native installs, reads from the .git directory.
    For Docker (no .git), falls back to GIT_BRANCH env var, then "main".
    """
    git_dir = settings.base_dir / ".git"
    if git_dir.exists():
        git_path = shutil.which("git") or "/usr/bin/git"
        try:
            import subprocess

            result = subprocess.run(
                [git_path, "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(settings.base_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

    return os.environ.get("GIT_BRANCH", "main")


async def _run_ssh_command(
    ip: str,
    command: str,
    private_key: Path,
    timeout: int = 60,
) -> tuple[int, str, str]:
    """Execute a command on a SpoolBuddy device via SSH.

    Returns (returncode, stdout, stderr).
    """
    ssh_path = shutil.which("ssh") or "/usr/bin/ssh"
    proc = await asyncio.create_subprocess_exec(
        ssh_path,
        "-i",
        str(private_key),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "BatchMode=yes",
        "-o",
        "LogLevel=ERROR",
        f"{SSH_USER}@{ip}",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", "SSH command timed out"

    return proc.returncode, stdout.decode(), stderr.decode()


async def perform_ssh_update(device_id: str, ip_address: str, install_path: str | None = None) -> None:
    """SSH into a SpoolBuddy device and update it to match Bambuddy's branch.

    Updates device.update_status/update_message in the DB and broadcasts
    progress via WebSocket at each step.
    """
    from sqlalchemy import select

    from backend.app.api.routes.spoolbuddy import ws_manager
    from backend.app.core.database import async_session
    from backend.app.models.spoolbuddy_device import SpoolBuddyDevice

    install_path = install_path or DEFAULT_INSTALL_PATH
    branch = detect_current_branch()

    async def _update_progress(status: str, message: str) -> None:
        """Update device status in DB and broadcast via WebSocket."""
        async with async_session() as db:
            result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
            device = result.scalar_one_or_none()
            if device:
                device.update_status = status
                device.update_message = message[:255] if message else None
                if status in ("complete", "error"):
                    device.pending_command = None
                await db.commit()

        await ws_manager.broadcast(
            {
                "type": "spoolbuddy_update",
                "device_id": device_id,
                "update_status": status,
                "update_message": message[:255] if message else None,
            }
        )

    try:
        private_key, _ = await get_or_create_keypair()

        # Step 1: Test SSH connectivity
        await _update_progress("updating", "Connecting via SSH...")
        rc, _, stderr = await _run_ssh_command(ip_address, "echo ok", private_key)
        if rc != 0:
            await _update_progress("error", f"SSH connection failed: {stderr[:200]}")
            return

        # Step 2: Git fetch
        await _update_progress("updating", f"Fetching latest code (branch: {branch})...")
        rc, _, stderr = await _run_ssh_command(
            ip_address,
            f"cd {install_path} && git -c safe.directory={install_path} fetch origin {branch}",
            private_key,
            timeout=120,
        )
        if rc != 0:
            await _update_progress("error", f"git fetch failed: {stderr[:200]}")
            return

        # Step 3: Git checkout + reset
        await _update_progress("updating", "Applying update...")
        rc, _, stderr = await _run_ssh_command(
            ip_address,
            f"cd {install_path} && git -c safe.directory={install_path} checkout {branch} "
            f"&& git -c safe.directory={install_path} reset --hard origin/{branch}",
            private_key,
        )
        if rc != 0:
            await _update_progress("error", f"git checkout/reset failed: {stderr[:200]}")
            return

        # Step 4: Install dependencies
        await _update_progress("updating", "Installing dependencies...")
        venv_pip = f"{install_path}/spoolbuddy/venv/bin/pip"
        rc, _, stderr = await _run_ssh_command(
            ip_address,
            f"{venv_pip} install --upgrade spidev gpiod smbus2 httpx 2>&1",
            private_key,
            timeout=120,
        )
        if rc != 0:
            logger.warning("SpoolBuddy %s: pip install returned non-zero (continuing): %s", device_id, stderr[:200])

        # Step 5: Restart daemon
        await _update_progress("updating", "Restarting daemon...")
        rc, _, stderr = await _run_ssh_command(
            ip_address,
            "sudo /usr/bin/systemctl restart spoolbuddy.service",
            private_key,
        )
        if rc != 0:
            await _update_progress("error", f"Service restart failed: {stderr[:200]}")
            return

        # Step 6: Restart kiosk browser to load updated frontend
        rc, _, stderr = await _run_ssh_command(
            ip_address,
            "sudo /usr/bin/systemctl restart getty@tty1.service",
            private_key,
        )
        if rc != 0:
            logger.warning("SpoolBuddy %s: kiosk restart failed (non-fatal): %s", device_id, stderr[:200])

        logger.info("SpoolBuddy %s: SSH update complete (branch=%s)", device_id, branch)

    except Exception as e:
        logger.error("SpoolBuddy %s: SSH update failed: %s", device_id, e)
        await _update_progress("error", f"Update failed: {str(e)[:200]}")
