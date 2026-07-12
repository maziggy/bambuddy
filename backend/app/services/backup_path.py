"""Why a backup directory is not writable — and what to actually do about it.

Bambuddy's systemd unit runs with ``ProtectSystem=strict``. That mounts the
entire filesystem read-only inside the service's own mount namespace and carves
back out only ``ReadWritePaths=<install> <data> <logs>``. A backup output path
on a NAS mount is therefore read-only *to the service* while the operator's own
shell writes to it happily. The kernel reports this as ``EROFS``, not
``EACCES``, so the obvious move — checking folder permissions — turns up nothing
and the real cause (our own unit file) is the last place anyone looks (#2544).

Docker has the same shape with a different cause: a host path that was never
bind-mounted into the container is simply not the host path. Worse, it is still
*writable* — the write lands in the container's ephemeral layer and vanishes on
the next ``docker compose up``. A backup that silently goes nowhere is the one
failure mode a backup feature must not have.

So: probe the directory with a real write before trusting it, and when that
write fails, name which of these it is and hand back the exact command that
fixes it.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import tempfile
from pathlib import Path

from backend.app.services.discovery import is_running_in_docker

logger = logging.getLogger(__name__)

# Cgroup line for a systemd service, e.g.
#   0::/system.slice/bambuddy.service
#   0::/system.slice/system-bambuddy.slice/bambuddy@1.service
_SERVICE_CGROUP = re.compile(r"/([^/]+\.service)\b")


def systemd_unit_name() -> str | None:
    """Name of the systemd unit we are running as, or None if we are not one.

    ``INVOCATION_ID`` is set by systemd for every unit it starts and by nothing
    else, so it is the signal that we are a unit at all. The name itself comes
    from the cgroup path — systemd exports no environment variable for it.
    """
    if not os.environ.get("INVOCATION_ID"):
        return None
    try:
        cgroup = Path("/proc/self/cgroup").read_text()
    except OSError:
        return "bambuddy.service"
    match = _SERVICE_CGROUP.search(cgroup)
    return match.group(1) if match else "bambuddy.service"


def _systemd_remedy(unit: str, path: Path) -> str:
    return (
        f"sudo systemctl edit {unit}\n"
        "\n"
        "Add these two lines to the drop-in, save, then restart:\n"
        "\n"
        "[Service]\n"
        f"ReadWritePaths={path}\n"
        "\n"
        f"sudo systemctl restart {unit}"
    )


def _docker_remedy(path: Path) -> str:
    return f"services:\n  bambuddy:\n    volumes:\n      - {path}:{path}"


def classify_backup_dir_error(exc: OSError, backup_dir: Path) -> dict:
    """Map an OSError raised while writing to ``backup_dir`` onto a diagnosis.

    ``message`` is English and goes to the log and the API. The frontend
    translates from ``code`` and renders ``remedy`` verbatim as a snippet.
    """
    detail = str(exc)
    unit = systemd_unit_name()

    if exc.errno == errno.EROFS:
        if unit:
            return {
                "writable": False,
                "path": str(backup_dir),
                "code": "sandboxed",
                "detail": detail,
                "remedy": _systemd_remedy(unit, backup_dir),
                "message": (
                    f"{backup_dir} is read-only for the Bambuddy service. Its systemd unit runs with "
                    "ProtectSystem=strict, which makes every path outside the install, data and log "
                    f"directories read-only — add ReadWritePaths={backup_dir} to a drop-in "
                    f"(sudo systemctl edit {unit}) and restart. If the path is on a network share, also "
                    "confirm the share itself is not mounted read-only."
                ),
            }
        return {
            "writable": False,
            "path": str(backup_dir),
            "code": "read_only",
            "detail": detail,
            "remedy": None,
            "message": f"{backup_dir} is on a read-only filesystem.",
        }

    if exc.errno in (errno.EACCES, errno.EPERM):
        return {
            "writable": False,
            "path": str(backup_dir),
            "code": "permission_denied",
            "detail": detail,
            "remedy": None,
            "message": f"Bambuddy is not allowed to write to {backup_dir}. Check the directory's owner and mode.",
        }

    if exc.errno == errno.ENOSPC:
        return {
            "writable": False,
            "path": str(backup_dir),
            "code": "no_space",
            "detail": detail,
            "remedy": None,
            "message": f"No space left on the filesystem holding {backup_dir}.",
        }

    if exc.errno in (errno.ENOTDIR, errno.EEXIST):
        return {
            "writable": False,
            "path": str(backup_dir),
            "code": "not_a_directory",
            "detail": detail,
            "remedy": None,
            "message": f"{backup_dir} exists but is not a directory.",
        }

    if exc.errno == errno.ENOENT:
        return {
            "writable": False,
            "path": str(backup_dir),
            "code": "missing",
            "detail": detail,
            "remedy": None,
            "message": f"{backup_dir} does not exist and could not be created.",
        }

    return {
        "writable": False,
        "path": str(backup_dir),
        "code": "error",
        "detail": detail,
        "remedy": None,
        "message": f"Bambuddy cannot write to {backup_dir}: {exc}",
    }


def _is_container_ephemeral(backup_dir: Path) -> bool:
    """True if this path lives in the container's own writable layer.

    A bind mount or named volume always sits on a different device than the
    container root, so a matching ``st_dev`` means nothing was mounted here and
    the backups die with the container.
    """
    try:
        return backup_dir.stat().st_dev == Path("/").stat().st_dev
    except OSError:
        return False


def probe_backup_dir(backup_dir: Path) -> dict:
    """Create the directory and write a throwaway file in it.

    Returns the same shape as :func:`classify_backup_dir_error`, plus a
    ``warning`` code for a directory that is writable but not persistent.
    """
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=backup_dir, prefix=".bambuddy-write-test-") as probe:
            probe.write(b"bambuddy")
            probe.flush()
    except OSError as e:
        result = classify_backup_dir_error(e, backup_dir)
        logger.warning("Backup path check failed: %s", result["message"])
        return {**result, "warning": None}

    warning = None
    if is_running_in_docker() and _is_container_ephemeral(backup_dir):
        warning = "container_ephemeral"

    return {
        "writable": True,
        "path": str(backup_dir),
        "code": "ok",
        "detail": None,
        "remedy": _docker_remedy(backup_dir) if warning else None,
        "message": f"{backup_dir} is writable.",
        "warning": warning,
    }
