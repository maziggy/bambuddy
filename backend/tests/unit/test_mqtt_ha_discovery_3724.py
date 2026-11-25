"""Home Assistant MQTT discovery for the relay (#3724).

The relay tells Home Assistant how to read the printer status it already
publishes: one retained device config per printer and one per AMS unit, sent
again when Home Assistant or the broker connection comes back, and cleared
when what they describe is gone.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import mqtt_ha_discovery as ha_discovery
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.mqtt_relay import MQTTRelayService

PRINTER_TOPIC = "homeassistant/device/bambuddy_SERIAL1/config"
AMS0_TOPIC = "homeassistant/device/bambuddy_SERIAL1_ams_0/config"
AMS1_TOPIC = "homeassistant/device/bambuddy_SERIAL1_ams_1/config"
STATUS_TOPIC = "bambuddy/printers/SERIAL1/status"


def _ams(ams_id: int, trays: int = 4) -> dict:
    return {
        "id": ams_id,
        "humidity": 4,
        "temp": 25.0,
        "tray": [{"id": i, "tray_type": "PLA", "tray_color": "FF0000FF"} for i in range(trays)],
    }


def _state(ams: list | None = None, **kwargs) -> PrinterState:
    raw = {"ams": ams} if ams is not None else {}
    return PrinterState(connected=True, state="RUNNING", raw_data=raw, **kwargs)


@pytest.fixture
def relay():
    service = MQTTRelayService()
    service.enabled = True
    service.connected = True
    service.topic_prefix = "bambuddy"
    service.ha_discovery_enabled = True
    service._publish = MagicMock()
    service._publish_clear = MagicMock(return_value=None)
    return service


async def _send(relay, state, name="My P1S", model="P1S"):
    # Each call stands for a later status: the throttle is not what is tested here.
    relay._last_printer_status.clear()
    await relay.on_printer_status(1, state, name, "SERIAL1", printer_model=model)


def _configs(relay) -> dict[str, dict]:
    """Discovery configs published so far, the last one per topic."""
    return {
        call.args[0]: call.args[1]
        for call in relay._publish.call_args_list
        if call.args[0].startswith("homeassistant/")
    }


def _config_topics(relay) -> list[str]:
    return [call.args[0] for call in relay._publish.call_args_list if call.args[0].startswith("homeassistant/")]


def _cleared(relay) -> set[str]:
    return {call.args[0] for call in relay._publish_clear.call_args_list}


class TestPublish:
    async def test_nothing_is_published_while_discovery_is_off(self, relay):
        relay.ha_discovery_enabled = False
        await _send(relay, _state([_ams(0)]))

        assert _config_topics(relay) == []

    async def test_a_printer_and_each_ams_get_their_own_retained_config(self, relay):
        await _send(relay, _state([_ams(0), _ams(1)]))

        assert _config_topics(relay) == [PRINTER_TOPIC, AMS0_TOPIC, AMS1_TOPIC]
        for call in relay._publish.call_args_list:
            assert call.kwargs == {"retain": True}

    async def test_the_config_follows_the_first_status(self, relay):
        await _send(relay, _state([_ams(0)]))

        topics = [call.args[0] for call in relay._publish.call_args_list]
        assert topics.index(STATUS_TOPIC) < topics.index(PRINTER_TOPIC)

    @pytest.mark.parametrize(
        "state",
        [
            PrinterState(connected=False, state="RUNNING"),
            # Connected, but the printer has not reported yet: no AMS layout.
            PrinterState(connected=True),
        ],
    )
    async def test_a_printer_that_has_not_reported_is_not_announced(self, relay, state):
        await _send(relay, state)

        assert _config_topics(relay) == []

    async def test_the_printer_device_names_model_firmware_and_serial(self, relay):
        await _send(relay, _state(firmware_version="01.08.00.00"))

        config = _configs(relay)[PRINTER_TOPIC]
        assert config["device"] == {
            "identifiers": ["bambuddy_SERIAL1"],
            "name": "My P1S",
            "manufacturer": "Bambu Lab",
            "serial_number": "SERIAL1",
            "model": "P1S",
            "sw_version": "01.08.00.00",
        }
        assert config["origin"]["name"] == "Bambuddy"
        assert config["state_topic"] == STATUS_TOPIC

    async def test_an_ams_names_the_printer_as_its_via_device(self, relay):
        await _send(relay, _state([_ams(0)]))

        device = _configs(relay)[AMS0_TOPIC]["device"]
        assert device["via_device"] == "bambuddy_SERIAL1"
        assert device["identifiers"] == ["bambuddy_SERIAL1_ams_0"]
        assert device["name"] == "My P1S AMS 1"

    async def test_an_ams_has_a_sensor_per_slot(self, relay):
        await _send(relay, _state([_ams(0)]))

        components = _configs(relay)[AMS0_TOPIC]["components"]
        assert {"humidity", "temperature", "slot_1", "slot_2", "slot_3", "slot_4"} <= set(components)
        assert "selectattr('id', 'eq', 0)" in components["slot_1"]["value_template"]

    async def test_every_entity_has_a_unique_id_and_reads_the_status_topic(self, relay):
        await _send(relay, _state([_ams(0)]))

        unique_ids = []
        for config in _configs(relay).values():
            for component in config["components"].values():
                if component["platform"] == "device_automation":
                    continue
                unique_ids.append(component["unique_id"])
                # Only the events have a topic of their own.
                if component["platform"] != "event":
                    assert "state_topic" not in component
        assert len(unique_ids) == len(set(unique_ids))

    async def test_availability_needs_bambuddy_and_the_printer(self, relay):
        await _send(relay, _state())

        config = _configs(relay)[PRINTER_TOPIC]
        assert config["availability_mode"] == "all"
        assert [entry["topic"] for entry in config["availability"]] == ["bambuddy/status", STATUS_TOPIC]
        # The connectivity sensor has to stay readable while the printer is offline.
        online = config["components"]["online"]
        assert [entry["topic"] for entry in online["availability"]] == ["bambuddy/status"]

    async def test_events_use_the_existing_topics(self, relay):
        await _send(relay, _state())

        components = _configs(relay)[PRINTER_TOPIC]["components"]
        triggers = {c["type"]: c["topic"] for c in components.values() if c["platform"] == "device_automation"}
        assert triggers == {
            "print_started": "bambuddy/printers/SERIAL1/print/started",
            "print_completed": "bambuddy/printers/SERIAL1/print/completed",
            "print_failed": "bambuddy/printers/SERIAL1/print/failed",
            "error": "bambuddy/printers/SERIAL1/error",
            "filament_low": "bambuddy/printers/SERIAL1/filament/low",
        }
        print_event = components["print_event"]
        assert print_event["state_topic"] == "bambuddy/printers/SERIAL1/print/+"
        assert print_event["event_types"] == ["started", "completed", "failed"]
        assert "event_type" in print_event["value_template"]

    async def test_nothing_can_be_commanded(self, relay):
        await _send(relay, _state([_ams(0)]))

        assert "command_topic" not in json.dumps(_configs(relay))

    @pytest.mark.parametrize(
        ("model", "chamber", "door"),
        [("P1S", False, False), ("X1C", True, True), ("A1 Mini", False, False), ("H2D", True, True)],
    )
    async def test_chamber_and_door_only_on_models_that_have_them(self, relay, model, chamber, door):
        await _send(relay, _state(), model=model)

        components = _configs(relay)[PRINTER_TOPIC]["components"]
        assert ("chamber_temperature" in components) is chamber
        assert ("door" in components) is door

    async def test_a_second_nozzle_gets_its_own_temperature(self, relay):
        await _send(relay, _state(temperatures={"nozzle": 220.0, "nozzle_2": 210.0}), model="H2D")

        assert "nozzle_2_temperature" in _configs(relay)[PRINTER_TOPIC]["components"]

    async def test_the_status_carries_what_the_device_is_built_from(self, relay):
        await _send(relay, _state(firmware_version="01.08.00.00"))

        payload = next(c.args[1] for c in relay._publish.call_args_list if c.args[0] == STATUS_TOPIC)
        assert payload["model"] == "P1S"
        assert payload["firmware_version"] == "01.08.00.00"
        assert "stg_cur_name" in payload


class TestResend:
    async def test_an_unchanged_printer_is_announced_once(self, relay):
        await _send(relay, _state([_ams(0)]))
        await _send(relay, _state([_ams(0)], progress=50))

        assert _config_topics(relay) == [PRINTER_TOPIC, AMS0_TOPIC]

    async def test_a_rename_is_published_for_the_printer_and_its_ams(self, relay):
        await _send(relay, _state([_ams(0)]))
        relay._publish.reset_mock()

        await _send(relay, _state([_ams(0)]), name="Workshop")

        configs = _configs(relay)
        assert configs[PRINTER_TOPIC]["device"]["name"] == "Workshop"
        assert configs[AMS0_TOPIC]["device"]["name"] == "Workshop AMS 1"

    async def test_an_added_ams_is_published_without_touching_the_rest(self, relay):
        await _send(relay, _state([_ams(0)]))
        relay._publish.reset_mock()

        await _send(relay, _state([_ams(0), _ams(1)]))

        assert _config_topics(relay) == [AMS1_TOPIC]
        assert _cleared(relay) == set()

    async def test_home_assistant_coming_online_gets_every_config_again(self, relay):
        await _send(relay, _state([_ams(0)]))
        sent = _configs(relay)
        relay._publish.reset_mock()

        relay._on_message(None, None, SimpleNamespace(topic="homeassistant/status", payload=b"online"))

        assert _configs(relay) == sent
        for call in relay._publish.call_args_list:
            assert call.kwargs == {"retain": True}

    async def test_home_assistant_going_offline_sends_nothing(self, relay):
        await _send(relay, _state())
        relay._publish.reset_mock()

        relay._on_message(None, None, SimpleNamespace(topic="homeassistant/status", payload=b"offline"))

        relay._publish.assert_not_called()

    async def test_a_reconnect_subscribes_and_sends_every_config_again(self, relay):
        await _send(relay, _state([_ams(0)]))
        sent = _configs(relay)
        relay._publish.reset_mock()
        client = MagicMock()

        relay._on_connect(client, None, {}, 0)

        subscribed = [topic for topic, _qos in client.subscribe.call_args.args[0]]
        assert subscribed == ["homeassistant/status", "homeassistant/device/+/config"]
        assert _configs(relay) == sent

    async def test_a_connect_without_discovery_subscribes_to_nothing(self, relay):
        relay.ha_discovery_enabled = False
        client = MagicMock()

        relay._on_connect(client, None, {}, 0)

        client.subscribe.assert_not_called()

    async def test_a_custom_discovery_prefix_is_used_throughout(self, relay):
        relay.ha_discovery_prefix = "ha"
        await _send(relay, _state([_ams(0)]))

        assert [call.args[0] for call in relay._publish.call_args_list if "/device/" in call.args[0]] == [
            "ha/device/bambuddy_SERIAL1/config",
            "ha/device/bambuddy_SERIAL1_ams_0/config",
        ]


class TestClear:
    async def test_a_removed_ams_is_cleared(self, relay):
        await _send(relay, _state([_ams(0), _ams(1)]))
        relay._publish.reset_mock()

        await _send(relay, _state([_ams(0)]))

        assert _cleared(relay) == {AMS1_TOPIC}
        assert _config_topics(relay) == []

    async def test_a_cleared_ams_is_not_sent_again_on_a_reconnect(self, relay):
        await _send(relay, _state([_ams(0), _ams(1)]))
        await _send(relay, _state([_ams(0)]))
        relay._publish.reset_mock()

        relay._on_connect(MagicMock(), None, {}, 0)

        assert AMS1_TOPIC not in _config_topics(relay)

    async def test_a_deleted_printer_is_cleared_with_its_ams(self, relay):
        await _send(relay, _state([_ams(0)]))

        await relay.on_printer_removed("SERIAL1")

        assert _cleared(relay) == {PRINTER_TOPIC, AMS0_TOPIC}
        assert relay._discovery_sent == {}

    async def test_deleting_one_printer_leaves_the_other(self, relay):
        await _send(relay, _state([_ams(0)]))
        relay._last_printer_status.clear()
        await relay.on_printer_status(2, _state(), "Other", "SERIAL10", printer_model="A1")

        await relay.on_printer_removed("SERIAL1")

        assert _cleared(relay) == {PRINTER_TOPIC, AMS0_TOPIC}
        assert list(relay._discovery_sent) == ["homeassistant/device/bambuddy_SERIAL10/config"]

    async def test_a_printer_deleted_before_it_reported_is_still_cleared(self, relay):
        # Its config is on the broker from before a restart.
        await relay.on_printer_removed("SERIAL1")

        assert _cleared(relay) == {PRINTER_TOPIC}

    async def test_deleting_a_printer_clears_nothing_while_discovery_is_off(self, relay):
        relay.ha_discovery_enabled = False

        await relay.on_printer_removed("SERIAL1")

        relay._publish_clear.assert_not_called()

    async def test_a_config_left_on_the_broker_is_cleared_too(self, relay):
        """After a restart nothing has been published yet, but the broker still
        holds the retained configs: they come back over the subscription."""
        config = {"state_topic": STATUS_TOPIC}
        relay._on_message(None, None, SimpleNamespace(topic=AMS1_TOPIC, payload=json.dumps(config).encode()))

        await _send(relay, _state([_ams(0)]))

        assert _cleared(relay) == {AMS1_TOPIC}

    async def test_a_config_that_arrives_after_the_printer_reported_is_cleared_too(self, relay):
        """The broker answers the subscription when it gets to it."""
        await _send(relay, _state([_ams(0)]))
        config = {"state_topic": STATUS_TOPIC}
        relay._on_message(None, None, SimpleNamespace(topic=AMS1_TOPIC, payload=json.dumps(config).encode()))
        relay._publish.reset_mock()

        await _send(relay, _state([_ams(0)]))

        assert _cleared(relay) == {AMS1_TOPIC}
        assert _config_topics(relay) == []

    async def test_our_own_config_coming_back_changes_nothing(self, relay):
        await _send(relay, _state([_ams(0)]))
        config = json.dumps(relay._discovery_sent[AMS0_TOPIC]).encode()
        relay._on_message(None, None, SimpleNamespace(topic=AMS0_TOPIC, payload=config))
        relay._publish.reset_mock()

        await _send(relay, _state([_ams(0)]))

        assert _config_topics(relay) == []
        relay._publish_clear.assert_not_called()

    async def test_another_instances_config_is_left_alone(self, relay):
        config = {"state_topic": "other/printers/SERIAL9/status"}
        topic = "homeassistant/device/bambuddy_SERIAL9/config"
        relay._on_message(None, None, SimpleNamespace(topic=topic, payload=json.dumps(config).encode()))
        relay._on_message(
            None, None, SimpleNamespace(topic="homeassistant/device/zigbee_1/config", payload=json.dumps(config))
        )

        await relay._clear_discovery()

        relay._publish_clear.assert_not_called()


def _settings(**overrides) -> dict:
    return {
        "mqtt_enabled": True,
        "mqtt_broker": "broker.local",
        "mqtt_topic_prefix": "bambuddy",
        "mqtt_ha_discovery_enabled": True,
        "mqtt_ha_discovery_prefix": "homeassistant",
        **overrides,
    }


class TestConfigure:
    @pytest.fixture
    def configured(self, relay):
        """A relay that has announced a printer, and whose reconfigure does not
        touch a broker."""
        relay.client = None
        relay._connect = AsyncMock(return_value=True)
        relay._configure_smart_plug_service = AsyncMock()
        with patch.object(MQTTRelayService, "_configure_panda_breath", AsyncMock()):
            yield relay

    async def test_saving_unchanged_settings_clears_nothing(self, configured):
        await _send(configured, _state([_ams(0)]))

        await configured.configure(_settings())

        configured._publish_clear.assert_not_called()
        assert set(configured._discovery_sent) == {PRINTER_TOPIC, AMS0_TOPIC}

    @pytest.mark.parametrize(
        "change",
        [
            {"mqtt_ha_discovery_enabled": False},
            {"mqtt_ha_discovery_prefix": "ha"},
            {"mqtt_topic_prefix": "printers"},
            {"mqtt_enabled": False},
        ],
        ids=["toggle off", "discovery prefix", "topic prefix", "relay off"],
    )
    async def test_the_old_configs_are_cleared(self, configured, change):
        await _send(configured, _state([_ams(0)]))

        await configured.configure(_settings(**change))

        assert _cleared(configured) == {PRINTER_TOPIC, AMS0_TOPIC}
        assert configured._discovery_sent == {}

    async def test_the_printer_is_announced_under_the_new_prefix(self, configured):
        await _send(configured, _state())
        await configured.configure(_settings(mqtt_ha_discovery_prefix="ha"))
        configured._publish.reset_mock()

        await _send(configured, _state())

        assert _config_topics(configured) == []
        assert [c.args[0] for c in configured._publish.call_args_list if c.args[0].startswith("ha/")] == [
            "ha/device/bambuddy_SERIAL1/config"
        ]

    async def test_the_ams_is_cleared_before_its_printer(self, configured):
        await _send(configured, _state([_ams(0)]))

        await configured.configure(_settings(mqtt_ha_discovery_enabled=False))

        assert [call.args[0] for call in configured._publish_clear.call_args_list] == [AMS0_TOPIC, PRINTER_TOPIC]

    async def test_equal_prefixes_turn_discovery_off(self, configured):
        configured.ha_discovery_enabled = False

        await configured.configure(_settings(mqtt_topic_prefix="homeassistant/"))

        assert configured.ha_discovery_enabled is False

    async def test_a_blank_discovery_prefix_falls_back_to_the_default(self, configured):
        await configured.configure(_settings(mqtt_ha_discovery_prefix="  "))

        assert configured.ha_discovery_prefix == "homeassistant"
        assert configured.ha_discovery_enabled is True


class TestTopics:
    def test_only_our_discovery_topics_are_recognised(self):
        assert ha_discovery.device_id_from_topic("homeassistant", PRINTER_TOPIC) == "bambuddy_SERIAL1"
        assert ha_discovery.device_id_from_topic("homeassistant", "homeassistant/device/zigbee_1/config") is None
        assert ha_discovery.device_id_from_topic("homeassistant", "homeassistant/status") is None
        assert ha_discovery.device_id_from_topic("ha", PRINTER_TOPIC) is None

    def test_a_printer_does_not_own_another_whose_serial_starts_the_same(self):
        assert ha_discovery.belongs_to_printer("bambuddy_SERIAL1_ams_0", "SERIAL1")
        assert not ha_discovery.belongs_to_printer("bambuddy_SERIAL10", "SERIAL1")
        assert not ha_discovery.belongs_to_printer("bambuddy_SERIAL10_ams_0", "SERIAL1")

    def test_an_ams_ht_is_numbered_from_one(self):
        assert ha_discovery.ams_unit_name({"id": 128, "is_ams_ht": True}) == "AMS HT 1"
        assert ha_discovery.ams_unit_name({"id": 1}) == "AMS 2"
