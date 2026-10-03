"""Exercise kernel provisioning against temporary files and mocked OS commands."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "install/hx711/provision.py"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("hx711_provision", SCRIPT)
    provision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provision)
    monkeypatch.setattr(provision, "STATE", tmp_path / "state/state.json")
    monkeypatch.setattr(provision, "SOURCE", tmp_path / "source")
    monkeypatch.setattr(provision, "MODULES", tmp_path / "modules")
    monkeypatch.setattr(provision, "ASSETS", {name: tmp_path / "assets" / name for name in provision.ASSETS})
    release = "6.18.50+rpt-rpi-v8"
    monkeypatch.setattr(provision.platform, "release", lambda: release)
    fixture = SimpleNamespace(
        p=provision,
        calls=[],
        fail=None,
        stock=False,
        active=False,
        hardware_ready=True,
        dkms_status="",
        cleanup_error=False,
        shadowed=False,
    )

    def command(*args, check=True, capture_output=False):
        fixture.calls.append(args)
        code = 0
        stdout = ""
        if args[0] == "modinfo":
            code = 0 if fixture.stock else 1
            if fixture.stock:
                stdout = str(stock_path(fixture))
                if args[-1] == "hx711" and fixture.shadowed:
                    stdout = str(provision.MODULES / release / "updates/foreign/hx711.ko")
        elif args[:3] == ("systemctl", "is-active", "--quiet"):
            code = 0 if fixture.active else 3
        elif args[:2] == ("systemctl", "restart") and not fixture.hardware_ready:
            code = 1
        elif args[0] == "dtc":
            # Preserve the generated DTS for assertions without requiring dtc on the host.
            fixture.dts = Path(args[-1]).read_text()
            Path(args[args.index("-o") + 1]).write_bytes(b"test-overlay")
        if fixture.fail and args[: len(fixture.fail)] == fixture.fail:
            code = 1
        if args[:2] == ("dkms", "remove") and fixture.cleanup_error:
            code = 1
        if check and code:
            raise subprocess.CalledProcessError(code, args)
        return SimpleNamespace(
            returncode=code, stdout=fixture.dkms_status if args[:2] == ("dkms", "status") else stdout
        )

    monkeypatch.setattr(provision, "run", command)
    add_headers(fixture, release)
    return fixture


def add_headers(setup, release):
    headers = setup.p.MODULES / release / "build"
    headers.mkdir(parents=True)
    (headers.parent / "kernel").mkdir()
    for name in ("Makefile", "Module.symvers"):
        (headers / name).touch()


def stock_path(setup):
    return setup.p.MODULES / "6.18.50+rpt-rpi-v8/kernel/drivers/iio/adc/hx711.ko.xz"


def add_stock(setup):
    setup.stock = True
    path = stock_path(setup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_install_builds_running_and_pending_kernel_and_orders_boot(setup):
    add_headers(setup, "6.18.51+rpt-rpi-v8")
    setup.p.install()
    assert ("dkms", "install", setup.p.MODULE, "-k", "6.18.50+rpt-rpi-v8") in setup.calls
    assert ("dkms", "install", setup.p.MODULE, "-k", "6.18.51+rpt-rpi-v8") in setup.calls
    assert ("systemctl", "enable", setup.p.UNIT) in setup.calls
    assert "dout-gpios = <&gpio 5 0>" in setup.dts
    assert "sck-gpios = <&gpio 6 0>" in setup.dts
    dropin = setup.p.ASSETS["20-hx711.conf"].read_text()
    assert "Wants=spoolbuddy-hx711.service" in dropin
    assert "Requires=" not in dropin
    assert setup.p.ASSETS["setup.sh"].stat().st_mode & 0o777 == 0o755


def test_repeat_install_keeps_source_and_restores_running_daemon(setup):
    setup.p.install()
    setup.calls.clear()
    setup.active = True
    setup.p.install()
    assert ("dkms", "add", setup.p.MODULE) not in setup.calls
    assert ("systemctl", "stop", "spoolbuddy.service") in setup.calls
    assert setup.calls[-1] == ("systemctl", "start", "spoolbuddy.service")


def test_os_module_needs_no_dkms_source(setup):
    add_stock(setup)
    setup.p.install()
    assert not any(call[0] == "dkms" for call in setup.calls)
    assert not setup.p.SOURCE.exists()
    assert not json.loads(setup.p.STATE.read_text())["dkms"]


def test_failed_build_preserves_app_and_removes_new_source(setup):
    setup.active = True
    setup.fail = ("dkms", "install")
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert not setup.p.SOURCE.exists()
    assert not setup.p.STATE.exists()
    assert not any(path.exists() for path in setup.p.ASSETS.values())
    assert ("systemctl", "stop", "spoolbuddy.service") not in setup.calls


def test_failed_reinstall_preserves_previous_managed_state(setup):
    setup.p.install()
    before = setup.p.STATE.read_bytes()
    setup.fail = ("dkms", "install")
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert setup.p.STATE.read_bytes() == before
    assert setup.p.SOURCE.exists()
    assert all(path.exists() for path in setup.p.ASSETS.values())


def test_build_failure_does_not_restart_existing_hardware(setup):
    setup.p.install()
    setup.calls.clear()
    setup.active = True
    setup.fail = ("dkms", "install")
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert not any(call[:2] in (("systemctl", "stop"), ("systemctl", "start")) for call in setup.calls)


def test_activation_failure_restores_files_and_running_services(setup):
    setup.p.install()
    before = setup.p.STATE.read_bytes()
    assets = {name: path.read_bytes() for name, path in setup.p.ASSETS.items()}
    setup.calls.clear()
    setup.active = True
    setup.fail = ("systemctl", "enable")
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install(17, 27)
    assert setup.p.STATE.read_bytes() == before
    assert all(path.read_bytes() == assets[name] for name, path in setup.p.ASSETS.items())
    failure_index = setup.calls.index(("systemctl", "enable", setup.p.UNIT))
    assert setup.calls[failure_index + 1] == ("systemctl", "stop", setup.p.UNIT)
    assert setup.calls[-2:] == [
        ("systemctl", "start", setup.p.UNIT),
        ("systemctl", "start", "spoolbuddy.service"),
    ]


def test_cleanup_failure_keeps_owned_source_for_retry(setup, capsys):
    setup.fail = ("dkms", "add")
    setup.cleanup_error = True
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert setup.p.SOURCE.exists()
    assert setup.p.read_state()["dkms"]
    assert "DKMS cleanup failed" in capsys.readouterr().out


def test_existing_registration_without_source_is_never_removed(setup):
    setup.dkms_status = "spoolbuddy-hx711/6.18.0: broken\n"
    with pytest.raises(RuntimeError, match="already registered"):
        setup.p.install()
    assert not setup.p.SOURCE.exists()
    assert not setup.p.STATE.exists()
    assert not any(call[:2] in (("dkms", "add"), ("dkms", "remove")) for call in setup.calls)


def test_status_inspection_failure_stops_before_copying_source(setup):
    setup.fail = ("dkms", "status")
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert not setup.p.SOURCE.exists()
    assert not any(call[:2] == ("dkms", "remove") for call in setup.calls)


def test_partial_source_copy_failure_does_not_leave_unowned_files(setup, monkeypatch):
    def fail_copy(source, destination):
        destination.mkdir()
        (destination / "hx711.c").write_text("partial")
        raise OSError("simulated copy failure")

    monkeypatch.setattr(setup.p.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="copy failure"):
        setup.p.install()
    assert not setup.p.SOURCE.exists()
    assert not setup.p.STATE.exists()
    assert not any(call[:2] in (("dkms", "add"), ("dkms", "remove")) for call in setup.calls)


def test_missing_sensor_does_not_block_application_start(setup, capsys):
    setup.hardware_ready = False
    setup.active = True
    setup.p.install()
    assert setup.p.STATE.exists()
    assert "NFC can still start" in capsys.readouterr().out
    assert setup.calls[-1] == ("systemctl", "start", "spoolbuddy.service")


@pytest.mark.parametrize("pin", [0, 1, 2, 3, 7, 8, 9, 10, 11, 23, 24, 25])
def test_reserved_pins_rejected_before_os_changes(setup, pin):
    with pytest.raises(ValueError):
        setup.p.install(pin, 6)
    assert setup.calls == []


def test_missing_header_install_failure_prevents_boot_configuration(setup):
    pending = setup.p.MODULES / "6.18.51+rpt-rpi-v8"
    (pending / "kernel").mkdir(parents=True)
    setup.fail = ("apt-get", "install")
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert ("apt-get", "install", "-y", "linux-headers-6.18.51+rpt-rpi-v8") in setup.calls
    assert not setup.p.STATE.exists()


def test_removed_kernel_directory_does_not_require_archived_headers(setup):
    (setup.p.MODULES / "6.18.1+rpt-rpi-v8").mkdir()
    setup.p.install()
    assert not any(call[0] == "apt-get" for call in setup.calls)
    builds = [call[-1] for call in setup.calls if call[:2] == ("dkms", "install")]
    assert builds == ["6.18.50+rpt-rpi-v8"]


def test_switching_to_nau7802_removes_only_managed_hardware(setup):
    setup.p.install()
    setup.calls.clear()
    setup.p.remove()
    assert ("systemctl", "disable", "--now", setup.p.UNIT) in setup.calls
    assert ("dkms", "remove", setup.p.MODULE, "--all") in setup.calls
    assert not setup.p.STATE.exists()
    assert not setup.p.SOURCE.exists()
    assert not any(path.exists() for path in setup.p.ASSETS.values())


def test_nau7802_without_managed_hx711_is_untouched(setup):
    setup.p.remove()
    assert setup.calls == []


def test_unmanaged_service_is_not_overwritten(setup):
    unit = setup.p.ASSETS[setup.p.UNIT]
    unit.parent.mkdir()
    unit.write_text("existing local trial")
    with pytest.raises(RuntimeError, match="unmanaged"):
        setup.p.install()
    assert unit.read_text() == "existing local trial"
    assert setup.calls == []


def test_modified_managed_service_is_not_deleted(setup):
    setup.p.install()
    unit = setup.p.ASSETS[setup.p.UNIT]
    unit.write_text("user changes")
    setup.calls.clear()
    with pytest.raises(RuntimeError, match="modified"):
        setup.p.remove()
    assert unit.read_text() == "user changes"
    assert setup.calls == []


def test_symlink_target_is_rejected(setup, tmp_path):
    target = tmp_path / "unrelated"
    target.write_text("keep")
    unit = setup.p.ASSETS[setup.p.UNIT]
    unit.parent.mkdir()
    unit.symlink_to(target)
    with pytest.raises(RuntimeError):
        setup.p.install()
    assert target.read_text() == "keep"


@pytest.mark.parametrize(
    "release,expected",
    [
        ("6.18.50+rpt-rpi-v8", "v8"),
        ("6.18.50+rpt-rpi-2712", "2712"),
        ("6.12.109+rpt-rpi-v8", "v8"),
        ("6.12.109+rpt-rpi-2712", "2712"),
    ],
)
def test_supported_kernel_flavours(setup, release, expected):
    assert setup.p.kernel_flavour(release) == expected


@pytest.mark.parametrize("release", ["6.18-generic", "6.18+rpt-rpi-v8-rt", "../../kernel-rpi-v8"])
def test_unsupported_kernel_flavours(setup, release):
    with pytest.raises(RuntimeError):
        setup.p.kernel_flavour(release)


def test_stock_module_replaces_managed_dkms_on_repeat_install(setup):
    setup.p.install()
    setup.active = True
    setup.calls.clear()
    add_stock(setup)
    setup.p.install()
    assert ("modinfo", "-F", "filename", str(stock_path(setup))) in setup.calls
    assert ("dkms", "remove", setup.p.MODULE, "--all") in setup.calls
    assert not any(c[:2] == ("dkms", "install") for c in setup.calls)
    assert setup.calls.index(("systemctl", "stop", setup.p.UNIT)) < setup.calls.index(("modprobe", "-r", "hx711"))
    assert not setup.p.SOURCE.exists()
    assert not setup.p.read_state()["dkms"]
    assert setup.calls[-1] == ("systemctl", "start", "spoolbuddy.service")
    setup.calls.clear()
    setup.p.install()
    assert not any(c[0] == "dkms" for c in setup.calls)


def test_stock_migration_activation_failure_restores_dkms_and_state(setup):
    setup.p.install()
    before = setup.p.STATE.read_bytes()
    add_stock(setup)
    setup.fail = ("systemctl", "enable")
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert setup.p.STATE.read_bytes() == before
    assert setup.p.SOURCE.exists()
    assert setup.p.read_state()["dkms"]
    assert ("dkms", "install", setup.p.MODULE, "-k", "6.18.50+rpt-rpi-v8") in setup.calls


def test_foreign_module_shadowing_stock_aborts_migration(setup):
    setup.p.install()
    before = setup.p.STATE.read_bytes()
    add_stock(setup)
    setup.shadowed = True
    with pytest.raises(RuntimeError, match="shadowed"):
        setup.p.install()
    assert setup.p.STATE.read_bytes() == before
    assert setup.p.read_state()["dkms"]


def test_dkms_module_alone_is_not_mistaken_for_stock(setup):
    setup.p.install()
    setup.calls.clear()
    setup.stock = True  # modinfo by name succeeds, but only updates/dkms exists.
    setup.shadowed = True
    setup.p.install()
    assert not any(c[:2] == ("dkms", "remove") for c in setup.calls)
    assert setup.p.read_state()["dkms"]


def test_stock_symlink_outside_kernel_tree_is_not_trusted(setup, tmp_path):
    add_stock(setup)
    path = stock_path(setup)
    path.unlink()
    foreign = tmp_path / "foreign.ko"
    foreign.touch()
    path.symlink_to(foreign)
    assert setup.p.stock_module("6.18.50+rpt-rpi-v8") is None


def test_fresh_install_preserves_foreign_driver_that_shadows_stock(setup):
    add_stock(setup)
    setup.shadowed = True
    with pytest.raises(RuntimeError, match="shadowed"):
        setup.p.install()
    assert not setup.p.STATE.exists()
    assert not any(c[0] == "dkms" for c in setup.calls)


@pytest.mark.parametrize("failure", [("dkms", "remove"), ("modprobe", "-r")])
def test_migration_failure_keeps_previous_ownership_and_source(setup, failure):
    setup.p.install()
    before = setup.p.STATE.read_bytes()
    add_stock(setup)
    setup.fail = failure
    with pytest.raises(subprocess.CalledProcessError):
        setup.p.install()
    assert setup.p.STATE.read_bytes() == before
    assert setup.p.read_state()["dkms"]
