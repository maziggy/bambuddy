"""
Tests for subscribe_plug_to_mqtt — the shared helper that resolves a
SmartPlug row's per-type topic fields (with legacy fallback) and calls
MQTTSmartPlugService.subscribe().

Regression guard for #1010, where the startup-restore code path had
drifted from the create/update routes: it only looked at the legacy
`mqtt_topic` field and silently skipped plugs whose topics were set
only in the newer per-type fields, so the MQTT smart-plug subscription
was lost on every Bambuddy restart until the user re-saved the plug.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.app.services.mqtt_smart_plug import subscribe_plug_to_mqtt


def _plug(**overrides):
    """Build a SmartPlug-shaped record. All fields default to None/defaults."""
    defaults = {
        "id": 1,
        "mqtt_topic": None,
        "mqtt_power_topic": None,
        "mqtt_power_path": None,
        "mqtt_power_multiplier": None,
        "mqtt_energy_topic": None,
        "mqtt_energy_path": None,
        "mqtt_energy_multiplier": None,
        "mqtt_state_topic": None,
        "mqtt_state_path": None,
        "mqtt_state_on_value": None,
        "mqtt_multiplier": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_per_type_topics_restored_without_legacy_mqtt_topic():
    """#1010: plug configured only with per-type topics must still subscribe."""
    service = MagicMock()
    plug = _plug(
        id=42,
        mqtt_power_topic="shellies/plug-living/power",
        mqtt_power_path="value",
        mqtt_state_topic="shellies/plug-living/relay/0",
        mqtt_state_on_value="on",
    )

    topics = subscribe_plug_to_mqtt(service, plug)

    service.subscribe.assert_called_once()
    kwargs = service.subscribe.call_args.kwargs
    assert kwargs["plug_id"] == 42
    assert kwargs["power_topic"] == "shellies/plug-living/power"
    assert kwargs["power_path"] == "value"
    assert kwargs["state_topic"] == "shellies/plug-living/relay/0"
    assert kwargs["state_on_value"] == "on"
    # energy wasn't configured, so no per-type topic
    assert kwargs["energy_topic"] is None
    assert set(topics) == {"shellies/plug-living/power", "shellies/plug-living/relay/0"}


def test_legacy_single_topic_falls_back_for_all_data_types():
    """Backward-compat: a plug with only the legacy mqtt_topic must still work."""
    service = MagicMock()
    plug = _plug(
        id=7,
        mqtt_topic="zigbee2mqtt/shelly-office",
        mqtt_power_path="power",
        mqtt_energy_path="energy",
        mqtt_state_path="state",
        mqtt_state_on_value="ON",
        mqtt_multiplier=0.001,  # legacy
    )

    topics = subscribe_plug_to_mqtt(service, plug)

    kwargs = service.subscribe.call_args.kwargs
    assert kwargs["power_topic"] == "zigbee2mqtt/shelly-office"
    assert kwargs["energy_topic"] == "zigbee2mqtt/shelly-office"
    assert kwargs["state_topic"] == "zigbee2mqtt/shelly-office"
    # Legacy multiplier flows through for both power and energy.
    assert kwargs["power_multiplier"] == 0.001
    assert kwargs["energy_multiplier"] == 0.001
    assert topics == ["zigbee2mqtt/shelly-office"]


def test_per_type_multipliers_override_legacy():
    service = MagicMock()
    plug = _plug(
        mqtt_power_topic="t/power",
        mqtt_power_multiplier=0.5,
        mqtt_energy_topic="t/energy",
        mqtt_energy_multiplier=0.25,
        mqtt_multiplier=9.0,  # should be overridden by per-type values
    )

    subscribe_plug_to_mqtt(service, plug)

    kwargs = service.subscribe.call_args.kwargs
    assert kwargs["power_multiplier"] == 0.5
    assert kwargs["energy_multiplier"] == 0.25


def test_per_type_topics_beat_legacy_topic_when_both_set():
    """If both legacy and per-type topic are set, per-type wins."""
    service = MagicMock()
    plug = _plug(
        mqtt_topic="old/topic",
        mqtt_power_topic="new/power",
        mqtt_energy_topic="new/energy",
    )

    subscribe_plug_to_mqtt(service, plug)

    kwargs = service.subscribe.call_args.kwargs
    assert kwargs["power_topic"] == "new/power"
    assert kwargs["energy_topic"] == "new/energy"
    # state has no per-type topic set, so it falls back to legacy
    assert kwargs["state_topic"] == "old/topic"


def test_no_topics_configured_skips_subscribe():
    """Nothing to subscribe to means the service is not touched."""
    service = MagicMock()
    plug = _plug(id=99)  # all fields None

    topics = subscribe_plug_to_mqtt(service, plug)

    service.subscribe.assert_not_called()
    assert topics == []


def test_returns_unique_topic_list_when_same_topic_used_for_multiple_types():
    service = MagicMock()
    plug = _plug(
        mqtt_power_topic="shared/topic",
        mqtt_energy_topic="shared/topic",
        mqtt_state_topic="shared/topic",
    )

    topics = subscribe_plug_to_mqtt(service, plug)

    assert topics == ["shared/topic"]
