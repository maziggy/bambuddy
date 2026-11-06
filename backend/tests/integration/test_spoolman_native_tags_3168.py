"""The Spoolman routes against Spoolman 0.27's native tags, and against an older server (#3168).

The routes run with a real SpoolmanClient over HTTP to a fake that enforces the
server's tag rules (``backend/tests/_fixtures/spoolman_tags.py``). Only the
client lookup and the websocket broadcast are patched.
"""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice
from backend.app.services.spoolman_tracking import get_fallback_spool_tag_for_slot
from backend.tests._fixtures.spoolman_tags import BASE_URL, FakeSpoolman, client_for

SCAN = "/api/v1/spoolbuddy/nfc/tag-scanned"
INVENTORY = "/api/v1/spoolman/inventory"

TRAY_UUID = "9E0B0717BEE94D7887EB1D8DFD1A14F3"
CHIP = "D3E68F32"
OTHER_CHIP = "A1B2C3D4"


@pytest.fixture
async def spoolman_on(db_session: AsyncSession):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value=BASE_URL))
    await db_session.commit()


@contextmanager
def serving(fake: FakeSpoolman):
    """Every route that asks for the Spoolman client gets one talking to ``fake``."""
    client = client_for(fake)
    getter = AsyncMock(return_value=client)
    with (
        patch("backend.app.services.spoolman.get_spoolman_client", getter),
        patch("backend.app.services.spoolman.init_spoolman_client", getter),
        patch("backend.app.api.routes.spoolman_inventory.get_spoolman_client", getter),
        patch("backend.app.api.routes.spoolman_inventory.init_spoolman_client", getter),
        patch("backend.app.api.routes.spoolman.get_spoolman_client", getter),
        patch("backend.app.api.routes.spoolman.init_spoolman_client", getter),
        patch("backend.app.api.routes.spoolbuddy.ws_manager") as ws,
        patch("backend.app.api.routes.spoolman_inventory.ws_manager") as inventory_ws,
    ):
        ws.broadcast = AsyncMock()
        inventory_ws.broadcast = AsyncMock()
        yield ws


async def _scan(async_client: AsyncClient, tag_uid: str, tray_uuid: str | None = None) -> dict:
    payload = {"device_id": "sb-1", "tag_uid": tag_uid}
    if tray_uuid:
        payload["tray_uuid"] = tray_uuid
    resp = await async_client.post(SCAN, json=payload)
    assert resp.status_code == 200
    return resp.json()


class TestSpoolBuddyScan:
    async def test_a_native_tag_matches_without_loading_the_inventory(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        for spool_id in range(1, 30):
            fake.add_spool(spool_id)
        fake.add_spool(30, extra_tag=TRAY_UUID, tags=[TRAY_UUID, CHIP])

        with serving(fake):
            result = await _scan(async_client, CHIP, TRAY_UUID)

        assert result["spool_id"] == 30
        assert fake.asked("GET /spool") == 0

    async def test_a_spool_found_through_extra_tag_moves_over_on_its_first_scan(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=TRAY_UUID)

        with serving(fake):
            first = await _scan(async_client, CHIP, TRAY_UUID)
            listings_after_first = fake.asked("GET /spool")
            second = await _scan(async_client, OTHER_CHIP, TRAY_UUID)

        assert first["spool_id"] == second["spool_id"] == 7
        assert listings_after_first == 1
        assert fake.asked("GET /spool") == 1, "the second scan loaded the inventory again"
        # Both sides of the spool are linked now, each by the chip its reader saw.
        assert fake.native(7) == sorted([TRAY_UUID, CHIP, OTHER_CHIP])

    async def test_the_tray_uuid_in_extra_tag_still_wins_over_a_native_chip_uid(self, async_client, spoolman_on):
        """The order the scan always had: tray UUID first, even before it has moved over.

        Spool 9 carries the UUID only in extra.tag, spool 7 the chip natively. The
        scan belongs to spool 9, and spool 7 must not be given the UUID.
        """
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=CHIP, tags=[CHIP])
        fake.add_spool(9, extra_tag=TRAY_UUID)

        with serving(fake):
            result = await _scan(async_client, CHIP, TRAY_UUID)

        assert result["spool_id"] == 9
        assert fake.spools[7]["extra"]["tag"] == json.dumps(CHIP)
        assert fake.holder(TRAY_UUID) == 9
        assert fake.holder(CHIP) == 7

    async def test_a_tag_left_on_an_archived_spool_moves_to_its_successor(self, async_client, spoolman_on):
        """The archived spool holds the chip natively; its active successor has it in extra.tag."""
        fake = FakeSpoolman()
        fake.add_spool(1, extra_tag=CHIP, tags=[CHIP], archived=True)
        fake.add_spool(9, extra_tag=CHIP)

        with serving(fake):
            first = await _scan(async_client, CHIP)
            second = await _scan(async_client, CHIP)

        assert first["spool_id"] == second["spool_id"] == 9
        assert fake.holder(CHIP) == 9
        assert fake.asked("POST /spool/9/tag") == 2  # refused once, then linked after the move

    async def test_a_scan_right_after_settling_a_conflict_adds_the_tag(self, async_client, spoolman_on):
        """A refusal holds off the AMS sync, not someone scanning at the reader.

        Spool 7 is found by its tray UUID; its chip is linked to spool 1 by mistake.
        The conflict is settled in Spoolman and the spool scanned again at once.
        """
        fake = FakeSpoolman()
        fake.add_spool(1, tags=[CHIP])
        fake.add_spool(7, extra_tag=TRAY_UUID, tags=[TRAY_UUID])

        with serving(fake):
            first = await _scan(async_client, CHIP, TRAY_UUID)
            assert (first["spool_id"], fake.holder(CHIP)) == (7, 1)
            fake.spools[1]["tags"] = []
            second = await _scan(async_client, CHIP, TRAY_UUID)

        assert second["spool_id"] == 7
        assert fake.holder(CHIP) == 7

    async def test_an_older_server_matches_through_extra_tag_and_writes_no_tags(self, async_client, spoolman_on):
        fake = FakeSpoolman(tag_api=False)
        fake.add_spool(7, extra_tag=TRAY_UUID)

        with serving(fake):
            result = await _scan(async_client, CHIP, TRAY_UUID)

        assert result["spool_id"] == 7
        assert fake.tag_writes() == []


class TestLinkTag:
    async def test_the_tags_are_added_to_what_the_spool_carries(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, tags=[OTHER_CHIP])

        with serving(fake):
            resp = await async_client.patch(
                f"{INVENTORY}/spools/7/tag", json={"tray_uuid": TRAY_UUID, "tag_uid": CHIP.lower()}
            )

        assert resp.status_code == 200
        assert fake.native(7) == sorted([TRAY_UUID, CHIP, OTHER_CHIP])
        assert fake.spools[7]["extra"]["tag"] == json.dumps(TRAY_UUID)

    async def test_a_tag_another_spool_holds_natively_is_refused_and_nothing_stays_behind(
        self, async_client, spoolman_on
    ):
        """Spool 9 holds the chip only as a native tag, so the extra.tag check cannot see it."""
        fake = FakeSpoolman()
        fake.add_spool(7, tags=[OTHER_CHIP])
        fake.add_spool(9, tags=[CHIP])

        with serving(fake):
            resp = await async_client.patch(f"{INVENTORY}/spools/7/tag", json={"tray_uuid": TRAY_UUID, "tag_uid": CHIP})

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert (detail["code"], detail["spool_id"], detail["field"]) == ("tag_already_linked", 9, "tag_uid")
        # The tray UUID this request linked first is taken back; the tag spool 7 had stays.
        assert fake.native(7) == [OTHER_CHIP]
        assert fake.spools[7]["extra"] == {}

    @pytest.mark.parametrize("holder", ["filament", "location"])
    async def test_a_tag_a_filament_or_location_holds_is_refused_without_naming_a_spool(
        self, async_client, spoolman_on, holder
    ):
        fake = FakeSpoolman()
        fake.add_spool(7)
        if holder == "filament":
            fake.filament_tags[CHIP] = 4
        else:
            fake.location_tags.add(CHIP)

        with serving(fake):
            resp = await async_client.patch(f"{INVENTORY}/spools/7/tag", json={"tray_uuid": TRAY_UUID, "tag_uid": CHIP})

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert (detail["code"], detail["field"]) == ("tag_linked_elsewhere", "tag_uid")
        assert "spool_id" not in detail
        assert fake.native(7) == []

    async def test_a_tag_an_archived_spool_holds_moves_to_the_spool(self, async_client, spoolman_on):
        """The archived spool is the one this spool replaced; Bambuddy cannot even show it."""
        fake = FakeSpoolman()
        fake.add_spool(1, tags=[CHIP], archived=True)
        fake.add_spool(7)

        with serving(fake):
            resp = await async_client.patch(f"{INVENTORY}/spools/7/tag", json={"tag_uid": CHIP})

        assert resp.status_code == 200
        assert fake.native(7) == [CHIP]
        assert fake.native(1) == []

    async def test_a_failed_extra_tag_write_takes_back_what_the_request_added(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, tags=[OTHER_CHIP])
        fake.fail_patch.add(7)

        with serving(fake):
            resp = await async_client.patch(f"{INVENTORY}/spools/7/tag", json={"tray_uuid": TRAY_UUID, "tag_uid": CHIP})

        assert resp.status_code >= 500
        assert fake.native(7) == [OTHER_CHIP]


class TestLinkSpoolRoute:
    """POST /spoolman/spools/{id}/link, the AMS slot's "Link to Spoolman"."""

    async def test_a_refused_native_tag_leaves_extra_tag_untouched(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7)
        fake.refuse_400.add(TRAY_UUID)

        with serving(fake):
            resp = await async_client.post("/api/v1/spoolman/spools/7/link", json={"spool_tag": TRAY_UUID})

        assert resp.status_code == 502
        assert fake.spools[7]["extra"] == {}
        assert fake.native(7) == []

    async def test_a_failed_extra_tag_write_takes_the_native_tag_back(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, tags=[OTHER_CHIP])
        fake.fail_patch.add(7)

        with serving(fake):
            resp = await async_client.post("/api/v1/spoolman/spools/7/link", json={"spool_tag": TRAY_UUID})

        assert resp.status_code >= 500
        assert fake.native(7) == [OTHER_CHIP]

    async def test_a_tag_the_spool_already_had_stays_when_the_write_fails(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, tags=[TRAY_UUID])
        fake.fail_patch.add(7)

        with serving(fake):
            resp = await async_client.post("/api/v1/spoolman/spools/7/link", json={"spool_tag": TRAY_UUID})

        assert resp.status_code >= 500
        assert fake.native(7) == [TRAY_UUID]


class TestNfcWriteResult:
    """SpoolBuddy reports a tag it has just written for a Spoolman spool."""

    @pytest.fixture
    async def device(self, db_session: AsyncSession):
        db_session.add(
            SpoolBuddyDevice(
                device_id="sb-write",
                hostname="spoolbuddy",
                ip_address="10.0.0.9",
                firmware_version="1.0.0",
                has_nfc=True,
                has_scale=True,
                tare_offset=0,
                calibration_factor=1.0,
                last_seen=datetime.now(timezone.utc),
                pending_command="write_tag",
                pending_write_payload=json.dumps(
                    {"spool_id": 7, "ndef_data_hex": "deadbeef", "data_origin": "spoolman"}
                ),
            )
        )
        await db_session.commit()

    async def _report(self, async_client):
        return await async_client.post(
            "/api/v1/spoolbuddy/nfc/write-result",
            json={"device_id": "sb-write", "spool_id": 7, "tag_uid": CHIP, "success": True},
        )

    async def test_the_written_tag_moves_from_its_previous_spool(self, async_client, spoolman_on, device):
        fake = FakeSpoolman()
        fake.add_spool(1, tags=[CHIP])
        fake.add_spool(7)

        with serving(fake):
            resp = await self._report(async_client)

        assert resp.status_code == 200
        assert (fake.native(1), fake.native(7)) == ([], [CHIP])
        assert fake.spools[7]["extra"]["tag"] == json.dumps(CHIP)

    async def test_a_tag_the_spool_already_holds_is_left_as_it_is(self, async_client, spoolman_on, device):
        fake = FakeSpoolman()
        fake.add_spool(7, tags=[CHIP])

        with serving(fake):
            resp = await self._report(async_client)

        assert resp.status_code == 200
        assert fake.tag_writes() == []
        assert fake.native(7) == [CHIP]
        assert fake.spools[7]["extra"]["tag"] == json.dumps(CHIP)

    async def test_a_refused_native_tag_leaves_extra_tag_untouched(self, async_client, spoolman_on, device):
        fake = FakeSpoolman()
        fake.add_spool(7)
        fake.refuse_400.add(CHIP)

        with serving(fake):
            resp = await self._report(async_client)

        assert resp.status_code == 502
        assert fake.spools[7]["extra"] == {}

    async def test_a_failed_extra_tag_write_takes_the_native_tag_back(self, async_client, spoolman_on, device):
        fake = FakeSpoolman()
        fake.add_spool(7)
        fake.fail_patch.add(7)

        with serving(fake):
            resp = await self._report(async_client)

        assert resp.status_code == 502
        assert fake.native(7) == []


class TestClearRfidTag:
    async def test_clearing_removes_extra_tag_and_the_native_tags(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=TRAY_UUID, tags=[TRAY_UUID, CHIP])

        with serving(fake):
            resp = await async_client.patch(f"{INVENTORY}/spools/7", json={"tag_uid": None})

        assert resp.status_code == 200
        assert fake.spools[7]["extra"]["tag"] == json.dumps("")
        assert fake.native(7) == []
        assert resp.json()["tray_uuid"] is None

    async def test_a_failed_update_keeps_the_native_tags(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=TRAY_UUID, tags=[TRAY_UUID, CHIP])
        fake.fail_patch.add(7)

        with serving(fake):
            resp = await async_client.patch(f"{INVENTORY}/spools/7", json={"tag_uid": None})

        assert resp.status_code >= 500
        assert fake.native(7) == sorted([TRAY_UUID, CHIP])


class TestUnlink:
    async def test_unlinking_a_spool_removes_its_native_tags(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=TRAY_UUID, tags=[TRAY_UUID, CHIP])

        with serving(fake):
            resp = await async_client.post("/api/v1/spoolman/spools/7/unlink")

        assert resp.status_code == 200
        assert fake.native(7) == []
        assert fake.spools[7]["extra"]["tag"] == json.dumps("")

    async def test_an_older_server_is_not_asked_for_the_spool_again(self, async_client, spoolman_on):
        fake = FakeSpoolman(tag_api=False)
        fake.add_spool(7, extra_tag=TRAY_UUID)

        with serving(fake):
            resp = await async_client.post("/api/v1/spoolman/spools/7/unlink")

        assert resp.status_code == 200
        reads_of_spool_7 = fake.asked("GET /spool/7")
        # merge_spool_extra reads the spool once to merge into it; nothing more.
        assert reads_of_spool_7 == 1


class TestMigration:
    @pytest.fixture
    async def printer(self, db_session: AsyncSession) -> Printer:
        printer = Printer(
            name="X1C",
            serial_number="00M09A350100123",
            ip_address="192.168.0.50",
            access_code="12345678",
            model="X1C",
            is_active=True,
        )
        db_session.add(printer)
        await db_session.commit()
        return printer

    def _inventory(self, printer: Printer) -> FakeSpoolman:
        fake = FakeSpoolman()
        fake.add_spool(1, extra_tag=TRAY_UUID)  # to move
        fake.add_spool(2, extra_tag=CHIP, tags=[CHIP])  # already native
        fake.add_spool(3, extra_tag=get_fallback_spool_tag_for_slot(printer.serial_number, 0, 1))  # a slot, not a tag
        fake.add_spool(4, extra_tag=OTHER_CHIP)  # spool 5 holds it natively
        fake.add_spool(5, tags=[OTHER_CHIP])
        fake.add_spool(6, extra_tag="11223344", archived=True)  # archived: left out
        fake.add_spool(8)  # no tag at all
        return fake

    async def test_the_dry_run_reports_the_real_runs_outcome_and_writes_nothing(
        self, async_client, spoolman_on, printer
    ):
        fake = self._inventory(printer)

        with serving(fake):
            resp = await async_client.post(f"{INVENTORY}/tags/migrate")

        assert resp.status_code == 200
        report = resp.json()
        assert report["dry_run"] is True
        assert report["moved"] == [1]
        assert (report["already"], report["slot_ids"]) == (1, 1)
        assert report["conflicts"] == [{"spool_id": 4, "tag": OTHER_CHIP, "holder": 5}]
        assert fake.tag_writes() == []

    async def test_the_real_run_moves_what_the_dry_run_named(self, async_client, spoolman_on, printer):
        fake = self._inventory(printer)

        with serving(fake):
            dry = (await async_client.post(f"{INVENTORY}/tags/migrate")).json()
            real = (await async_client.post(f"{INVENTORY}/tags/migrate", params={"dry_run": "false"})).json()

        assert real["moved"] == dry["moved"] == [1]
        assert real["conflicts"] == dry["conflicts"]
        assert fake.native(1) == [TRAY_UUID]
        assert fake.native(6) == []
        assert fake.native(3) == []
        assert fake.native(4) == []
        # extra.tag is left as it was, for older readers and a downgrade.
        assert fake.spools[1]["extra"]["tag"] == json.dumps(TRAY_UUID)

    async def test_two_spools_sharing_one_extra_tag_move_the_first_and_report_the_second(
        self, async_client, spoolman_on
    ):
        fake = FakeSpoolman()
        fake.add_spool(1, extra_tag=CHIP)
        fake.add_spool(2, extra_tag=CHIP)

        with serving(fake):
            dry = (await async_client.post(f"{INVENTORY}/tags/migrate")).json()
            real = (await async_client.post(f"{INVENTORY}/tags/migrate", params={"dry_run": "false"})).json()

        expected = [{"spool_id": 2, "tag": CHIP, "holder": 1}]
        assert dry["conflicts"] == real["conflicts"] == expected
        assert fake.holder(CHIP) == 1

    async def test_an_archived_spool_and_its_successor_sharing_a_tag(self, async_client, spoolman_on):
        """The case from the review: the tag goes to the active spool, not the archived one."""
        fake = FakeSpoolman()
        fake.add_spool(1, extra_tag=CHIP, archived=True)
        fake.add_spool(9, extra_tag=CHIP)

        with serving(fake):
            dry = (await async_client.post(f"{INVENTORY}/tags/migrate")).json()
            real = (await async_client.post(f"{INVENTORY}/tags/migrate", params={"dry_run": "false"})).json()

        assert dry["moved"] == real["moved"] == [9]
        assert dry["conflicts"] == real["conflicts"] == []
        assert fake.holder(CHIP) == 9

    async def test_a_tag_already_on_an_archived_spool_is_moved_not_reported(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(1, extra_tag=CHIP, tags=[CHIP], archived=True)
        fake.add_spool(9, extra_tag=CHIP)

        with serving(fake):
            dry = (await async_client.post(f"{INVENTORY}/tags/migrate")).json()
            real = (await async_client.post(f"{INVENTORY}/tags/migrate", params={"dry_run": "false"})).json()

        assert dry["moved"] == real["moved"] == [9]
        assert dry["conflicts"] == real["conflicts"] == []
        assert (fake.native(1), fake.native(9)) == ([], [CHIP])

    async def test_a_padded_ams_chip_uid_is_copied_as_the_chips_own_uid(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(1, extra_tag=f"{CHIP}00000100")  # linked from an AMS slot
        fake.add_spool(2, extra_tag="E004015012345678")  # an 8-byte reader UID stays whole
        fake.add_spool(3, extra_tag=f"{OTHER_CHIP}00000100", tags=[OTHER_CHIP])  # already native

        with serving(fake):
            dry = (await async_client.post(f"{INVENTORY}/tags/migrate")).json()
            real = (await async_client.post(f"{INVENTORY}/tags/migrate", params={"dry_run": "false"})).json()

        assert dry["moved"] == real["moved"] == [1, 2]
        assert dry["already"] == real["already"] == 1
        assert (fake.native(1), fake.native(2)) == ([CHIP], ["E004015012345678"])
        assert fake.spools[1]["tags"][0]["format"] == "bambu"
        # extra.tag keeps the AMS's padded form, which the AMS sync matches on.
        assert fake.spools[1]["extra"]["tag"] == json.dumps(f"{CHIP}00000100")

    async def test_an_older_server_is_refused(self, async_client, spoolman_on):
        fake = FakeSpoolman(tag_api=False)
        fake.add_spool(1, extra_tag=TRAY_UUID)

        with serving(fake):
            resp = await async_client.post(f"{INVENTORY}/tags/migrate", params={"dry_run": "false"})

        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "spoolman_without_tags"
        assert fake.tag_writes() == []


class TestStatus:
    async def test_a_027_server_is_reported_with_native_tags(self, async_client, spoolman_on):
        with serving(FakeSpoolman()):
            status = (await async_client.get("/api/v1/spoolman/status")).json()

        assert (status["connected"], status["native_tags"]) == (True, True)

    async def test_an_older_server_is_reported_without(self, async_client, spoolman_on):
        with serving(FakeSpoolman(tag_api=False)):
            status = (await async_client.get("/api/v1/spoolman/status")).json()

        assert (status["connected"], status["native_tags"]) == (True, False)
