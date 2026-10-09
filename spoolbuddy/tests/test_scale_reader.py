"""Calibration and failure handling shared by NAU7802 and HX711."""

import sys
from unittest.mock import MagicMock

import pytest
from daemon.scale_reader import ScaleReader


@pytest.fixture
def hardware(monkeypatch):
    chip = MagicMock()
    module = MagicMock()
    module.HX711.return_value = chip
    monkeypatch.setitem(sys.modules, "daemon.hx711", module)
    return chip


def test_hx711_negative_calibration(hardware):
    hardware.read_raw.return_value = -202290
    scale = ScaleReader(-47570, -0.0024134678594906677, driver="hx711")
    assert scale.read() == (373.4, False, -202290)


def test_new_calibration_clears_old_weights(hardware):
    hardware.read_raw.return_value = 100
    scale = ScaleReader(driver="hx711")
    scale.read()
    scale.update_calibration(50, 2)
    hardware.read_raw.return_value = 200
    assert scale.read() == (300.0, False, 200)


def test_tare_accepts_zero_adc_and_clears_stability(hardware):
    hardware.read_raw.return_value = 0
    scale = ScaleReader(100, 1, driver="hx711")
    scale.read()
    assert scale.tare() == 0
    assert scale.read() == (0.0, False, 0)


def test_invalid_read_not_averaged_and_recovery_is_fresh(hardware):
    hardware.read_raw.side_effect = [100, TimeoutError("pulse"), 200]
    scale = ScaleReader(driver="hx711")
    scale.read()
    assert scale.read() is None
    assert not scale.ok
    assert scale.read() == (200.0, False, 200)
    assert scale.ok


def test_default_still_uses_nau7802(monkeypatch):
    module = MagicMock()
    monkeypatch.setitem(sys.modules, "daemon.nau7802", module)
    scale = ScaleReader()
    module.NAU7802.assert_called_once_with()
    assert scale.ok


def test_diagnostic_uses_existing_hardware(hardware):
    hardware.read_raw.side_effect = range(100, 110)
    scale = ScaleReader(driver="hx711")
    result = scale.diagnostic()
    assert "Samples: 10" in result
    assert "Spread: 9 counts" in result
    assert hardware.read_raw.call_count == 10


def test_diagnostic_preserves_calibration_and_weight_history(hardware):
    hardware.read_raw.return_value = 100
    hardware.diagnostic_info.return_value = "Channel: A; gain: 128"
    scale = ScaleReader(50, 2, driver="hx711")
    scale.read()
    history = list(scale._samples)
    hardware.read_raw.return_value = 200
    output = scale.diagnostic()
    assert "Channel: A; gain: 128" in output
    assert "Average: 200.0 counts" in output
    assert "Tare: 50" in output
    assert "Calibration: 2 g/count" in output
    assert list(scale._samples) == history
    assert scale.last_raw == 100


def test_failed_initialization_diagnostic_retains_actionable_error(hardware):
    hardware.init.side_effect = RuntimeError("No HX711 device; check spoolbuddy-hx711.service")
    scale = ScaleReader(driver="hx711")
    assert not scale.ok
    with pytest.raises(RuntimeError, match="check spoolbuddy-hx711.service"):
        scale.diagnostic()
    hardware.read_raw.assert_not_called()


def test_nau7802_transient_error_preserves_average_and_stability(monkeypatch):
    module = MagicMock()
    chip = module.NAU7802.return_value
    chip.read_raw.side_effect = [100, OSError("transient I2C error"), 200]
    monkeypatch.setitem(sys.modules, "daemon.nau7802", module)
    scale = ScaleReader()
    scale.read()
    history = list(scale._stability_history)
    assert scale.read() is None
    assert scale.ok
    assert list(scale._stability_history) == history
    assert scale.read() == (150.0, False, 200)


def test_hx711_initialization_failure_warns_and_marks_scale_unavailable(hardware, caplog):
    hardware.init.side_effect = RuntimeError("Check spoolbuddy-hx711.service")
    scale = ScaleReader(driver="hx711")
    assert not scale.ok
    assert any(r.levelname == "WARNING" and "spoolbuddy-hx711.service" in r.message for r in caplog.records)
