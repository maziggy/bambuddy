"""SSH-based update service for SpoolBuddy devices.

Instead of the daemon updating itself (fragile: permission issues, self-modifying
code, hardcoded branch), Bambuddy SSHes into the SpoolBuddy Pi and drives the
update remotely: git fetch/checkout, pip install, systemctl restart.

Uses `asyncssh` (pure-Python async SSH client) rather than shelling out to the
OpenSSH `ssh` binary. The subprocess approach fails in Docker: both `ssh` and
`ssh-keygen` call `getpwuid(getuid())` during startup and abort with
"No user exists for uid <N>" when the container runs under a UID that is not
listed in /etc/passwd (e.g. PUID=1000 on python:3.13-slim, which only has
entries for root). asyncssh does all of its work in-process.
"""

import asyncio
import logging
import os
import shlex
from pathlib import Path

import asyncssh
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

SSH_USER = "spoolbuddy"
DEFAULT_INSTALL_PATH = "/opt/bambuddy"

# Project root — where the `.git` directory lives for native installs and for
# Docker containers that bind-mount the repo. This is intentionally distinct
# from `settings.base_dir`, which points at the persistent *data* directory
# (e.g. `DATA_DIR=/app/data` in Docker) and therefore never contains `.git`.
# `backend/app/services/spoolbuddy_ssh.py` → parents[3] = project root.
_APP_DIR = Path(__file__).resolve().parents[3]

# Note for Docker: asyncssh.connect() internally calls getpass.getuser() to
# resolve the *local* username for ~/.ssh/config host matching. Under an
# arbitrary PUID with no /etc/passwd entry this would raise OSError. The
# Dockerfile sets LOGNAME/USER/HOME so getpass.getuser() succeeds via env-var
# lookup before ever touching the passwd database.


def _get_ssh_key_dir() -> Path:
    """Return (and create if needed) the directory for SpoolBuddy SSH keys."""
    key_dir = settings.base_dir / "spoolbuddy" / "ssh"
    if not key_dir.exists():
        key_dir.mkdir(mode=0o700, parents=True)
    return key_dir


async def get_or_create_keypair() -> tuple[Path, Path]:
    """Return (private_key_path, public_key_path), generating if missing.

    Uses the in-process `cryptography` library instead of shelling out to
    `ssh-keygen`. The subprocess approach fails inside Docker containers when
    the image runs under an arbitrary UID (e.g. PUID=1001) that is not listed
    in /etc/passwd — `ssh-keygen` calls `getpwuid()` for the current user's
    home directory and aborts with "no user exists for uid <N>".
    """
    key_dir = _get_ssh_key_dir()
    private_key = key_dir / "id_ed25519"
    public_key = key_dir / "id_ed25519.pub"

    if private_key.exists() and public_key.exists():
        return private_key, public_key

    logger.info("Generating SSH keypair for SpoolBuddy updates")
    priv_obj = ed25519.Ed25519PrivateKey.generate()
    pub_obj = priv_obj.public_key()

    private_bytes = priv_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = pub_obj.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    # OpenSSH public format has no comment field by default; append one to match
    # the previous ssh-keygen output so the authorized_keys line is identifiable.
    public_line = public_bytes + b" bambuddy-spoolbuddy\n"

    private_key.write_bytes(private_bytes)
    private_key.chmod(0o600)
    public_key.write_bytes(public_line)

    logger.info("SSH keypair generated at %s", key_dir)
    return private_key, public_key


async def get_public_key() -> str:
    """Return the SSH public key content for pairing."""
    _, public_key = await get_or_create_keypair()
    return public_key.read_text().strip()


def detect_current_branch() -> str:
    """Detect the git branch Bambuddy is running on.

    Reads `.git/HEAD` directly from the application root (``_APP_DIR``) rather
    than shelling out to `git`. The application root is deliberately distinct
    from ``settings.base_dir``: in Docker, ``base_dir`` points at the data
    volume (``/app/data``) which never contains ``.git``, while the repo is
    bind-mounted (or COPYd) to ``/app``. This works for native installs,
    bare Docker containers (no ``.git`` — fall through to the env var), and
    Docker containers that bind-mount the repo (``.git`` is present, no
    ``git`` binary required, and no ``getpwuid()`` call that could fail under
    an arbitrary PUID).

    Fallback order: ``.git/HEAD`` → ``GIT_BRANCH`` env var → ``"main"``.
    """
    git_path = _APP_DIR / ".git"
    try:
        if git_path.exists():
            # Git worktrees use a file containing `gitdir: <path>` instead of
            # a directory — follow the pointer.
            if git_path.is_file():
                content = git_path.read_text(encoding="utf-8").strip()
                if content.startswith("gitdir:"):
                    git_path = (_APP_DIR / content.removeprefix("gitdir:").strip()).resolve()

            head_file = git_path / "HEAD"
            if head_file.is_file():
                head = head_file.read_text(encoding="utf-8").strip()
                # Normal case: `ref: refs/heads/<branch>`.
                # Detached HEAD stores a raw commit hash — fall through to env var.
                if head.startswith("ref: refs/heads/"):
                    return head.removeprefix("ref: refs/heads/").strip()
    except OSError as exc:
        logger.debug("Could not read .git/HEAD, falling back: %s", exc)

    return os.environ.get("GIT_BRANCH", "main")


async def _run_ssh_command(
    ip: str,
    command: str,
    private_key: Path,
    *,
    known_hosts: "asyncssh.SSHKnownHosts | None" = None,
    timeout: int = 60,
) -> tuple[int, str, str, str | None]:
    """Execute a command on a SpoolBuddy device via SSH.

    Uses asyncssh rather than the OpenSSH `ssh` binary — see module docstring
    for the Docker/PUID rationale.

    Returns (returncode, stdout, stderr, observed_host_key).
    observed_host_key is non-None only on a successful connection when known_hosts=None
    was passed. Callers are responsible for also checking whether a stored key already
    exists before persisting — use `observed_key and not stored_host_key` not just
    `observed_key is not None`.
    On connection failure rc=255; on timeout rc=-1.
    """
    observed_host_key: str | None = None
    try:
        async with asyncio.timeout(timeout):
            async with asyncssh.connect(
                host=ip,
                username=SSH_USER,
                client_keys=[str(private_key)],
                known_hosts=known_hosts,
                config=[],  # do not load ~/.ssh/config — HOME may not resolve under arbitrary Docker PUIDs
                connect_timeout=10,
            ) as conn:
                if known_hosts is None:
                    # TOFU first-use: capture the host key for storage
                    server_key = conn.get_server_host_key()
                    if server_key:
                        observed_host_key = server_key.export_public_key("openssh").decode().strip()
                result = await conn.run(command, check=False)
    except asyncssh.HostKeyNotVerifiable:
        logger.error("SSH host key mismatch for %s — possible MITM attack", ip)
        return 255, "", "Host key mismatch — verify device identity before retrying", None
    except TimeoutError:
        return -1, "", "SSH command timed out", None
    except (asyncssh.Error, OSError) as exc:
        return 255, "", str(exc), None

    stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode(errors="replace")
    stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode(errors="replace")
    # asyncssh's exit_status is None when the remote closed without setting one
    returncode = result.exit_status if result.exit_status is not None else 0
    return returncode, stdout, stderr, observed_host_key


async def perform_ssh_update(device_id: str, ip_address: str, install_path: str | None = None) -> None:
    """SSH into a SpoolBuddy device and update it to match Bambuddy's branch.

    Updates device.update_status/update_message in the DB and broadcasts
    progress via WebSocket at each step.  Host key verification uses TOFU:
    the device's SSH public key is stored on first connect and verified on
    all subsequent connections.
    """
    from sqlalchemy import select

    from backend.app.api.routes.spoolbuddy import ws_manager
    from backend.app.core.database import async_session
    from backend.app.models.spoolbuddy_device import SpoolBuddyDevice

    install_path = install_path or DEFAULT_INSTALL_PATH
    branch = detect_current_branch()
    safe_branch = shlex.quote(branch)
    safe_path = shlex.quote(install_path)

    # Load the stored SSH host key for TOFU verification
    stored_host_key: str | None = None
    async with async_session() as db:
        result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
        dev = result.scalar_one_or_none()
        if dev:
            stored_host_key = dev.ssh_host_key

    known_hosts: asyncssh.SSHKnownHosts | None = None
    if stored_host_key:
        try:
            # asyncssh.import_known_hosts() expects str — passing bytes crashes
            # inside its line-by-line parser with a TypeError.
            known_hosts = asyncssh.import_known_hosts(f"{ip_address} {stored_host_key}\n")
        except (ValueError, TypeError, asyncssh.Error) as exc:
            logger.warning(
                "Could not parse stored SSH host key for %s, falling back to TOFU: %s",
                device_id,
                exc,
            )

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
        rc, _, stderr, observed_key = await _run_ssh_command(
            ip_address, "echo ok", private_key, known_hosts=known_hosts
        )
        if rc != 0:
            await _update_progress("error", f"SSH connection failed: {stderr[:200]}")
            return

        # TOFU: persist host key on first successful connect
        if observed_key and not stored_host_key:
            async with async_session() as db:
                result = await db.execute(select(SpoolBuddyDevice).where(SpoolBuddyDevice.device_id == device_id))
                d = result.scalar_one_or_none()
                if d:
                    d.ssh_host_key = observed_key
                    await db.commit()
            logger.info("TOFU: stored SSH host key for SpoolBuddy %s", device_id)
            try:
                known_hosts = asyncssh.import_known_hosts(f"{ip_address} {observed_key}\n")
            except (ValueError, TypeError, asyncssh.Error) as exc:
                logger.error(
                    "TOFU: could not parse just-stored host key for %s; "
                    "remaining SSH steps in this run will not verify host key: %s",
                    device_id,
                    exc,
                )
                known_hosts = None

        # Step 2: Git fetch
        await _update_progress("updating", f"Fetching latest code (branch: {branch})...")
        rc, _, stderr, _ = await _run_ssh_command(
            ip_address,
            f"cd {safe_path} && git -c safe.directory={safe_path} fetch origin {safe_branch}",
            private_key,
            known_hosts=known_hosts,
            timeout=120,
        )
        if rc != 0:
            await _update_progress("error", f"git fetch failed: {stderr[:200]}")
            return

        # Step 3: Git checkout + reset
        await _update_progress("updating", "Applying update...")
        rc, _, stderr, _ = await _run_ssh_command(
            ip_address,
            f"cd {safe_path} && git -c safe.directory={safe_path} checkout {safe_branch} "
            f"&& git -c safe.directory={safe_path} reset --hard origin/{safe_branch}",
            private_key,
            known_hosts=known_hosts,
        )
        if rc != 0:
            await _update_progress("error", f"git checkout/reset failed: {stderr[:200]}")
            return

        # Step 4: Install dependencies
        await _update_progress("updating", "Installing dependencies...")
        venv_pip = shlex.quote(f"{install_path}/spoolbuddy/venv/bin/pip")
        rc, _, stderr, _ = await _run_ssh_command(
            ip_address,
            f"{venv_pip} install --upgrade spidev gpiod smbus2 httpx 2>&1",
            private_key,
            known_hosts=known_hosts,
            timeout=120,
        )
        if rc != 0:
            logger.warning("SpoolBuddy %s: pip install returned non-zero (continuing): %s", device_id, stderr[:200])

        # Step 5: Restart daemon
        await _update_progress("updating", "Restarting daemon...")
        rc, _, stderr, _ = await _run_ssh_command(
            ip_address,
            "sudo /usr/bin/systemctl restart spoolbuddy.service",
            private_key,
            known_hosts=known_hosts,
        )
        if rc != 0:
            await _update_progress("error", f"Service restart failed: {stderr[:200]}")
            return

        # Step 6: Clear browser cache and restart kiosk
        # Remove Chromium's Service Worker + cache storage to prevent stale frontend
        await _run_ssh_command(
            ip_address,
            "sudo find /home -maxdepth 5 -path '*/chromium/Default/Service Worker' -type d -exec rm -rf {} + 2>/dev/null; true",
            private_key,
            known_hosts=known_hosts,
        )
        rc, _, stderr, _ = await _run_ssh_command(
            ip_address,
            "sudo /usr/bin/systemctl restart getty@tty1.service",
            private_key,
            known_hosts=known_hosts,
        )
        if rc != 0:
            logger.warning("SpoolBuddy %s: kiosk restart failed (non-fatal): %s", device_id, stderr[:200])

        logger.info("SpoolBuddy %s: SSH update complete (branch=%s)", device_id, branch)
        await _update_progress("complete", f"Updated to {branch}")

    except Exception:
        logger.exception("SpoolBuddy %s: SSH update failed", device_id)
        await _update_progress("error", "Update failed due to an internal error")
