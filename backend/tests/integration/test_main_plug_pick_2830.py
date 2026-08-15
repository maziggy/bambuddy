"""The printer card's Power row and HA row, over the real API (#2830).

The ranking itself is unit-tested. These drive the two endpoints the card calls,
with real rows in the database, because the ranking is only worth anything if
the endpoints use it -- and because the second endpoint has to agree with the
first about which plug is the main one or the card draws it twice.
"""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

MAIN = "/api/v1/smart-plugs/by-printer/{}"
ENTITIES = "/api/v1/smart-plugs/by-printer/{}/scripts"


async def _ha(smart_plug_factory, printer, entity_id, **kwargs):
    return await smart_plug_factory(
        plug_type="homeassistant",
        ha_entity_id=entity_id,
        printer_id=printer.id,
        **kwargs,
    )


class TestTheReportedStranding:
    """An X1C with two Home Assistant plugs: an exhaust fan with no power
    monitoring, added first, and the outlet the printer is actually plugged
    into. The card showed the fan, with "--" where its wattage would be."""

    async def test_the_outlet_takes_the_power_row(self, async_client: AsyncClient, printer_factory, smart_plug_factory):
        printer = await printer_factory()
        await _ha(
            smart_plug_factory,
            printer,
            "switch.print_farm_exhaust_fan",
            name="Print Farm Exhaust Fan",
            controls_printer_power=False,
            show_on_printer_card=False,
        )
        await _ha(
            smart_plug_factory,
            printer,
            "switch.bambu_x1c_outlet",
            name="Bambu X1C Outlet",
            ha_power_entity="sensor.bambu_x1c_outlet_power",
        )

        response = await async_client.get(MAIN.format(printer.id))

        assert response.status_code == 200
        assert response.json()["name"] == "Bambu X1C Outlet"

    async def test_the_outlet_is_not_also_drawn_in_the_ha_row(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        """It is rendered directly above that row with its own controls."""
        printer = await printer_factory()
        await _ha(smart_plug_factory, printer, "switch.fan", name="Fan", controls_printer_power=False)
        await _ha(smart_plug_factory, printer, "switch.outlet", name="Bambu X1C Outlet")

        response = await async_client.get(ENTITIES.format(printer.id))

        assert response.status_code == 200
        assert [p["name"] for p in response.json()] == ["Fan"]


class TestTheHAEntityRow:
    async def test_scripts_and_lights_still_appear(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        printer = await printer_factory()
        await _ha(smart_plug_factory, printer, "switch.outlet", name="Outlet")
        await _ha(smart_plug_factory, printer, "script.start", name="Start Script", controls_printer_power=False)
        await _ha(smart_plug_factory, printer, "light.chamber", name="Chamber Light", controls_printer_power=False)

        response = await async_client.get(ENTITIES.format(printer.id))

        assert sorted(p["name"] for p in response.json()) == ["Chamber Light", "Start Script"]

    async def test_hidden_entities_stay_hidden(self, async_client: AsyncClient, printer_factory, smart_plug_factory):
        printer = await printer_factory()
        await _ha(smart_plug_factory, printer, "switch.outlet", name="Outlet")
        await _ha(
            smart_plug_factory,
            printer,
            "light.chamber",
            name="Chamber Light",
            controls_printer_power=False,
            show_on_printer_card=False,
        )

        response = await async_client.get(ENTITIES.format(printer.id))

        assert response.json() == []

    async def test_a_tasmota_main_plug_leaves_the_row_untouched(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        """Only HA entities are listed there, so excluding the main plug must
        not remove anything when the main plug was never in the list."""
        printer = await printer_factory()
        await smart_plug_factory(name="Tasmota Outlet", printer_id=printer.id)
        await _ha(smart_plug_factory, printer, "light.chamber", name="Chamber Light", controls_printer_power=False)

        response = await async_client.get(ENTITIES.format(printer.id))

        assert [p["name"] for p in response.json()] == ["Chamber Light"]


class TestASinglePlugIsNeverDropped:
    """Hidden and disabled are ranking criteria, not filters. Excluding those
    outright would take the Power row -- and with it the on/off button, and the
    HA row nested inside it -- off a card that has one plug to show."""

    async def test_a_hidden_plug_still_holds_the_row(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        printer = await printer_factory()
        await _ha(smart_plug_factory, printer, "switch.outlet", name="Outlet", show_on_printer_card=False)

        response = await async_client.get(MAIN.format(printer.id))

        assert response.json()["name"] == "Outlet"

    async def test_an_accessory_still_holds_the_row(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        printer = await printer_factory()
        await smart_plug_factory(name="Filter Fan", printer_id=printer.id, controls_printer_power=False)

        response = await async_client.get(MAIN.format(printer.id))

        assert response.json()["name"] == "Filter Fan"

    async def test_no_plugs_means_null(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory()

        response = await async_client.get(MAIN.format(printer.id))

        assert response.status_code == 200
        assert response.json() is None


class TestTheRestOfTheOrder:
    async def test_an_enabled_plug_beats_a_disabled_one(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        printer = await printer_factory()
        await smart_plug_factory(name="Disabled", printer_id=printer.id, enabled=False)
        await smart_plug_factory(name="Live", printer_id=printer.id)

        response = await async_client.get(MAIN.format(printer.id))

        assert response.json()["name"] == "Live"

    async def test_a_switch_beats_a_script(self, async_client: AsyncClient, printer_factory, smart_plug_factory):
        printer = await printer_factory()
        await _ha(smart_plug_factory, printer, "script.start", name="Start Script")
        await _ha(smart_plug_factory, printer, "switch.outlet", name="Outlet")

        response = await async_client.get(MAIN.format(printer.id))

        assert response.json()["name"] == "Outlet"

    async def test_a_printer_with_only_scripts_still_gets_one(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        printer = await printer_factory()
        await _ha(smart_plug_factory, printer, "script.a", name="First Script")
        await _ha(smart_plug_factory, printer, "script.b", name="Second Script")

        response = await async_client.get(MAIN.format(printer.id))

        assert response.json()["name"] == "First Script"

    async def test_a_script_in_the_power_row_keeps_its_button(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        """The script-only fallback is unchanged from before #2830, including
        the one-click run in the HA row. Only a switchable main plug is
        de-duplicated -- a script reached from the power row costs a confirm
        dialog it never used to need."""
        printer = await printer_factory()
        await _ha(smart_plug_factory, printer, "script.a", name="First Script")
        await _ha(smart_plug_factory, printer, "script.b", name="Second Script")

        response = await async_client.get(ENTITIES.format(printer.id))

        assert sorted(p["name"] for p in response.json()) == ["First Script", "Second Script"]

    async def test_a_switchable_plug_beats_a_monitor_only_mqtt_plug(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        """An MQTT plug reports watts but cannot be controlled -- the control
        endpoint rejects it. Put it in the power row and the on/off button
        answers with an error."""
        printer = await printer_factory()
        await smart_plug_factory(
            name="Monitor", plug_type="mqtt", printer_id=printer.id, mqtt_power_topic="tele/printer/SENSOR"
        )
        await _ha(smart_plug_factory, printer, "switch.outlet", name="Outlet")

        response = await async_client.get(MAIN.format(printer.id))

        assert response.json()["name"] == "Outlet"

    async def test_a_lone_monitor_only_plug_still_holds_the_row(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        printer = await printer_factory()
        await smart_plug_factory(
            name="Monitor", plug_type="mqtt", printer_id=printer.id, mqtt_power_topic="tele/printer/SENSOR"
        )

        response = await async_client.get(MAIN.format(printer.id))

        assert response.json()["name"] == "Monitor"


class TestOtherPrintersAreUnaffected:
    async def test_plugs_are_not_borrowed_across_printers(
        self, async_client: AsyncClient, printer_factory, smart_plug_factory
    ):
        one = await printer_factory()
        two = await printer_factory()
        await smart_plug_factory(name="Plug One", printer_id=one.id)
        await smart_plug_factory(name="Plug Two", printer_id=two.id)
        await smart_plug_factory(name="Unlinked")

        assert (await async_client.get(MAIN.format(one.id))).json()["name"] == "Plug One"
        assert (await async_client.get(MAIN.format(two.id))).json()["name"] == "Plug Two"
