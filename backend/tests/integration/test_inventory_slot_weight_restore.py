"""A built-in inventory spool back in its slot gets its weight on the printer again.

The same pass as with Spoolman (``_sync_slot_weights``), with the spool's
weight and tags read from the database: a reloaded spool gets its weight once
per fill, a Bambu Lab spool only with its own tag, and a spool the RFID pass
links to a slot gets it in the same pass.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.slot_filament_weight import FilamentWeight

BAMBU_TRAY_UUID = "EB335968299543078925C71D83DA3864"
OTHER_TRAY_UUID = "438DD55FE022481AB5746C3FC00498A2"
UNKNOWN_TRAY_UUID = "90220AB7AF464FCF8E2B1A6D3C5F7E91"


def _ams(**tray) -> list:
    """The H3C's AMS 3H with slot 4 as given; filled and untagged by default."""
    return [{"id": 32, "tray": [{"id": 4, "exists": True, "tray_type": "PLA", "remain": -1, **tray}]}]


EMPTY = [{"id": 32, "tray": [{"id": 4, "exists": False, "tray_type": "", "state": 9}]}]


@pytest.fixture
async def assigned(db_session: AsyncSession, printer_factory):
    """Spool 965 g of 1000 g in AMS32-T4 of an H3C, and another spool tagged OTHER_TRAY_UUID."""
    printer = await printer_factory(name="H3C", model="H3C")
    spool = Spool(material="PLA", label_weight=1000, weight_used=34.6)
    other = Spool(material="PLA", label_weight=1000, weight_used=500, tray_uuid=OTHER_TRAY_UUID)
    db_session.add_all([spool, other])
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=32, tray_id=4))
    await db_session.commit()
    return printer, spool, other


@pytest.fixture
def mqtt():
    """The printer's MQTT client, as _sync_slot_weights reaches it."""
    client = MagicMock()
    client.model = "H3C"
    client.ams_set_filament_weight = MagicMock(return_value=True)
    with patch("backend.app.main.printer_manager") as pm:
        pm.get_client.return_value = client
        yield client


async def _push(printer_id: int, ams_data: list, *, printing: bool = False) -> None:
    from backend.app.main import _sync_slot_weights

    await _sync_slot_weights(printer_id, ams_data, printing)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_untagged_spool_back_without_a_weight_gets_it_once(async_client: AsyncClient, assigned, mqtt):
    printer, _, _ = assigned

    await _push(printer.id, _ams(remain=-1))
    mqtt.ams_set_filament_weight.assert_called_once_with(32, 4, FilamentWeight(1000, 965))

    await _push(printer.id, _ams(remain=-1))
    assert mqtt.ams_set_filament_weight.call_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_bambu_spool_taken_out_and_put_back_gets_its_weight(
    async_client: AsyncClient, assigned, mqtt, db_session: AsyncSession
):
    printer, spool, _ = assigned
    spool.tray_uuid = BAMBU_TRAY_UUID
    await db_session.commit()
    bambu = {"tray_uuid": BAMBU_TRAY_UUID, "remain": 100}

    await _push(printer.id, _ams(**bambu))
    mqtt.ams_set_filament_weight.assert_not_called()  # the printer's own figure, nothing says otherwise

    await _push(printer.id, EMPTY)
    await _push(printer.id, _ams(**bambu))
    mqtt.ams_set_filament_weight.assert_called_once_with(32, 4, FilamentWeight(1000, 965, rfid=True))


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("tray_uuid", [OTHER_TRAY_UUID, UNKNOWN_TRAY_UUID], ids=["another spools tag", "unknown tag"])
async def test_another_bambu_spool_in_a_held_slot_gets_no_weight(
    async_client: AsyncClient, assigned, mqtt, tray_uuid, caplog
):
    """The assigned spool comes out and a different Bambu spool goes in while
    the slot still holds the assignment: its grams are not that spool's."""
    printer, spool, other = assigned
    caplog.set_level("INFO", logger="backend.app.main")

    await _push(printer.id, EMPTY)
    await _push(printer.id, _ams(tray_uuid=tray_uuid, remain=100))

    mqtt.ams_set_filament_weight.assert_not_called()
    whose = f"is spool {other.id}'s" if tray_uuid == OTHER_TRAY_UUID else "is not in the inventory"
    assert (
        f"Spool {spool.id} in printer {printer.id} AMS32-T4: not sending its weight, the tag in the tray {whose}"
        in (caplog.text)
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_spool_the_rfid_pass_links_gets_its_weight(
    async_client: AsyncClient, printer_factory, mqtt, db_session: AsyncSession
):
    """auto_assign_spool links a Bambu spool by its tag; the pass after it sends the weight."""
    from backend.app.services.spool_tag_matcher import auto_assign_spool, get_spool_by_tag

    printer = await printer_factory(name="H3C", model="H3C")
    db_session.add(Spool(material="PLA", label_weight=1000, weight_used=34.6, tray_uuid=BAMBU_TRAY_UUID))
    await db_session.commit()
    ams_data = _ams(tray_uuid=BAMBU_TRAY_UUID, remain=100)
    spool = await get_spool_by_tag(db_session, "", BAMBU_TRAY_UUID)
    printer_side = MagicMock()
    printer_side.get_status.return_value = MagicMock(raw_data={"ams": ams_data})
    await auto_assign_spool(printer.id, 32, 4, spool, printer_side, db_session)
    await db_session.commit()

    await _push(printer.id, ams_data)

    mqtt.ams_set_filament_weight.assert_called_once_with(32, 4, FilamentWeight(1000, 965, rfid=True))
