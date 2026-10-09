"""Exercise installer scale selection without running privileged installation."""

import os
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install/install.sh"
DEFINITIONS = INSTALLER.read_text().rsplit('\nmain "$@"', 1)[0]


def run_installer(tmp_path, commands, stdin=""):
    return subprocess.run(
        [
            "bash",
            "-c",
            DEFINITIONS
            + '\nINSTALL_PATH="$TEST_INSTALL_PATH"\nPYTHON_CMD=python3\nuname() { if [[ "$1" == -m ]]; then echo aarch64; else echo 6.18.50+rpt-rpi-v8; fi; }\n'
            + commands,
        ],
        input=stdin,
        text=True,
        capture_output=True,
        env={**os.environ, "TEST_INSTALL_PATH": str(tmp_path)},
        timeout=5,
    )


@pytest.mark.parametrize("driver", ["nau7802", "hx711"])
def test_explicit_selection_writes_environment_and_dependencies(tmp_path, driver):
    (tmp_path / "spoolbuddy").mkdir()
    result = run_installer(
        tmp_path,
        f"parse_args --scale-driver {driver} --yes\n"
        "choose_scale_driver\nchown() { :; }; chgrp() { :; }\n"
        'create_spoolbuddy_env\nprintf "PACKAGES=%s\\n" "$SYSTEM_PACKAGES"',
    )
    assert result.returncode == 0, result.stderr
    env = (tmp_path / "spoolbuddy/.env").read_text()
    assert f"SPOOLBUDDY_SCALE_DRIVER={driver}" in env
    assert "SPOOLBUDDY_HX711_DATA_PIN=5" in env
    assert "SPOOLBUDDY_HX711_CLOCK_PIN=6" in env
    assert "python3-lgpio" not in result.stdout
    assert ("linux-headers-rpi-v8" in result.stdout) == (driver == "hx711")


def test_unattended_fresh_install_keeps_nau7802_default(tmp_path):
    result = run_installer(tmp_path, 'parse_args --yes\nchoose_scale_driver\necho "SELECTED=$SCALE_DRIVER"')
    assert result.returncode == 0
    assert "SELECTED=nau7802" in result.stdout


def test_reinstall_preserves_hx711_and_custom_wiring(tmp_path):
    (tmp_path / "spoolbuddy").mkdir()
    (tmp_path / "spoolbuddy/.env").write_text(
        'SPOOLBUDDY_SCALE_DRIVER="hx711"\nSPOOLBUDDY_HX711_DATA_PIN=17\nSPOOLBUDDY_HX711_CLOCK_PIN=27\n'
    )
    result = run_installer(
        tmp_path,
        'parse_args --yes\nchoose_scale_driver\necho "SELECTED=$SCALE_DRIVER:$HX711_DATA_PIN:$HX711_CLOCK_PIN"',
    )
    assert result.returncode == 0, result.stderr
    assert "SELECTED=hx711:17:27" in result.stdout


def test_interactive_selection_retries_invalid_choice(tmp_path):
    result = run_installer(tmp_path, 'choose_scale_driver\necho "SELECTED=$SCALE_DRIVER"', "invalid\n2\n")
    assert result.returncode == 0
    assert "SELECTED=hx711" in result.stdout


@pytest.mark.parametrize("args", ["--scale-driver", "--scale-driver bogus"])
def test_bad_selection_fails_before_installation(tmp_path, args):
    result = run_installer(tmp_path, f"parse_args {args}\necho SHOULD_NOT_RUN")
    assert result.returncode != 0
    assert "SHOULD_NOT_RUN" not in result.stdout


def test_same_gpio_pins_rejected(tmp_path):
    (tmp_path / "spoolbuddy").mkdir()
    (tmp_path / "spoolbuddy/.env").write_text("SPOOLBUDDY_HX711_DATA_PIN=6\n")
    result = run_installer(tmp_path, "parse_args --scale-driver hx711 --yes\nchoose_scale_driver")
    assert result.returncode != 0
    assert "different GPIO" in result.stdout


@pytest.mark.parametrize("pin", [2, 3, 10, 23, 24, 25])
def test_hx711_cannot_claim_nfc_or_bus_pins(tmp_path, pin):
    result = run_installer(
        tmp_path, f"HX711_DATA_PIN={pin}\nparse_args --scale-driver hx711 --yes\nchoose_scale_driver"
    )
    assert result.returncode != 0
    assert "conflict" in result.stdout


def test_pi5_uses_2712_headers(tmp_path):
    result = run_installer(
        tmp_path,
        'uname() { if [[ "$1" == -m ]]; then echo aarch64; else echo 6.18.50+rpt-rpi-2712; fi; }\n'
        'parse_args --scale-driver hx711 --yes\nchoose_scale_driver\necho "$SYSTEM_PACKAGES"',
    )
    assert result.returncode == 0, result.stderr
    assert "linux-headers-rpi-2712" in result.stdout
    assert "linux-headers-rpi-v8" not in result.stdout


def test_reinstall_retains_identity_and_calibration(tmp_path):
    (tmp_path / "spoolbuddy").mkdir()
    env_file = tmp_path / "spoolbuddy/.env"
    env_file.write_text(
        "SPOOLBUDDY_DEVICE_ID=my-device\nSPOOLBUDDY_TARE_OFFSET=-123\n"
        "SPOOLBUDDY_CALIBRATION_FACTOR=-0.004\nSPOOLBUDDY_SCALE_DRIVER=nau7802\n"
    )
    result = run_installer(
        tmp_path,
        "parse_args --scale-driver hx711 --yes\nchoose_scale_driver\n"
        "chown() { :; }; chgrp() { :; }\n"
        "BAMBUDDY_URL=https://example.test\nAPI_KEY=test-key\ncreate_spoolbuddy_env",
    )
    assert result.returncode == 0, result.stderr
    content = env_file.read_text()
    assert "SPOOLBUDDY_DEVICE_ID=my-device" in content
    assert "SPOOLBUDDY_TARE_OFFSET=-123" in content
    assert "SPOOLBUDDY_CALIBRATION_FACTOR=-0.004" in content
    assert "SPOOLBUDDY_SCALE_DRIVER=hx711" in content
    assert "SPOOLBUDDY_SCALE_DRIVER=nau7802" not in content


def test_kernel_helper_receives_selected_pins(tmp_path):
    result = run_installer(tmp_path, "SCALE_DRIVER=hx711\nPYTHON_CMD=echo\nsetup_scale_hardware")
    assert result.returncode == 0, result.stderr
    assert "provision.py install --data-pin 5 --clock-pin 6" in result.stdout


def test_saved_prompt_values_are_kept_literal(tmp_path):
    # Reinstallation reads values owned by the service user. Root must not
    # evaluate shell substitutions embedded in those saved values.
    value = '$(touch "' + str(tmp_path / "executed") + '")'
    (tmp_path / "value").write_text(value)
    result = run_installer(
        tmp_path,
        'NON_INTERACTIVE=true\nsaved=$(cat "$INSTALL_PATH/value")\n'
        'prompt "Saved connection value" "$saved" API_KEY\nprintf "%s" "$API_KEY"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == value
    assert not (tmp_path / "executed").exists()


@pytest.mark.parametrize("mode", ["spoolbuddy", "full"])
@pytest.mark.parametrize("helper_status", [0, 1])
def test_normal_install_sequence_keeps_configuration_until_hardware_succeeds(tmp_path, mode, helper_status):
    """Run main with OS mutations stubbed, retaining real config/helper dispatch."""
    (tmp_path / "spoolbuddy").mkdir()
    config = tmp_path / "spoolbuddy/.env"
    saved = (
        "SPOOLBUDDY_DEVICE_ID=existing-device\n"
        "SPOOLBUDDY_TARE_OFFSET=-43000\n"
        "SPOOLBUDDY_CALIBRATION_FACTOR=-0.002\n"
        "SPOOLBUDDY_SCALE_DRIVER=nau7802\n"
    )
    config.write_text(saved)
    # Explicit stubs keep main's real ordering under test without running apt,
    # modifying /boot, creating users, downloading code, or rebooting the host.
    isolated = (
        "detect_installer_source_context check_root check_raspberry_pi check_raspberry_pi_os detect_python "
        "enable_spi enable_i2c configure_boot_config install_system_packages install_wifi_safeguard "
        "upgrade_system_packages strip_services strip_packages create_spoolbuddy_user download_spoolbuddy "
        "setup_kiosk setup_spoolbuddy_venv ensure_kiosk_env_access setup_ssh_key create_bambuddy_user "
        "setup_bambuddy_venv install_nodejs build_frontend create_bambuddy_directories create_bambuddy_env "
        "create_bambuddy_service bootstrap_spoolbuddy_kiosk_key reboot hostname chown chgrp"
    )
    commands = "\n".join(f"{name}() {{ :; }}" for name in isolated.split())
    commands += f"""
create_spoolbuddy_service() {{ touch "$INSTALL_PATH/service-created"; }}
kernel_test_python() {{
    if [[ "$1" == "$INSTALL_PATH/spoolbuddy/install/hx711/provision.py" ]]; then
        printf '%s\\n' "$*" > "$INSTALL_PATH/helper-call"
        cp "$INSTALL_PATH/spoolbuddy/.env" "$INSTALL_PATH/config-at-provision"
        return {helper_status}
    fi
    command python3 "$@"
}}
PYTHON_CMD=kernel_test_python
main --yes --mode {mode} --path "$INSTALL_PATH" --scale-driver hx711 \\
    --repo /test/source.git --ref test --bambuddy-url https://example.test --api-key test-key
"""
    result = run_installer(tmp_path, commands)
    assert (tmp_path / "config-at-provision").read_text() == saved
    assert "provision.py install --data-pin 5 --clock-pin 6" in (tmp_path / "helper-call").read_text()
    if helper_status:
        assert result.returncode != 0
        assert config.read_text() == saved
        assert not (tmp_path / "service-created").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert "SPOOLBUDDY_SCALE_DRIVER=hx711" in config.read_text()
        for line in saved.splitlines()[:3]:
            assert line in config.read_text()
        assert (tmp_path / "service-created").exists()
