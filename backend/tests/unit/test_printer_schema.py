"""Serial-number normalization on the printer schema (#1465).

Bambu serial numbers are uppercase alphanumeric and the MQTT report topic
``device/<serial>/report`` is case-sensitive. A serial entered in the wrong
case connects and subscribes without error but never receives a message, so
the schema normalizes it on input.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.printer import PrinterCreate, WLEDConfig, WLEDPresets


def _make(serial: str) -> PrinterCreate:
    return PrinterCreate(
        name="Test Printer",
        serial_number=serial,
        ip_address="192.168.1.50",
        access_code="12345678",
    )


def test_serial_number_uppercased():
    assert _make("01p00a3b1234567").serial_number == "01P00A3B1234567"


def test_serial_number_whitespace_stripped():
    assert _make("  01P00A3B1234567  ").serial_number == "01P00A3B1234567"


def test_serial_number_stripped_and_uppercased():
    assert _make(" 31b8c0ca1234567 ").serial_number == "31B8C0CA1234567"


def test_already_normalized_serial_unchanged():
    assert _make("31B8C0CA1234567").serial_number == "31B8C0CA1234567"


def test_blank_serial_number_rejected():
    with pytest.raises(ValidationError):
        _make("   ")


def test_wled_config_normalizes_url_and_accepts_optional_mappings():
    config = WLEDConfig(
        enabled=True,
        base_url=" http://wled.local/ ",
        presets=WLEDPresets(printing=3, paused=None),
        finished_timeout_seconds=120,
    )

    assert config.base_url == "http://wled.local"
    assert config.presets.printing == 3
    assert config.presets.paused is None


@pytest.mark.parametrize("preset", [0, 251])
def test_wled_preset_outside_supported_range_rejected(preset):
    with pytest.raises(ValidationError):
        WLEDPresets(idle=preset)


def test_enabled_wled_requires_url():
    with pytest.raises(ValidationError):
        WLEDConfig(enabled=True)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data",
        "http://user:password@wled.local",
    ],
)
def test_wled_url_uses_lan_service_safety_policy(url):
    with pytest.raises(ValidationError):
        WLEDConfig(base_url=url)


def test_wled_finished_timeout_is_bounded():
    with pytest.raises(ValidationError):
        WLEDConfig(finished_timeout_seconds=86401)
