"""Kernel HX711 device selection, signed samples and failure handling."""

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from daemon import hx711
from daemon.hx711 import HX711


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    monkeypatch.setattr(hx711, "SYSFS_ROOT", tmp_path)
    monkeypatch.setattr(hx711.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    (tmp_path / "bus/iio/devices").mkdir(parents=True)
    return tmp_path


def add_device(sysfs, index=0, data=5, clock=6, raw=8388608):
    parent = sysfs / f"devices/platform/scale{index}"
    device = parent / f"iio:device{index}"
    device.mkdir(parents=True)
    (sysfs / "bus/iio/devices" / device.name).symlink_to(device)
    node = parent / "of_node"
    node.mkdir()
    (node / "compatible").write_bytes(b"avia,hx711\0")
    (node / "dout-gpios").write_bytes(struct.pack(">III", 42, data, 0))
    (node / "sck-gpios").write_bytes(struct.pack(">III", 42, clock, 0))
    (device / "name").write_text("hx711\n")
    (device / "in_voltage0_scale_available").write_text("0.001537269 0.003074538\n")
    (device / "in_voltage_scale").write_text("0.001537269\n")
    (device / "in_voltage0_raw").write_text(f"{raw}\n")
    return device


@pytest.mark.parametrize("signed", [0, 1, -1, -43443, -90802, -202290, -8388607, 8388606])
def test_existing_signed_calibration_is_preserved(sysfs, signed):
    add_device(sysfs, raw=signed + 8388608)
    scale = HX711()
    scale.init()
    assert scale.data_ready()
    assert scale.read_raw() == signed


def test_selects_matching_gpio_pair_not_first_iio_device(sysfs):
    add_device(sysfs, data=17, clock=27, raw=123)
    add_device(sysfs, index=1, raw=8388609)
    scale = HX711()
    scale.init()
    assert scale.read_raw() == 1


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_device_fails(sysfs, count):
    for index in range(count):
        add_device(sysfs, index=index)
    with pytest.raises(RuntimeError, match=f"found {count}"):
        HX711().init()


@pytest.mark.parametrize("raw", ["0", "16777215", "16777216", "-1", "bad", "1.5", ""])
def test_corrupt_or_saturated_samples_are_rejected(sysfs, raw):
    device = add_device(sysfs)
    scale = HX711()
    scale.init()
    (device / "in_voltage0_raw").write_text(raw)
    with pytest.raises((OSError, ValueError)):
        scale.read_raw()


def test_read_failure_does_not_publish_stale_sample_and_can_recover(sysfs, monkeypatch):
    device = add_device(sysfs, raw=8388610)
    scale = HX711()
    scale.init()
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path.name == "in_voltage0_raw":
            raise PermissionError("read denied")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", denied)
        with pytest.raises(PermissionError):
            scale.read_raw()
    (device / "in_voltage0_raw").write_text("8388611")
    assert scale.read_raw() == 3


def test_device_removal_is_an_error(sysfs):
    device = add_device(sysfs)
    scale = HX711()
    scale.init()
    (device / "in_voltage0_raw").unlink()
    with pytest.raises(OSError, match="disappeared"):
        scale.data_ready()


def test_gain_change_is_rejected(sysfs):
    device = add_device(sysfs)
    scale = HX711()
    scale.init()
    (device / "in_voltage_scale").write_text("0.003074538")
    with pytest.raises(OSError, match="gain changed"):
        scale.read_raw()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "bad", ""])
def test_invalid_scale_fails_initialization(sysfs, value):
    device = add_device(sysfs)
    (device / "in_voltage_scale").write_text(value)
    scale = HX711()
    with pytest.raises(ValueError):
        scale.init()
    with pytest.raises(RuntimeError, match="closed"):
        scale.read_raw()


def test_close_is_idempotent_and_does_not_remove_kernel_device(sysfs):
    device = add_device(sysfs)
    scale = HX711()
    scale.init()
    scale.close()
    scale.close()
    assert device.exists()
    with pytest.raises(RuntimeError, match="closed"):
        scale.read_raw()


@pytest.mark.parametrize("args", [(5, 5), (-1, 6), (5, 28), (23, 6), (5, 11)])
def test_conflicting_pins_rejected(args):
    with pytest.raises(ValueError):
        HX711(*args)


def test_kernel_reader_through_existing_scale_calibration(sysfs):
    from daemon.scale_reader import ScaleReader

    device = add_device(sysfs, raw=8388608 - 43412)
    factor = -0.0024134678594906677
    scale = ScaleReader(driver="hx711", tare_offset=-43412, calibration_factor=factor)
    assert scale.ok
    (device / "in_voltage0_raw").write_text(str(8388608 - 90802))
    grams, _, raw = scale.read()
    assert raw == -90802
    assert grams == pytest.approx(round((-90802 + 43412) * factor, 1))
    (device / "in_voltage0_raw").write_text("0")
    assert scale.read() is None
    assert not scale.ok
    scale.close()


def test_failed_hardware_service_rejected_even_if_iio_device_exists(sysfs, monkeypatch):
    add_device(sysfs)
    monkeypatch.setattr(hx711.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match="spoolbuddy-hx711.service failed"):
        HX711().init()
