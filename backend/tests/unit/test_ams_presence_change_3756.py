"""A spool going into or out of an AMS slot is reported from the presence bit (#3756).

The AMS change hash keys on tray_type, tag_uid and remain. An untagged spool
loaded into a slot changes none of them until the printer has fed it and
filled in the slot's last filament, 24 s later on an H2C, or never if the
slot remembers nothing. The presence bit (``tray_exist_bits``) flips at once.
``on_ams_presence_change`` reports that flip per slot, and only the flip: not
the first sighting of a slot, not a repeat, and not the all-clear a printer
sends while shutting down.

TestRecordedH2C replays pushes recorded from an H2C (see
``_fixtures/h2c_ams_load_3756.py``). The other tests build pushes in the same
shape for what the recording doesn't cover.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient, slot_presence_changes
from backend.app.services.printer_manager import PrinterManager
from backend.tests._fixtures.h2c_ams_load_3756 import h2c_pushes


def _tray(tray_id: int, **fields) -> dict:
    return {"id": str(tray_id), "tray_type": "", "tag_uid": "0000000000000000", "remain": -1, **fields}


def _push(bits: str, *, power_on: bool = True, trays: list | None = None, unit_id: str = "0") -> dict:
    """One AMS push: unit 0's four trays, and the presence bitmask in hex."""
    if trays is None:
        trays = [
            _tray(0, tray_type="PLA", tag_uid="C7EFC10300000100", remain=80),
            _tray(1, tray_type="PETG", remain=-1),
            # Slot 2 reports no filament, empty or just loaded: the printer fills
            # in the bay's last one only once it has fed the spool.
            _tray(2),
            _tray(3, tray_type="ABS", remain=-1),
        ]
    return {"ams": [{"id": unit_id, "tray": trays}], "tray_exist_bits": bits, "power_on_flag": power_on}


@pytest.fixture
def client():
    c = BambuMQTTClient(ip_address="192.168.1.100", serial_number="TEST3756", access_code="12345678")
    c.seen = []
    c.on_ams_presence_change = lambda ams_id, tray_id, present: c.seen.append((ams_id, tray_id, present))
    return c


class TestSlotPresenceChanges:
    def test_a_slot_seen_for_the_first_time_is_not_a_change(self):
        current, changes = slot_presence_changes({}, [{"id": "0", "tray": [{"id": "1", "exists": True}]}])
        assert current == {(0, 1): True}
        assert changes == []

    def test_a_flip_is_reported_with_its_direction(self):
        ams = [{"id": "0", "tray": [{"id": "1", "exists": True}, {"id": "2", "exists": False}]}]
        _, changes = slot_presence_changes({(0, 1): False, (0, 2): True}, ams)
        assert changes == [(0, 1, True), (0, 2, False)]

    def test_a_tray_without_a_presence_bit_is_left_out(self):
        current, changes = slot_presence_changes({(0, 1): False}, [{"id": "0", "tray": [{"id": "1"}]}])
        assert current == {}
        assert changes == []

    def test_an_unreadable_id_is_skipped(self):
        current, _ = slot_presence_changes({}, [{"id": "x", "tray": [{"id": "1", "exists": True}]}])
        assert current == {}


class TestRecordedH2C:
    def test_each_pull_and_reload_is_reported_once(self, client):
        for _, push in h2c_pushes():
            client._handle_ams_data(push)
        assert client.seen == [(1, 2, False), (1, 2, True), (0, 0, False), (0, 0, True)]

    def test_an_untagged_reload_is_reported_24_s_before_the_ams_change(self, client):
        fired: list[float] = []
        presence: list[float] = []
        at = [0.0]
        client.on_ams_change = lambda _ams: fired.append(at[0])
        client.on_ams_presence_change = lambda ams_id, tray_id, present: presence.append(at[0]) if present else None
        for seconds, push in h2c_pushes():
            if seconds > 170:
                break
            at[0] = seconds
            client._handle_ams_data(push)
        # The bit came back at 138.8 s; the change hash only moved when the
        # printer filled in the slot's last filament at 162.9 s.
        assert presence == [138.8]
        assert [t for t in fired if t > 130] == [162.9]

    def test_the_tag_is_read_after_the_bit_flips(self, client):
        flipped: list[float] = []
        tagged: list[float] = []
        for seconds, push in h2c_pushes():
            if seconds < 180:
                continue
            before = len(client.seen)
            client._handle_ams_data(push)
            if len(client.seen) > before and client.seen[-1] == (0, 0, True):
                flipped.append(seconds)
            tray = next(
                t for u in client.state.raw_data["ams"] if str(u["id"]) == "0" for t in u["tray"] if str(t["id"]) == "0"
            )
            if tray.get("tag_uid", "0").strip("0") and not tagged and flipped:
                tagged.append(seconds)
        assert flipped == [201.2]
        assert tagged == [220.3]


class TestFromAmsPushes:
    def test_an_untagged_spool_going_in_is_reported_before_the_hash_changes(self, client):
        client.on_ams_change = MagicMock()
        client._handle_ams_data(_push("b"))  # slots 0, 1 and 3 hold a spool
        client.on_ams_change.reset_mock()

        client._handle_ams_data(_push("f"))  # a spool goes into slot 2

        assert client.seen == [(0, 2, True)]
        # Nothing the change hash reads has moved yet.
        client.on_ams_change.assert_not_called()

    def test_a_spool_coming_out_is_reported(self, client):
        client._handle_ams_data(_push("f"))
        client._handle_ams_data(_push("b"))
        assert client.seen == [(0, 2, False)]

    def test_the_first_push_reports_nothing(self, client):
        client._handle_ams_data(_push("b"))
        assert client.seen == []

    def test_a_repeated_push_reports_nothing(self, client):
        client._handle_ams_data(_push("b"))
        client._handle_ams_data(_push("f"))
        client._handle_ams_data(_push("f"))
        client._handle_ams_data(_push("f"))
        assert client.seen == [(0, 2, True)]

    def test_two_slots_at_once_are_both_reported(self, client):
        client._handle_ams_data(_push("3"))  # slots 0 and 1
        client._handle_ams_data(_push("c"))  # slots 2 and 3
        assert sorted(client.seen) == [(0, 0, False), (0, 1, False), (0, 2, True), (0, 3, True)]

    def test_a_printer_shutting_down_reports_nothing(self, client):
        client._handle_ams_data(_push("f"))
        client._handle_ams_data(_push("0", power_on=False))  # the all-clear sent while powering off
        client._handle_ams_data(_push("f"))  # back on, spools where they were
        assert client.seen == []

    def test_a_push_without_the_bitmask_reports_nothing(self, client):
        client._handle_ams_data(_push("b"))
        client._handle_ams_data({"ams": [{"id": "0", "tray": [_tray(2, remain=50)]}]})
        assert client.seen == []

    def test_an_ams_ht_bay(self, client):
        ht = [_tray(0, tray_type="PETG", remain=-1)]
        client._handle_ams_data(_push("0", trays=ht, unit_id="128"))
        client._handle_ams_data(_push("10000", trays=ht, unit_id="128"))  # HT 128 is bit 16
        client._handle_ams_data(_push("0", trays=ht, unit_id="128"))
        assert client.seen == [(128, 0, True), (128, 0, False)]

    def test_without_a_callback_nothing_breaks(self, client):
        client.on_ams_presence_change = None
        client._handle_ams_data(_push("b"))
        client._handle_ams_data(_push("f"))


class TestPrinterManager:
    @pytest.mark.asyncio
    async def test_the_callback_gets_the_printer_id(self):
        manager = PrinterManager()
        printer = MagicMock(id=7, ip_address="192.168.1.100", serial_number="00M09A123456789", access_code="1234")
        received = []

        async def on_presence(printer_id, ams_id, tray_id, present):
            received.append((printer_id, ams_id, tray_id, present))

        manager.set_ams_presence_change_callback(on_presence)
        scheduled = []
        with (
            patch("backend.app.services.printer_manager.BambuMQTTClient") as mock_client,
            patch.object(manager, "_schedule_async", side_effect=scheduled.append),
        ):
            mock_client.return_value.state.connected = True
            await manager.connect_printer(printer)
            mock_client.call_args.kwargs["on_ams_presence_change"](0, 2, True)

        for coro in scheduled:
            await coro
        assert received == [(7, 0, 2, True)]
