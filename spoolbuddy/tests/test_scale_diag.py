"""The existing CLI diagnostic must follow the configured scale board."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/scale_diag.py"
    spec = importlib.util.spec_from_file_location("scale_diag_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SPOOLBUDDY_DIR", tmp_path)
    for key in tuple(os.environ):
        if key.startswith("SPOOLBUDDY_"):
            monkeypatch.delenv(key)
    # Track new values added by the parser so they do not leak between tests.
    monkeypatch.setattr(module.os, "environ", dict(os.environ))
    return module


def test_cli_uses_saved_hx711_selection_without_i2c(diagnostic, monkeypatch):
    (diagnostic.SPOOLBUDDY_DIR / ".env").write_text("SPOOLBUDDY_SCALE_DRIVER=hx711\n")
    hx711 = MagicMock()
    nau7802 = MagicMock()
    monkeypatch.setattr(diagnostic, "hx711_diagnostic", hx711)
    monkeypatch.setattr(diagnostic, "nau7802_diagnostic", nau7802)
    diagnostic.main()
    hx711.assert_called_once()
    nau7802.assert_not_called()


def test_cli_retains_nau7802_default(diagnostic, monkeypatch):
    nau7802 = MagicMock()
    monkeypatch.setattr(diagnostic, "nau7802_diagnostic", nau7802)
    diagnostic.main()
    nau7802.assert_called_once()


def test_environment_override_takes_precedence_and_secrets_are_not_loaded(diagnostic):
    (diagnostic.SPOOLBUDDY_DIR / ".env").write_text(
        "SPOOLBUDDY_SCALE_DRIVER=hx711\nSPOOLBUDDY_API_KEY=do-not-load\nSPOOLBUDDY_HX711_DATA_PIN=5\n"
    )
    diagnostic.os.environ["SPOOLBUDDY_SCALE_DRIVER"] = "nau7802"
    diagnostic.load_scale_environment()
    assert diagnostic.os.environ["SPOOLBUDDY_SCALE_DRIVER"] == "nau7802"
    assert "SPOOLBUDDY_API_KEY" not in diagnostic.os.environ
    assert diagnostic.os.environ["SPOOLBUDDY_HX711_DATA_PIN"] == "5"


def test_hx711_cli_samples_and_closes_reader(diagnostic, monkeypatch, capsys):
    from daemon import hx711

    reader = MagicMock()
    reader.diagnostic_info.return_value = "Channel: A; gain: 128"
    reader.read_raw.side_effect = range(-50, -40)
    factory = MagicMock(return_value=reader)
    monkeypatch.setattr(hx711, "HX711", factory)
    diagnostic.hx711_diagnostic()
    factory.assert_called_once_with(data_pin=5, clock_pin=6)
    assert reader.read_raw.call_count == 10
    reader.close.assert_called_once()
    output = capsys.readouterr().out
    assert "gain: 128" in output
    assert "Spread: 9" in output


def test_hx711_read_failure_is_reported_and_reader_closed(diagnostic, monkeypatch, capsys):
    from daemon import hx711

    diagnostic.os.environ["SPOOLBUDDY_SCALE_DRIVER"] = "hx711"
    reader = MagicMock()
    reader.init.side_effect = OSError("HX711 device missing")
    monkeypatch.setattr(hx711, "HX711", MagicMock(return_value=reader))
    with pytest.raises(SystemExit) as result:
        diagnostic.main()
    assert result.value.code == 1
    assert "HX711 device missing" in capsys.readouterr().err
    reader.close.assert_called_once()


def test_unknown_board_does_not_probe_nau7802(diagnostic, monkeypatch):
    diagnostic.os.environ["SPOOLBUDDY_SCALE_DRIVER"] = "unknown"
    probe = MagicMock()
    monkeypatch.setattr(diagnostic, "nau7802_diagnostic", probe)
    with pytest.raises(SystemExit):
        diagnostic.main()
    probe.assert_not_called()
