"""Choosing which of a printer's plugs is "the" power plug (#2830).

The printer card's Power row carries the power on/off and auto-off-after-print
controls, so it has to land on the plug that actually feeds the printer. It used
to take the first non-script row the database happened to return, which put an
exhaust fan -- flagged as not powering the printer, and explicitly hidden from
the card -- in front of the outlet the printer is plugged into.
"""

from types import SimpleNamespace

import pytest

from backend.app.api.routes.smart_plugs import (
    _can_be_switched,
    _is_script_plug,
    _main_plug_rank,
    _pick_main_plug,
    _reports_power,
)

pytestmark = pytest.mark.unit


def _plug(plug_id=1, **kwargs):
    """A plug with every flag at its model default, overridable per test."""
    defaults = {
        "id": plug_id,
        "name": f"Plug {plug_id}",
        "plug_type": "tasmota",
        "ha_entity_id": None,
        "ha_power_entity": None,
        "mqtt_topic": None,
        "mqtt_power_topic": None,
        "rest_power_path": None,
        "controls_printer_power": True,
        "enabled": True,
        "show_on_printer_card": True,
    }
    return SimpleNamespace(**{**defaults, **kwargs})


class TestTheReportedCase:
    def test_the_printers_outlet_beats_an_exhaust_fan(self):
        """The reporter's two Home Assistant plugs on one X1C. The fan was
        created first, so with no ranking it won on row order alone."""
        fan = _plug(
            1,
            name="Print Farm Exhaust Fan",
            plug_type="homeassistant",
            ha_entity_id="switch.print_farm_exhaust_fan",
            controls_printer_power=False,
            show_on_printer_card=False,
        )
        outlet = _plug(
            2,
            name="Bambu X1C Outlet",
            plug_type="homeassistant",
            ha_entity_id="switch.bambu_x1c_outlet",
            ha_power_entity="sensor.bambu_x1c_outlet_power",
        )

        assert _pick_main_plug([fan, outlet]) is outlet

    def test_and_it_wins_whichever_order_they_arrive_in(self):
        fan = _plug(1, plug_type="homeassistant", ha_entity_id="switch.fan", controls_printer_power=False)
        outlet = _plug(2, plug_type="homeassistant", ha_entity_id="switch.outlet")

        assert _pick_main_plug([outlet, fan]) is outlet


class TestTheOrderOfPreference:
    def test_a_switch_beats_a_script(self):
        """A script cannot be switched off, so it can never be the power plug."""
        script = _plug(1, plug_type="homeassistant", ha_entity_id="script.start_print")
        switch = _plug(2, plug_type="homeassistant", ha_entity_id="switch.outlet")

        assert _pick_main_plug([script, switch]) is switch

    def test_a_switchable_plug_beats_a_monitor_only_mqtt_plug(self):
        """``control_smart_plug`` rejects MQTT plugs as monitor-only, so the
        row's on/off button would answer with an error. An MQTT plug is also
        the kind that reports watts, so it would otherwise win on rank 5 --
        this is the one pair where the tiebreak could have done real harm."""
        monitor = _plug(1, plug_type="mqtt", mqtt_power_topic="tele/printer/SENSOR")
        switch = _plug(2, plug_type="homeassistant", ha_entity_id="switch.outlet")

        assert _pick_main_plug([monitor, switch]) is switch

    def test_a_monitor_only_plug_still_holds_the_row_on_its_own(self):
        """Rank, not filter: a printer whose only plug is an MQTT monitor keeps
        the wattage readout it has always had."""
        monitor = _plug(1, plug_type="mqtt", mqtt_power_topic="tele/printer/SENSOR")

        assert _pick_main_plug([monitor]) is monitor

    def test_a_power_plug_beats_an_accessory(self):
        accessory = _plug(1, controls_printer_power=False)
        outlet = _plug(2)

        assert _pick_main_plug([accessory, outlet]) is outlet

    def test_an_enabled_plug_beats_a_disabled_one(self):
        """A disabled plug ignores automation -- its auto-off toggle on the card
        would sit there doing nothing."""
        disabled = _plug(1, enabled=False)
        live = _plug(2)

        assert _pick_main_plug([disabled, live]) is live

    def test_a_visible_plug_beats_a_hidden_one(self):
        hidden = _plug(1, show_on_printer_card=False)
        visible = _plug(2)

        assert _pick_main_plug([hidden, visible]) is visible

    def test_powering_the_printer_outranks_being_visible(self):
        """The two only disagree when someone hides the plug that really feeds
        the printer. Letting the display flag win would hand the power buttons
        to an accessory, which is the harm #2629 fixed for the scheduler."""
        visible_accessory = _plug(1, controls_printer_power=False, show_on_printer_card=True)
        hidden_outlet = _plug(2, controls_printer_power=True, show_on_printer_card=False)

        assert _pick_main_plug([visible_accessory, hidden_outlet]) is hidden_outlet

    def test_a_plug_that_reports_watts_breaks_a_tie(self):
        """Otherwise the row reads "--" while a plug that knows the answer sits
        one rank below it."""
        mute = _plug(1, plug_type="homeassistant", ha_entity_id="switch.a")
        metered = _plug(2, plug_type="homeassistant", ha_entity_id="switch.b", ha_power_entity="sensor.b_power")

        assert _pick_main_plug([mute, metered]) is metered

    def test_reporting_watts_does_not_outrank_powering_the_printer(self):
        metered_accessory = _plug(1, ha_power_entity="sensor.fan_power", controls_printer_power=False)
        mute_outlet = _plug(2, plug_type="homeassistant", ha_entity_id="switch.outlet")

        assert _pick_main_plug([metered_accessory, mute_outlet]) is mute_outlet


class TestDeterminism:
    def test_equal_plugs_resolve_by_id(self):
        """The query had no ORDER BY, so on Postgres an unrelated UPDATE could
        move a row and silently swap which plug the card called the printer's
        power."""
        first = _plug(1)
        second = _plug(2)

        assert _pick_main_plug([second, first]) is first

    def test_the_documented_order_holds_end_to_end(self):
        """One plug per criterion, each failing only that one, ranked together.
        Pairwise tests would pass against an order with the middle two swapped."""
        ideal = _plug(6)
        mute = _plug(5, plug_type="homeassistant", ha_entity_id="switch.mute")
        hidden = _plug(4, show_on_printer_card=False)
        disabled = _plug(3, enabled=False)
        accessory = _plug(2, controls_printer_power=False)
        script = _plug(1, plug_type="homeassistant", ha_entity_id="script.a")

        ranked = sorted([script, accessory, disabled, hidden, mute, ideal], key=_main_plug_rank)

        assert ranked == [ideal, mute, hidden, disabled, accessory, script]


class TestNoPlugs:
    def test_no_plugs_means_no_main_plug(self):
        assert _pick_main_plug([]) is None

    def test_all_scripts_still_yields_one(self):
        """Pre-existing behaviour: a printer whose only entities are scripts
        still gets a Power row, because the card nests everything else inside
        it. Ranking must not turn that into an empty card."""
        second = _plug(2, plug_type="homeassistant", ha_entity_id="script.b")
        first = _plug(1, plug_type="homeassistant", ha_entity_id="script.a")

        assert _pick_main_plug([second, first]) is first


class TestSwitchability:
    @pytest.mark.parametrize(
        "plug,expected",
        [
            (_plug(plug_type="tasmota"), True),
            (_plug(plug_type="rest"), True),
            (_plug(plug_type="homeassistant", ha_entity_id="switch.outlet"), True),
            (_plug(plug_type="homeassistant", ha_entity_id="light.chamber"), True),
            (_plug(plug_type="homeassistant", ha_entity_id="script.start"), False),
            (_plug(plug_type="mqtt", mqtt_topic="zigbee2mqtt/plug"), False),
        ],
    )
    def test_matches_what_the_control_endpoint_accepts(self, plug, expected):
        assert _can_be_switched(plug) is expected


class TestUnsetFlags:
    """An upgraded database adds these columns by ALTER, so they are nullable
    there even though a fresh one declares them NOT NULL. Every row is
    backfilled with the default and nothing writes a null, but a rank that
    raised on one would take down the whole printers page."""

    def test_a_plug_with_null_flags_ranks_last_rather_than_raising(self):
        unset = _plug(1, controls_printer_power=None, enabled=None, show_on_printer_card=None)
        ordinary = _plug(2)

        assert _pick_main_plug([unset, ordinary]) is ordinary

    def test_and_still_holds_the_row_when_it_is_the_only_plug(self):
        unset = _plug(1, controls_printer_power=None, enabled=None, show_on_printer_card=None)

        assert _pick_main_plug([unset]) is unset

    def test_a_plug_with_no_type_at_all_does_not_raise(self):
        assert _pick_main_plug([_plug(1, plug_type=None)]) is not None


class TestScriptDetection:
    @pytest.mark.parametrize(
        "entity_id,expected",
        [
            ("script.start_print", True),
            ("switch.outlet", False),
            ("light.chamber", False),
            (None, False),
        ],
    )
    def test_only_ha_script_entities_count(self, entity_id, expected):
        assert _is_script_plug(_plug(plug_type="homeassistant", ha_entity_id=entity_id)) is expected

    def test_a_tasmota_plug_is_never_a_script(self):
        assert _is_script_plug(_plug(plug_type="tasmota", ha_entity_id="script.confusing")) is False


class TestPowerCapability:
    """Read from configuration, not measured -- this runs on every card render.

    It is a floor: an HA plug with no dedicated sensor may still report watts
    from the switch entity's own ``current_power_w`` attribute, which only a
    live read would show. Good enough for a tiebreak, and it never decides
    anything on its own.
    """

    def test_tasmota_reports_power_natively(self):
        assert _reports_power(_plug(plug_type="tasmota")) is True

    def test_home_assistant_needs_a_power_sensor(self):
        assert _reports_power(_plug(plug_type="homeassistant", ha_entity_id="switch.a")) is False
        assert _reports_power(_plug(plug_type="homeassistant", ha_power_entity="sensor.a_power")) is True

    def test_mqtt_accepts_either_the_new_or_the_legacy_topic(self):
        assert _reports_power(_plug(plug_type="mqtt")) is False
        assert _reports_power(_plug(plug_type="mqtt", mqtt_power_topic="tele/plug/SENSOR")) is True
        assert _reports_power(_plug(plug_type="mqtt", mqtt_topic="zigbee2mqtt/plug")) is True

    def test_rest_needs_a_power_path(self):
        """The REST service returns no energy at all without one, so a URL on
        its own proves nothing."""
        assert _reports_power(_plug(plug_type="rest")) is False
        assert _reports_power(_plug(plug_type="rest", rest_power_path="apower")) is True
