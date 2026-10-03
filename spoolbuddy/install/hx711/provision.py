"""Provision optional HX711 hardware from the normal root-run installer.

The daemon remains unprivileged. DKMS sources and boot helpers are copied into
root-owned locations so application updates cannot change privileged boot code.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
STATE = Path("/var/lib/spoolbuddy-hx711/state.json")
MODULES = Path("/lib/modules")
SOURCE = Path("/usr/src/spoolbuddy-hx711-6.18.0")
MODULE = "spoolbuddy-hx711/6.18.0"
UNIT = "spoolbuddy-hx711.service"
DRIVER_HASH = "0d6b7fb23f44794152041a51befd3f763bfc1d4a443a8f1e449f17bc3bbe9000"
RESERVED_PINS = frozenset((0, 1, 2, 3, 7, 8, 9, 10, 11, 23, 24, 25))
ASSETS = {
    "setup.sh": Path("/usr/local/lib/spoolbuddy-hx711/setup.sh"),
    "spoolbuddy-hx711.dtbo": Path("/usr/local/lib/spoolbuddy-hx711/spoolbuddy-hx711.dtbo"),
    UNIT: Path("/etc/systemd/system/spoolbuddy-hx711.service"),
    "20-hx711.conf": Path("/etc/systemd/system/spoolbuddy.service.d/20-hx711.conf"),
}
# Wants orders startup but permits the application/NFC to run if scale setup fails.
DROPIN = "[Unit]\nWants=spoolbuddy-hx711.service\nAfter=spoolbuddy-hx711.service\n"


def run(*args, check=True, capture_output=False):
    return subprocess.run(args, check=check, capture_output=capture_output, text=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(content)
            output.flush()
            temp.chmod(mode)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def save(state):
    atomic_write(STATE, (json.dumps(state, indent=2) + "\n").encode(), 0o600)


def validate_pins(data_pin, clock_pin):
    if not (0 <= data_pin <= 27 and 0 <= clock_pin <= 27) or data_pin == clock_pin:
        raise ValueError("HX711 requires two different GPIO numbers from 0 to 27")
    if {data_pin, clock_pin} & RESERVED_PINS:
        raise ValueError("HX711 pins conflict with PN5180, SPI or I2C wiring")


def kernel_flavour(release):
    match = re.fullmatch(r"[0-9][0-9A-Za-z.+_-]*-rpi-(v8|2712)", release)
    if not match:
        raise RuntimeError("HX711 setup supports Raspberry Pi OS 64-bit v8 and 2712 kernels")
    return match[1]


def read_state():
    if STATE.is_symlink():
        raise RuntimeError("Refusing a symlink for HX711 installation state")
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    for name, target in ASSETS.items():
        if target.exists() or target.is_symlink():
            expected = state and state["assets"].get(name)
            if target.is_symlink() or not expected or digest(target) != expected:
                raise RuntimeError(f"Preserve existing HX711 setup at {target}; it is unmanaged or modified")
    if SOURCE.exists() or SOURCE.is_symlink():
        if SOURCE.is_symlink() or not state or not state["dkms"]:
            raise RuntimeError("Existing HX711 DKMS source is unmanaged; remove the previous trial first")
        for path in (PACKAGE / "module").iterdir():
            if not (SOURCE / path.name).is_file() or digest(SOURCE / path.name) != digest(path):
                raise RuntimeError("Installed HX711 source differs; preserve it before replacing the driver package")
    return state


def build_for_installed_kernels(release):
    """Also cover a newly installed kernel awaiting reboot after apt upgrade."""
    flavour = kernel_flavour(release)
    releases = {release}
    for path in MODULES.iterdir():
        if (path / "kernel").is_dir() and re.fullmatch(r"[0-9][0-9A-Za-z.+_-]*-rpi-" + flavour, path.name):
            releases.add(path.name)
    for target in sorted(releases):
        headers = MODULES / target / "build"
        if not all((headers / name).is_file() for name in ("Makefile", "Module.symvers")):
            run("apt-get", "install", "-y", "linux-headers-" + target)
        if not all((headers / name).is_file() for name in ("Makefile", "Module.symvers")):
            raise RuntimeError(f"Matching kernel headers unavailable for {target}")
        run("dkms", "install", MODULE, "-k", target)


def stock_module(release):
    """Look in the kernel tree even when updates/dkms shadows the stock module."""
    kernel = (MODULES / release / "kernel").resolve()
    for candidate in sorted(kernel.glob("drivers/iio/adc/hx711.ko*")):
        if not candidate.resolve().is_relative_to(kernel):
            continue
        result = run("modinfo", "-F", "filename", str(candidate), check=False, capture_output=True)
        if result.returncode == 0 and Path(result.stdout.strip()).resolve() == candidate.resolve():
            return candidate
    result = run("modinfo", "-k", release, "-F", "filename", "hx711", check=False, capture_output=True)
    if result.returncode == 0 and result.stdout.strip() == "(builtin)":
        return "(builtin)"
    return None


def verify_stock_selected(release, stock):
    selected = run("modinfo", "-k", release, "-F", "filename", "hx711", capture_output=True).stdout.strip()
    matches = selected == "(builtin)" if stock == "(builtin)" else Path(selected).resolve() == stock.resolve()
    if not matches:
        raise RuntimeError("Stock HX711 is still shadowed by another module; preserving existing setup")


def install(data_pin=5, clock_pin=6):
    validate_pins(data_pin, clock_pin)
    release = platform.release()
    kernel_flavour(release)
    if digest(PACKAGE / "module/hx711.c") != DRIVER_HASH:
        raise RuntimeError("Bundled HX711 source checksum mismatch")
    previous = read_state()
    snapshots = {name: path.read_bytes() for name, path in ASSETS.items() if path.exists()}
    state = previous or {"assets": {}, "dkms": False}
    was_running = run("systemctl", "is-active", "--quiet", "spoolbuddy.service", check=False).returncode == 0
    hardware_was_running = (
        bool(previous) and run("systemctl", "is-active", "--quiet", UNIT, check=False).returncode == 0
    )
    hardware_was_enabled = (
        bool(previous) and run("systemctl", "is-enabled", "--quiet", UNIT, check=False).returncode == 0
    )
    new_source = False
    registration_attempted = False
    migration_attempted = False
    app_stopped = hardware_touched = assets_touched = False
    try:
        # Build and validate all inputs before interrupting an existing daemon.
        with tempfile.TemporaryDirectory(prefix="spoolbuddy-hx711-") as directory:
            stage = Path(directory)
            dts = (PACKAGE / "wiring.dts").read_text()
            dts = dts.replace("@DATA_PIN@", str(data_pin)).replace("@CLOCK_PIN@", str(clock_pin))
            (stage / "wiring.dts").write_text(dts)
            run(
                "dtc",
                "-@",
                "-I",
                "dts",
                "-O",
                "dtb",
                "-o",
                str(stage / "spoolbuddy-hx711.dtbo"),
                str(stage / "wiring.dts"),
            )
            # A fresh Pi may not ship this module; absence is an install step.
            stock = stock_module(release)
            if stock and not state["dkms"]:
                verify_stock_selected(release, stock)
            if not stock:
                print("HX711 driver not installed for this kernel; building and installing it now.", flush=True)
            if not stock:
                if not SOURCE.exists():
                    # DKMS can retain a registered/broken package after its
                    # source directory disappears. Never claim or remove it
                    # as part of cleaning up our attempted fresh installation.
                    registered = run("dkms", "status", MODULE, capture_output=True).stdout.strip()
                    if registered:
                        raise RuntimeError(
                            "HX711 is already registered in DKMS but its source is missing. "
                            "Preserve/repair that installation before retrying; no driver was replaced."
                        )
                    new_source = True
                    shutil.copytree(PACKAGE / "module", SOURCE)
                    registration_attempted = True
                    run("dkms", "add", MODULE)
                build_for_installed_kernels(release)
                state = {**state, "dkms": True}
            if was_running:
                run("systemctl", "stop", "spoolbuddy.service")
                app_stopped = True
            if previous:
                hardware_touched = True
                run("systemctl", "stop", UNIT)
            if stock and state["dkms"]:
                # Stop the owned overlay before replacing the loaded module.
                # Keep source until state is saved so failures can roll back.
                migration_attempted = True
                run("dkms", "remove", MODULE, "--all")
                run("depmod", "-a", release)
                if stock != "(builtin)":
                    run("modprobe", "-r", "hx711")
                verify_stock_selected(release, stock)
                state = {**state, "dkms": False}
                print("Using the kernel's HX711 driver; removed the temporary DKMS package.", flush=True)
            staged = {
                "setup.sh": (PACKAGE / "setup.sh").read_bytes(),
                "spoolbuddy-hx711.dtbo": (stage / "spoolbuddy-hx711.dtbo").read_bytes(),
                UNIT: (PACKAGE / UNIT).read_bytes(),
                "20-hx711.conf": DROPIN.encode(),
            }
            assets_touched = True
            for name, content in staged.items():
                atomic_write(ASSETS[name], content, 0o755 if name == "setup.sh" else 0o644)
            state = {
                **state,
                "assets": {name: digest(path) for name, path in ASSETS.items()},
                "data_pin": data_pin,
                "clock_pin": clock_pin,
            }
            if migration_attempted and SOURCE.exists():
                shutil.rmtree(SOURCE)
            save(state)
            run("systemctl", "daemon-reload")
            run("systemctl", "enable", UNIT)
            if run("systemctl", "restart", UNIT, check=False).returncode:
                print("HX711 installed but hardware is not ready. Check wiring and journalctl -u spoolbuddy-hx711.")
                print(
                    "SpoolBuddy and NFC can still start. Restart the hardware service and SpoolBuddy after fixing wiring."
                )
    except BaseException:
        # Restore the previous managed configuration; never remove foreign files.
        if assets_touched:
            # Stop using the new helper before restoring its previous files.
            run("systemctl", "stop", UNIT, check=False)
            run("systemctl", "enable" if hardware_was_enabled else "disable", UNIT, check=False)
            for name, path in ASSETS.items():
                if name in snapshots:
                    atomic_write(path, snapshots[name], 0o755 if name == "setup.sh" else 0o644)
                else:
                    path.unlink(missing_ok=True)
            if previous:
                save(previous)
            else:
                STATE.unlink(missing_ok=True)
            run("systemctl", "daemon-reload")
        if new_source:
            removed = not registration_attempted or run("dkms", "remove", MODULE, "--all", check=False).returncode == 0
            if removed and SOURCE.exists():
                shutil.rmtree(SOURCE)
            elif not removed:
                # Keep enough ownership information to retry/remove safely.
                save({**(previous or {"assets": {}}), "dkms": True})
                print(f"DKMS cleanup failed; source retained at {SOURCE}. Rerun setup after checking dkms status.")
        if migration_attempted:
            if not SOURCE.exists():
                shutil.copytree(PACKAGE / "module", SOURCE)
            try:
                build_for_installed_kernels(release)
            except Exception as error:
                save({**(previous or {"assets": {}}), "dkms": True})
                print(f"HX711 DKMS restore failed: {error}. Source retained; rerun setup to repair.")
        if hardware_touched and hardware_was_running:
            run("systemctl", "start", UNIT, check=False)
        raise
    finally:
        if app_stopped:
            run("systemctl", "start", "spoolbuddy.service")
    print(f"HX711 configured: DT GPIO{data_pin}, SCK GPIO{clock_pin}. Driver and boot setup installed.")


def remove():
    # A normal NAU7802 installation must not touch unrelated HX711 setups.
    if not STATE.exists():
        return
    state = read_state()
    was_running = run("systemctl", "is-active", "--quiet", "spoolbuddy.service", check=False).returncode == 0
    try:
        if was_running:
            run("systemctl", "stop", "spoolbuddy.service")
        run("systemctl", "disable", "--now", UNIT)
        if state["dkms"]:
            run("dkms", "remove", MODULE, "--all")
            if SOURCE.exists():
                shutil.rmtree(SOURCE)
        for path in ASSETS.values():
            path.unlink(missing_ok=True)
        STATE.unlink()
        run("systemctl", "daemon-reload")
    finally:
        if was_running:
            run("systemctl", "start", "spoolbuddy.service")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove"))
    parser.add_argument("--data-pin", type=int, default=5)
    parser.add_argument("--clock-pin", type=int, default=6)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run the normal SpoolBuddy installer with sudo")
    if args.action == "install":
        if platform.machine() != "aarch64":
            parser.error("HX711 installation requires Raspberry Pi OS 64-bit")
        install(args.data_pin, args.clock_pin)
    else:
        remove()


if __name__ == "__main__":
    main()
