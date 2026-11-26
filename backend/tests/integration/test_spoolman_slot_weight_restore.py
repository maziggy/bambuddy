"""A spool back in its slot gets its Spoolman weight on the printer again.

The printer doesn't always keep a slot's weight when the spool comes out: an
untagged tray has come back as ``remain: -1``, a Bambu Lab spool with the
printer's own estimate. ``_sync_slot_weights`` runs after every AMS
change and sends Spoolman's figure to a slot seen empty, just linked to a
spool, or reporting no weight, once per fill. A tray with an RFID tag gets it
only when the tag is the assigned spool's.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.slot_filament_weight import FilamentWeight

BAMBU_TRAY_UUID = "EB335968299543078925C71D83DA3864"
OTHER_TRAY_UUID = "438DD55FE022481AB5746C3FC00498A2"
UNKNOWN_TRAY_UUID = "90220AB7AF464FCF8E2B1A6D3C5F7E91"
SPOOL = {
    "id": 10,
    "initial_weight": 1000.0,
    "remaining_weight": 965.0,
    "filament": {"weight": 1000},
    "extra": {"tag": json.dumps(BAMBU_TRAY_UUID)},
}
OTHER_SPOOL = {"id": 11, "initial_weight": 1000.0, "remaining_weight": 500.0, "tags": [{"uid": OTHER_TRAY_UUID}]}


def _ams(**tray) -> list:
    """The H3C's AMS 3H with slot 4 as given; filled and untagged by default."""
    return [{"id": 32, "tray": [{"id": 4, "exists": True, "tray_type": "PLA", "remain": -1, **tray}]}]


EMPTY = [{"id": 32, "tray": [{"id": 4, "exists": False, "tray_type": "", "state": 9}]}]


@pytest.fixture
async def assigned(db_session: AsyncSession, printer_factory):
    printer = await printer_factory(name="H3C", model="H3C")
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://spoolman.invalid:7912"))
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=32, tray_id=4, spoolman_spool_id=10))
    await db_session.commit()
    return printer


@pytest.fixture
def printer_side():
    """The printer's MQTT client and Spoolman, as _sync_slot_weights reaches them."""
    mqtt = MagicMock()
    mqtt.model = "H3C"
    mqtt.ams_set_filament_weight = MagicMock(return_value=True)
    spoolman = MagicMock()
    spoolman.get_spool = AsyncMock(return_value=SPOOL)
    spoolman.get_spools = AsyncMock(return_value=[SPOOL, OTHER_SPOOL])
    with (
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.get_spoolman_client", AsyncMock(return_value=spoolman)),
    ):
        pm.get_client.return_value = mqtt
        yield mqtt, spoolman


async def _push(printer_id: int, ams_data: list, *, printing: bool = False) -> None:
    from backend.app.main import _sync_slot_weights

    await _sync_slot_weights(printer_id, ams_data, printing)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_untagged_spool_back_without_a_weight_gets_it_once(async_client: AsyncClient, assigned, printer_side):
    mqtt, _ = printer_side

    await _push(assigned.id, _ams(remain=-1))
    mqtt.ams_set_filament_weight.assert_called_once_with(32, 4, FilamentWeight(1000, 965))

    # The next push still reads -1 until the printer has applied it, and a
    # printer that refused it would read -1 for good: one send per fill.
    await _push(assigned.id, _ams(remain=-1))
    assert mqtt.ams_set_filament_weight.call_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_bambu_spool_taken_out_and_put_back_gets_spoolmans_weight(
    async_client: AsyncClient, assigned, printer_side
):
    mqtt, _ = printer_side
    bambu = {"tray_uuid": BAMBU_TRAY_UUID, "remain": 100}

    await _push(assigned.id, _ams(**bambu))
    mqtt.ams_set_filament_weight.assert_not_called()  # the printer's own figure, nothing says otherwise

    await _push(assigned.id, EMPTY)
    await _push(assigned.id, _ams(**bambu))
    mqtt.ams_set_filament_weight.assert_called_once_with(32, 4, FilamentWeight(1000, 965, rfid=True))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_native_tag_on_the_spool_counts_too(async_client: AsyncClient, assigned, printer_side):
    """A Bambu chip's own UID as a native tag, while the AMS reports it padded."""
    mqtt, spoolman = printer_side
    spoolman.get_spool = AsyncMock(return_value={**SPOOL, "extra": {}, "tags": [{"uid": "D3E68F32"}]})

    await _push(assigned.id, EMPTY)
    await _push(assigned.id, _ams(tray_uuid=UNKNOWN_TRAY_UUID, tag_uid="D3E68F3200000100", remain=100))

    mqtt.ams_set_filament_weight.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("tray_uuid", [OTHER_TRAY_UUID, UNKNOWN_TRAY_UUID], ids=["another spools tag", "unknown tag"])
async def test_another_bambu_spool_in_a_held_slot_gets_no_weight(
    async_client: AsyncClient, assigned, printer_side, tray_uuid, caplog
):
    """Spool 10 comes out and a different Bambu spool goes in while the slot
    still holds spool 10's assignment: spool 10's grams are not that spool's."""
    mqtt, spoolman = printer_side
    caplog.set_level("INFO", logger="backend.app.main")

    await _push(assigned.id, _ams(tray_uuid=BAMBU_TRAY_UUID, remain=100))
    await _push(assigned.id, EMPTY)
    await _push(assigned.id, _ams(tray_uuid=tray_uuid, remain=100))
    await _push(assigned.id, _ams(tray_uuid=tray_uuid, remain=100))

    mqtt.ams_set_filament_weight.assert_not_called()
    assert spoolman.get_spools.await_count == 1, "looked up once per fill, not on every push"
    expected = "is spool 11's" if tray_uuid == OTHER_TRAY_UUID else "is not in Spoolman"
    assert f"not sending its weight, the tag in the tray {expected}" in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_bambu_spool_swapped_in_during_a_print_gets_no_weight_after_it(
    async_client: AsyncClient, assigned, printer_side
):
    """During a print an emptied slot keeps its spool (it may be a runout), so a
    different spool put in then is still under spool 10 when the print ends."""
    mqtt, _ = printer_side

    await _push(assigned.id, EMPTY, printing=True)
    await _push(assigned.id, _ams(tray_uuid=UNKNOWN_TRAY_UUID, remain=100), printing=True)
    await _push(assigned.id, _ams(tray_uuid=UNKNOWN_TRAY_UUID, remain=100))

    mqtt.ams_set_filament_weight.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_slot_already_showing_spoolmans_weight_is_left_alone(async_client: AsyncClient, assigned, printer_side):
    mqtt, _ = printer_side

    await _push(assigned.id, EMPTY)
    await _push(assigned.id, _ams(remain=96))

    mqtt.ams_set_filament_weight.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_spool_swapped_in_during_a_print_gets_it_after(async_client: AsyncClient, assigned, printer_side):
    mqtt, _ = printer_side

    await _push(assigned.id, EMPTY, printing=True)
    await _push(assigned.id, _ams(remain=100), printing=True)
    mqtt.ams_set_filament_weight.assert_not_called()

    await _push(assigned.id, _ams(remain=100))
    mqtt.ams_set_filament_weight.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_not_on_a_model_without_weight_editing(async_client: AsyncClient, assigned, printer_side):
    mqtt, spoolman = printer_side
    mqtt.model = "X1C"

    await _push(assigned.id, _ams(remain=-1))

    mqtt.ams_set_filament_weight.assert_not_called()
    spoolman.get_spool.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_spoolman_unreachable_tries_again_on_the_next_push(async_client: AsyncClient, assigned, printer_side):
    from backend.app.services.spoolman import SpoolmanUnavailableError

    mqtt, spoolman = printer_side
    spoolman.get_spool = AsyncMock(side_effect=[SpoolmanUnavailableError("down"), SPOOL])

    await _push(assigned.id, _ams(remain=-1))
    mqtt.ams_set_filament_weight.assert_not_called()

    await _push(assigned.id, _ams(remain=-1))
    mqtt.ams_set_filament_weight.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_outage_is_logged_once_until_spoolman_answers(
    async_client: AsyncClient, assigned, printer_side, db_session: AsyncSession, caplog
):
    """Two slots due on every AMS change while Spoolman is down: one warning,
    one read per change, and a warning again for the next outage."""
    from backend.app.services.spoolman import SpoolmanUnavailableError

    db_session.add(SpoolmanSlotAssignment(printer_id=assigned.id, ams_id=32, tray_id=5, spoolman_spool_id=12))
    await db_session.commit()
    mqtt, spoolman = printer_side
    down = SpoolmanUnavailableError("Cannot reach Spoolman")
    spoolman.get_spool = AsyncMock(side_effect=[down, down, SPOOL, {**SPOOL, "id": 12}, down])
    two = [{"id": 32, "tray": [{"id": t, "exists": True, "tray_type": "PLA", "remain": -1} for t in (4, 5)]}]
    caplog.set_level("INFO", logger="backend.app.main")

    def outage_warnings() -> int:
        return sum("Spoolman is unreachable" in r.message for r in caplog.records if r.levelname == "WARNING")

    await _push(assigned.id, two)
    await _push(assigned.id, two)
    assert (outage_warnings(), spoolman.get_spool.await_count) == (1, 2)

    await _push(assigned.id, two)
    assert mqtt.ams_set_filament_weight.call_count == 2
    assert "Spoolman answers again" in caplog.text

    await _push(assigned.id, EMPTY)
    await _push(assigned.id, _ams(remain=-1))
    assert outage_warnings() == 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_bambu_spool_the_sync_links_gets_its_weight(
    async_client: AsyncClient, printer_factory, db_session: AsyncSession
):
    """Through on_ams_change: the AMS sync links the tag to spool 10, and the
    weight goes out in the same pass."""
    from backend.app.main import on_ams_change

    printer = await printer_factory(name="H3C", model="H3C")
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://spoolman.invalid:7912"))
    await db_session.commit()

    ams_data = _ams(tray_uuid=BAMBU_TRAY_UUID, remain=100, tray_color="042F56FF", state=11)
    parsed = MagicMock(ams_id=32, tray_id=4, tray_uuid=BAMBU_TRAY_UUID, tag_uid="7757EF0100000100")
    parsed.tray_info_idx, parsed.tray_type, parsed.tray_sub_brands = "GFA00", "PLA", "PLA Basic"
    spoolman = MagicMock()
    spoolman.health_check = AsyncMock(return_value=True)
    spoolman.get_spools = AsyncMock(return_value=[])
    spoolman.parse_ams_tray = MagicMock(return_value=parsed)
    spoolman.sync_ams_tray = AsyncMock(return_value={"id": 10})
    spoolman.get_spool = AsyncMock(return_value=SPOOL)
    mqtt = MagicMock()
    mqtt.model = "H3C"
    status = MagicMock(raw_data={"ams": ams_data, "vt_tray": []}, state="IDLE")
    ws = MagicMock(broadcast=AsyncMock(), send_printer_status=AsyncMock())

    with (
        patch("backend.app.main.printer_manager") as pm_main,
        patch("backend.app.services.printer_manager.printer_manager") as pm_inv,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager", ws),
        patch("backend.app.services.spoolman_slot_sync.ws_manager", ws),
        patch("backend.app.main.get_spoolman_client", AsyncMock(return_value=spoolman)),
        patch("backend.app.services.slot_preset_writer.upsert_slot_preset_for_spoolman_spool", AsyncMock()),
    ):
        pm_main.get_status.return_value = status
        pm_main.get_client.return_value = mqtt
        pm_main.get_model.return_value = "H3C"
        pm_inv.get_status.return_value = status
        relay.on_ams_change = AsyncMock()
        await on_ams_change(printer.id, ams_data)

    mqtt.ams_set_filament_weight.assert_called_once_with(32, 4, FilamentWeight(1000, 965, rfid=True))
