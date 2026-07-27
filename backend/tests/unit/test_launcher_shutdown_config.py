"""Every launcher must be able to shut Bambuddy down gracefully.

Two defects, found together, both invisible until you look for them:

1. **The Docker image never received SIGTERM at all.** ``CMD ["sh", "-c",
   "uvicorn ..."]`` leaves the shell as PID 1 with uvicorn as its child, and
   dash does not forward signals. Measured on the shipped image: ``docker stop``
   ran the full 10s grace period, exited 137 (SIGKILL), and the container log
   contained no "Shutting down" line. So *every* stop, restart and image update
   was a hard kill — no WAL checkpoint, no MQTT disconnect, no virtual-printer
   teardown. ``exec`` makes uvicorn PID 1 and the signal lands.

2. **Uvicorn waits forever for in-flight requests.**
   ``timeout_graceful_shutdown`` defaults to None, and an MJPEG camera stream is
   a response that never completes — ``httptools``'s connection ``shutdown()``
   only flips ``keep_alive = False`` on an in-flight cycle, it does not close the
   transport. One open camera tile pins the process indefinitely, and the app's
   own teardown never runs because uvicorn only fires the lifespan shutdown
   *after* connections drain. The flag caps the wait and cancels the tasks; the
   camera generators already unwind cleanly on CancelledError.

Neither shows up in any functional test — the app is perfectly healthy right up
until you ask it to stop. Hence this: pin the launchers themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

FLAG = "--timeout-graceful-shutdown"

# These pin repo-root launcher files (Dockerfile, compose, service units,
# install scripts) that the Docker test image deliberately does not ship —
# Dockerfile.test copies only backend/, pyproject.toml, gcode_viewer/ and
# requirements. In a source checkout the files are always present and the
# guard below is live (a moved/deleted launcher still fails loudly on every
# `test_backend.sh` run); inside the stripped test image there is nothing to
# check, so skip rather than fail. `frontend/package.json` is present in every
# checkout but never in the test image, so it distinguishes the two.
pytestmark = pytest.mark.skipif(
    not (REPO / "frontend" / "package.json").is_file(),
    reason="launcher config files aren't shipped in the Docker test image; verified in native runs",
)


def _read(rel: str) -> str:
    path = REPO / rel
    assert path.is_file(), f"launcher moved or was removed: {rel}"
    return path.read_text()


def _uvicorn_lines(text: str) -> list[str]:
    """Lines that actually launch uvicorn, ignoring comments about it."""
    return [
        line for line in text.splitlines() if "uvicorn" in line and not line.lstrip().startswith(("#", "REM", "<!--"))
    ]


class TestDockerImage:
    def test_cmd_execs_uvicorn_so_it_becomes_pid_1(self):
        """Without exec, `sh` is PID 1, dash eats the SIGTERM, and docker stop
        always ends in SIGKILL after the grace period.
        """
        cmd = next(line for line in _read("Dockerfile").splitlines() if line.startswith("CMD "))

        assert "exec uvicorn" in cmd, (
            "Dockerfile CMD must `exec` uvicorn. Without it the shell stays as PID 1, "
            "uvicorn never receives SIGTERM, and every docker stop is a SIGKILL:\n" + cmd
        )

    def test_cmd_bounds_the_graceful_shutdown(self):
        cmd = next(line for line in _read("Dockerfile").splitlines() if line.startswith("CMD "))
        assert FLAG in cmd, cmd

    def test_compose_allows_more_than_dockers_default_grace(self):
        compose = _read("docker-compose.yml")
        assert "stop_grace_period:" in compose, (
            "docker-compose.yml should raise stop_grace_period above Docker's 10s default, "
            "so a slow teardown on a Pi is not clipped by a SIGKILL."
        )


class TestSystemdUnits:
    @pytest.mark.parametrize("unit", ["deploy/bambuddy.service"])
    def test_execstart_bounds_the_graceful_shutdown(self, unit):
        exec_start = next(line for line in _read(unit).splitlines() if line.startswith("ExecStart="))
        assert FLAG in exec_start, exec_start

    @pytest.mark.parametrize("unit", ["deploy/bambuddy.service"])
    def test_stop_timeout_leaves_room_for_the_teardown(self, unit):
        """systemd's timer is the backstop, not the mechanism — but it still has
        to outlast uvicorn's own 5s wait plus the app's ~1-2s of teardown.
        """
        match = re.search(r"^TimeoutStopSec=(\d+)", _read(unit), re.M)
        assert match, "unit should state a TimeoutStopSec rather than inherit the 90s default"
        assert int(match.group(1)) >= 15, (
            f"TimeoutStopSec={match.group(1)}s can clip the teardown: uvicorn waits up to 5s "
            "for in-flight requests, then the app checkpoints the WAL and stops the virtual "
            "printers."
        )


class TestInstallScript:
    def test_generated_systemd_unit_bounds_the_shutdown(self):
        lines = _uvicorn_lines(_read("install/install.sh"))
        exec_start = [line for line in lines if line.startswith("ExecStart=")]
        assert exec_start, "install.sh no longer emits a systemd ExecStart line"
        for line in exec_start:
            assert FLAG in line, line

    def test_generated_launchd_plist_bounds_the_shutdown(self):
        """The macOS plist passes argv as a <string> array, so the flag and its
        value are two separate entries.
        """
        plist_region = _read("install/install.sh")
        assert f"<string>{FLAG}</string>" in plist_region, (
            "the launchd plist in install.sh does not pass --timeout-graceful-shutdown"
        )


class TestWindowsService:
    def test_nssm_registration_bounds_the_shutdown(self):
        bat = _read("installers/windows/service/install-service.bat")
        install_line = next(line for line in _uvicorn_lines(bat) if "install Bambuddy" in line)
        assert FLAG in install_line, install_line

    def test_nssm_waits_long_enough_for_the_ctrl_c_stop(self):
        """NSSM's default AppStopMethodConsole is 1500ms. Uvicorn shuts down on
        the Ctrl-C but needs longer than that, so Windows was force-killing it
        mid-teardown.
        """
        bat = _read("installers/windows/service/install-service.bat")
        match = re.search(r"AppStopMethodConsole\s+(\d+)", bat)
        assert match, "install-service.bat must raise NSSM's 1500ms console-stop default"
        assert int(match.group(1)) >= 10000, match.group(1)
