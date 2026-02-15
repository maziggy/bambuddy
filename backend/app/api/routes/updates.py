"""Update checking and management routes."""

import asyncio
import logging
import os
import re
import shutil
import sys

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.config import APP_VERSION, GITHUB_REPO, settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/updates", tags=["updates"])

# Global state for update progress
_update_status = {
    "status": "idle",  # idle, checking, downloading, installing, complete, error
    "progress": 0,
    "message": "",
    "error": None,
}


def _is_docker_environment() -> bool:
    """Detect if running inside a Docker container."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as f:
            if "docker" in f.read():
                return True
    except (FileNotFoundError, PermissionError):
        pass  # cgroup file unavailable; continue with other detection methods
    git_dir = settings.base_dir / ".git"
    return not git_dir.exists()


def _find_executable(name: str) -> str | None:
    """Find an executable in PATH or common locations."""
    # Try standard PATH first
    path = shutil.which(name)
    if path:
        return path

    # Common locations for executables (useful when running as systemd service)
    common_paths = [
        f"/usr/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
        f"/home/linuxbrew/.linuxbrew/bin/{name}",
        f"{os.path.expanduser('~')}/.nvm/current/bin/{name}",
        f"{os.path.expanduser('~')}/.local/bin/{name}",
    ]

    for p in common_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    return None


def parse_version(version: str) -> tuple:
    """Parse version string into tuple for comparison.

    Returns (major, minor, patch, micro, is_prerelease, prerelease_num)
    where is_prerelease is 0 for release, 1 for prerelease.
    This ensures releases sort higher than prereleases of same version.

    Examples:
        "0.1.5"    -> (0, 1, 5, 0, 0, 0)   # release
        "0.1.5b7"  -> (0, 1, 5, 0, 1, 7)   # beta 7
        "0.1.5b10" -> (0, 1, 5, 0, 1, 10)  # beta 10
        "0.1.8.1"  -> (0, 1, 8, 1, 0, 0)   # patch release
    """
    # Remove 'v' prefix if present
    version = version.lstrip("v")

    # Match version pattern: major.minor.patch[.micro][b|beta|alpha|rc]N
    match = re.match(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?:b|beta|alpha|rc)?(\d+)?", version)

    if match:
        major = int(match.group(1))
        minor = int(match.group(2))
        patch = int(match.group(3))
        micro = int(match.group(4)) if match.group(4) else 0
        prerelease_num = int(match.group(5)) if match.group(5) else 0

        # Check if this is a prerelease (has b/beta/alpha/rc suffix)
        is_prerelease = 1 if re.search(r"[a-zA-Z]", version.split(".")[-1]) else 0

        return (major, minor, patch, micro, is_prerelease, prerelease_num)

    # Fallback: try simple split
    parts = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            num = "".join(c for c in part if c.isdigit())
            parts.append(int(num) if num else 0)

    return tuple(parts) + (0, 0, 0)


def is_newer_version(latest: str, current: str) -> bool:
    """Check if latest version is newer than current.

    Properly handles prerelease versions:
    - 0.1.5 > 0.1.5b7 (release is newer than any beta)
    - 0.1.5b8 > 0.1.5b7 (later beta is newer)
    - 0.1.6b1 > 0.1.5 (next version beta is newer than current release)
    """
    try:
        latest_parsed = parse_version(latest)
        current_parsed = parse_version(current)

        # Compare (major, minor, patch, micro) first
        latest_base = latest_parsed[:4]
        current_base = current_parsed[:4]

        if latest_base > current_base:
            return True
        elif latest_base < current_base:
            return False

        # Same base version - compare prerelease status
        # is_prerelease: 0 = release, 1 = prerelease
        # Release (0) should be "greater" than prerelease (1)
        latest_is_prerelease = latest_parsed[4] if len(latest_parsed) > 4 else 0
        current_is_prerelease = current_parsed[4] if len(current_parsed) > 4 else 0

        if latest_is_prerelease < current_is_prerelease:
            # latest is release, current is prerelease -> latest is newer
            return True
        elif latest_is_prerelease > current_is_prerelease:
            # latest is prerelease, current is release -> latest is NOT newer
            return False

        # Both are same type (both release or both prerelease)
        # Compare prerelease numbers
        latest_prerelease_num = latest_parsed[5] if len(latest_parsed) > 5 else 0
        current_prerelease_num = current_parsed[5] if len(current_parsed) > 5 else 0

        return latest_prerelease_num > current_prerelease_num

    except Exception:
        return False


@router.get("/version")
async def get_version():
    """Get current application version.

    Note: Unauthenticated - needed to display version in UI without login.
    """
    return {
        "version": APP_VERSION,
        "repo": GITHUB_REPO,
    }


@router.get("/check")
async def check_for_updates(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SYSTEM_READ),
):
    """Check GitHub for available updates."""
    global _update_status

    # Respect the check_updates setting
    result = await db.execute(select(Settings).where(Settings.key == "check_updates"))
    setting = result.scalar_one_or_none()
    if setting and setting.value.lower() == "false":
        return {
            "update_available": False,
            "current_version": APP_VERSION,
            "latest_version": None,
            "message": "Update checks are disabled",
        }

    _update_status = {
        "status": "checking",
        "progress": 0,
        "message": "Checking for updates...",
        "error": None,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=10.0,
            )

            if response.status_code == 404:
                # No releases yet
                _update_status = {
                    "status": "idle",
                    "progress": 100,
                    "message": "No releases found",
                    "error": None,
                }
                return {
                    "update_available": False,
                    "current_version": APP_VERSION,
                    "latest_version": None,
                    "message": "No releases found",
                }

            response.raise_for_status()
            release_data = response.json()

            latest_version = release_data.get("tag_name", "").lstrip("v")
            release_name = release_data.get("name", latest_version)
            release_notes = release_data.get("body", "")
            release_url = release_data.get("html_url", "")
            published_at = release_data.get("published_at", "")

            update_available = is_newer_version(latest_version, APP_VERSION)

            _update_status = {
                "status": "idle",
                "progress": 100,
                "message": "Update available" if update_available else "Up to date",
                "error": None,
            }

            is_docker = _is_docker_environment()
            return {
                "update_available": update_available,
                "current_version": APP_VERSION,
                "latest_version": latest_version,
                "release_name": release_name,
                "release_notes": release_notes,
                "release_url": release_url,
                "published_at": published_at,
                "is_docker": is_docker,
                "update_method": "docker" if is_docker else "git",
            }

    except httpx.HTTPError as e:
        logger.error("Failed to check for updates: %s", e)
        _update_status = {
            "status": "error",
            "progress": 0,
            "message": "Failed to check for updates",
            "error": "Failed to check for updates",
        }
        return {
            "update_available": False,
            "current_version": APP_VERSION,
            "latest_version": None,
            "error": "Failed to check for updates",
        }


async def _perform_update():
    """Perform the actual update using git fetch and reset."""
    global _update_status

    try:
        base_dir = settings.base_dir

        # Find git executable (may not be in PATH when running as systemd service)
        git_path = _find_executable("git")
        if not git_path:
            _update_status = {
                "status": "error",
                "progress": 0,
                "message": "Git not found",
                "error": "Could not find git executable. Please ensure git is installed.",
            }
            return

        logger.info("Using git at: %s", git_path)

        # Git config to avoid safe.directory issues
        git_config = ["-c", f"safe.directory={base_dir}"]

        _update_status = {
            "status": "downloading",
            "progress": 10,
            "message": "Configuring git...",
            "error": None,
        }

        # Ensure remote uses HTTPS (SSH may not be available)
        https_url = f"https://github.com/{GITHUB_REPO}.git"
        process = await asyncio.create_subprocess_exec(
            git_path,
            *git_config,
            "remote",
            "set-url",
            "origin",
            https_url,
            cwd=str(base_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()

        _update_status = {
            "status": "downloading",
            "progress": 20,
            "message": "Fetching latest changes...",
            "error": None,
        }

        # Fetch from origin
        process = await asyncio.create_subprocess_exec(
            git_path,
            *git_config,
            "fetch",
            "origin",
            "main",
            cwd=str(base_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Git fetch failed"
            logger.error("Git fetch failed: %s", error_msg)
            _update_status = {
                "status": "error",
                "progress": 0,
                "message": "Failed to fetch updates",
                "error": error_msg,
            }
            return

        _update_status = {
            "status": "downloading",
            "progress": 40,
            "message": "Applying updates...",
            "error": None,
        }

        # Hard reset to origin/main (clean update, no merge conflicts)
        process = await asyncio.create_subprocess_exec(
            git_path,
            *git_config,
            "reset",
            "--hard",
            "origin/main",
            cwd=str(base_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Git reset failed"
            logger.error("Git reset failed: %s", error_msg)
            _update_status = {
                "status": "error",
                "progress": 0,
                "message": "Failed to apply updates",
                "error": error_msg,
            }
            return

        _update_status = {
            "status": "installing",
            "progress": 50,
            "message": "Installing dependencies...",
            "error": None,
        }

        # Install Python dependencies
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            "requirements.txt",
            "-q",
            cwd=str(base_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.warning("pip install warning: %s", stderr.decode() if stderr else "unknown")

        # Try to build frontend if npm is available (optional - static files are pre-built)
        npm_path = _find_executable("npm")
        frontend_dir = base_dir / "frontend"

        if npm_path and frontend_dir.exists():
            _update_status = {
                "status": "installing",
                "progress": 70,
                "message": "Building frontend...",
                "error": None,
            }

            # npm install
            process = await asyncio.create_subprocess_exec(
                npm_path,
                "install",
                cwd=str(frontend_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            # npm run build
            process = await asyncio.create_subprocess_exec(
                npm_path,
                "run",
                "build",
                cwd=str(frontend_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.warning("Frontend build warning: %s", stderr.decode() if stderr else "unknown")
        else:
            logger.info("npm not found or frontend dir missing - using pre-built static files")

        _update_status = {
            "status": "complete",
            "progress": 100,
            "message": "Update complete! Please restart the application.",
            "error": None,
        }

        logger.info("Update completed successfully")

    except Exception as e:
        logger.error("Update failed: %s", e)
        _update_status = {
            "status": "error",
            "progress": 0,
            "message": "Update failed",
            "error": "Update failed unexpectedly",
        }


@router.post("/apply")
async def apply_update(
    background_tasks: BackgroundTasks,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Apply available update (git pull + rebuild)."""
    global _update_status

    if _update_status["status"] in ["downloading", "installing"]:
        return {
            "success": False,
            "message": "Update already in progress",
            "status": _update_status,
        }

    # Check if running in Docker
    if _is_docker_environment():
        return {
            "success": False,
            "is_docker": True,
            "message": (
                "Docker installations cannot be updated in-app. "
                "Please update via Docker Compose: "
                "git pull && docker compose build --pull && docker compose up -d"
            ),
        }

    # Start update in background
    background_tasks.add_task(_perform_update)

    _update_status = {
        "status": "downloading",
        "progress": 10,
        "message": "Starting update...",
        "error": None,
    }

    return {
        "success": True,
        "message": "Update started",
        "status": _update_status,
    }


@router.get("/status")
async def get_update_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SYSTEM_READ),
):
    """Get current update status."""
    return _update_status
