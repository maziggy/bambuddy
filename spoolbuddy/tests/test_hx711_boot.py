"""Run the actual boot shell logic with filesystem and hardware commands isolated."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "install/hx711/setup.sh"


@pytest.fixture
def boot(tmp_path):
    bins = tmp_path / "bin"
    bins.mkdir()
    node = tmp_path / "sys/firmware/devicetree/base/spoolbuddy-hx711"
    marker = tmp_path / "run/spoolbuddy-hx711-overlay"
    marker.parent.mkdir()
    options = tmp_path / "options.json"
    calls = tmp_path / "calls.jsonl"
    options.write_text("{}")
    fake = bins / "hardware"
    fake.write_text(
        f"#!{sys.executable}\n"
        """import json
import os
import sys
import time
from pathlib import Path

root = Path(os.environ['HX711_BOOT_TEST_ROOT'])
options = json.loads((root / 'options.json').read_text())
name = Path(sys.argv[0]).name
with (root / 'calls.jsonl').open('a') as output:
    output.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
node = root / 'sys/firmware/devicetree/base/spoolbuddy-hx711'
raw = root / 'sys/bus/platform/devices/spoolbuddy-hx711/iio:device0/in_voltage0_raw'
if name == 'modprobe':
    sys.exit(1 if options.get('module_error') else 0)
elif name == 'dtoverlay':
    if sys.argv[1] == '-r':
        if options.get('remove_error'):
            sys.exit(1)
        node.rmdir()
        raw.unlink(missing_ok=True)
    else:
        node.mkdir(parents=True)
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text('8388608\\n')
        if options.get('overlay_error'):
            sys.exit(1)
elif name == 'cat':
    time.sleep(options.get('read_delay', 0))
    if options.get('read_error'):
        sys.exit(1)
    print(raw.read_text(), end='')
elif name == 'sleep':
    time.sleep(float(sys.argv[1]))
"""
    )
    fake.chmod(0o755)
    for name in ("modprobe", "dtoverlay", "cat", "sleep"):
        (bins / name).symlink_to(fake)
    script = tmp_path / "setup.sh"
    text = SCRIPT.read_text().replace("/sys/", str(tmp_path / "sys") + "/")
    text = text.replace("/run/spoolbuddy-hx711-overlay", str(marker))
    text = text.replace("/usr/local/lib/spoolbuddy-hx711", str(tmp_path / "library"))
    # Shorten only the probe budget; execute the production control flow.
    script.write_text(text.replace("SECONDS + 5", "SECONDS + 1"))

    def run(action):
        return subprocess.run(
            ["bash", str(script), action],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "PATH": f"{bins}:{os.environ['PATH']}", "HX711_BOOT_TEST_ROOT": str(tmp_path)},
        )

    return SimpleNamespace(
        run=run,
        node=node,
        marker=marker,
        calls=lambda: [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else [],
        configure=lambda **kwargs: options.write_text(json.dumps(kwargs)),
    )


def test_start_and_stop_release_only_owned_overlay(boot):
    assert boot.run("start").returncode == 0
    assert boot.marker.exists() and boot.node.exists()
    assert boot.run("stop").returncode == 0
    assert not boot.marker.exists() and not boot.node.exists()
    before = boot.calls()
    assert boot.run("stop").returncode == 0
    assert boot.calls() == before  # ExecStopPost after ExecStop is harmless.


def test_preexisting_unmanaged_overlay_survives_failed_start(boot):
    boot.node.mkdir(parents=True)
    assert boot.run("start").returncode != 0
    assert boot.run("stop").returncode == 0
    assert boot.node.exists()
    assert boot.calls() == []


def test_missing_module_does_not_claim_overlay_ownership(boot):
    boot.configure(module_error=True)
    assert boot.run("start").returncode != 0
    assert not boot.marker.exists()
    assert boot.run("stop").returncode == 0
    assert boot.calls() == [["modprobe", "hx711"]]


def test_partially_applied_overlay_is_cleaned_after_failed_start(boot):
    boot.configure(overlay_error=True)
    assert boot.run("start").returncode != 0
    assert boot.marker.exists()
    assert boot.run("stop").returncode == 0
    assert not boot.node.exists() and not boot.marker.exists()


def test_slow_failed_reads_do_not_multiply_the_startup_wait(boot):
    boot.configure(read_error=True, read_delay=1.1)
    result = boot.run("start")
    assert result.returncode != 0
    assert "check scale wiring and power" in result.stderr
    assert sum(call[0] == "cat" for call in boot.calls()) == 1
    assert boot.run("stop").returncode == 0
    assert not boot.node.exists()


def test_cleanup_failure_keeps_marker_for_retry(boot):
    assert boot.run("start").returncode == 0
    boot.configure(remove_error=True)
    assert boot.run("stop").returncode != 0
    assert boot.marker.exists() and boot.node.exists()
    boot.configure()
    assert boot.run("stop").returncode == 0
    assert not boot.node.exists() and not boot.marker.exists()
