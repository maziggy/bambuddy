"""SpoolmanClient against Spoolman 0.27's native tags, and against an older server (#3168).

Every test runs the real client over HTTP against a fake that enforces the
server's tag rules (see ``backend/tests/_fixtures/spoolman_tags.py``), so what
is asserted is what goes over the wire, not which mock method was called.
"""

from unittest.mock import patch

import httpx
import pytest

from backend.app.services.spoolman import (
    TAG_API_RECHECK_SECONDS,
    TAG_REFUSAL_RETRY_SECONDS,
    AMSTray,
    SpoolmanClient,
)
from backend.tests._fixtures.spoolman_tags import BASE_URL, FakeSpoolman, client_for

TRAY_UUID = "9E0B0717BEE94D7887EB1D8DFD1A14F3"
CHIP = "D3E68F32"
# How the AMS reports that chip: its 4 bytes, padded to 8.
CHIP_FROM_AMS = "D3E68F3200000100"


class _NoCatalog:
    """A DB session whose colour catalogue has no row for anything."""

    async def execute(self, *_args, **_kwargs):
        class _Result:
            @staticmethod
            def scalar_one_or_none():
                return None

        return _Result()


def _tray(tray_uuid: str = TRAY_UUID, tag_uid: str = CHIP_FROM_AMS) -> AMSTray:
    return AMSTray(
        ams_id=0,
        tray_id=0,
        tray_type="PLA",
        tray_sub_brands="PLA Basic",
        tray_color="FF0000FF",
        remain=80,
        tag_uid=tag_uid,
        tray_uuid=tray_uuid,
        tray_info_idx="GFA00",
        tray_weight=1000,
    )


class TestHasTagApi:
    async def test_a_027_server_is_asked_once(self):
        fake = FakeSpoolman()
        client = client_for(fake)

        assert await client.has_tag_api() is True
        assert await client.has_tag_api() is True
        assert fake.asked("GET /tag/reader") == 1

    async def test_an_older_server_answers_404_and_is_not_asked_again(self):
        fake = FakeSpoolman(tag_api=False)
        client = client_for(fake)

        assert await client.has_tag_api() is False
        assert await client.has_tag_api() is False
        assert fake.asked("GET /tag/reader") == 1

    async def test_a_server_that_is_down_is_asked_again_next_time(self):
        calls = []

        async def down(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            raise httpx.ConnectError("refused", request=request)

        client = SpoolmanClient(BASE_URL)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(down))

        assert await client.has_tag_api() is False
        assert await client.has_tag_api() is False
        assert len(calls) == 2

    async def test_a_no_is_asked_again_later_so_an_upgrade_is_picked_up(self):
        fake = FakeSpoolman(tag_api=False)
        client = client_for(fake)
        now = [1000.0]

        with patch("backend.app.services.spoolman.time.monotonic", lambda: now[0]):
            assert await client.has_tag_api() is False
            fake.tag_api = True  # Spoolman upgraded to 0.27 while Bambuddy runs
            now[0] += TAG_API_RECHECK_SECONDS - 1
            assert await client.has_tag_api() is False
            now[0] += 2
            assert await client.has_tag_api() is True

        assert fake.asked("GET /tag/reader") == 2


class TestFindSpoolByTag:
    async def test_027_answers_with_one_query_and_never_loads_the_inventory(self):
        fake = FakeSpoolman()
        for spool_id in range(1, 50):
            fake.add_spool(spool_id)
        fake.add_spool(50, tags=[CHIP])
        client = client_for(fake)

        spool = await client.find_spool_by_tag(CHIP.lower())

        assert spool["id"] == 50
        assert fake.asked(f"GET /spool?tag={CHIP}") == 1
        assert fake.asked("GET /spool") == 0

    async def test_a_tag_only_in_extra_tag_is_still_found(self):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=TRAY_UUID)
        client = client_for(fake)

        spool = await client.find_spool_by_tag(TRAY_UUID)

        assert spool["id"] == 7
        assert fake.asked("GET /spool") == 1

    async def test_a_server_that_ignores_the_tag_parameter_matches_nothing(self):
        """Spoolman downgraded underneath: ?tag= is ignored and every spool comes back."""
        fake = FakeSpoolman(tag_api=False)
        fake.add_spool(3)
        client = client_for(fake)

        assert await client.find_spool_by_native_tag(CHIP) is None

    async def test_an_older_server_is_searched_through_extra_tag_alone(self):
        fake = FakeSpoolman(tag_api=False)
        fake.add_spool(3)
        fake.add_spool(7, extra_tag=TRAY_UUID)
        client = client_for(fake)

        spool = await client.find_spool_by_tag(TRAY_UUID)

        assert spool["id"] == 7
        assert not any("?tag=" in entry for entry in fake.log)


class TestLinkNativeTag:
    async def test_a_free_uid_is_linked(self):
        fake = FakeSpoolman()
        fake.add_spool(7)
        client = client_for(fake)

        assert await client.link_native_tag(7, CHIP) is None
        assert fake.native(7) == [CHIP]

    async def test_a_uid_another_spool_holds_names_that_spool(self):
        fake = FakeSpoolman()
        fake.add_spool(7)
        fake.add_spool(9, tags=[CHIP])
        client = client_for(fake)

        assert await client.link_native_tag(7, CHIP) == 9
        assert fake.native(7) == []

    async def test_a_uid_a_filament_holds_answers_minus_one(self):
        fake = FakeSpoolman()
        fake.add_spool(7)
        fake.filament_tags[CHIP] = 4
        client = client_for(fake)

        assert await client.link_native_tag(7, CHIP) == -1


class TestClaimNativeTag:
    async def test_a_tag_an_archived_spool_holds_is_taken_from_it(self):
        fake = FakeSpoolman()
        fake.add_spool(1, tags=[CHIP], archived=True)
        fake.add_spool(7)
        client = client_for(fake)

        assert await client.claim_native_tag(7, CHIP) is None
        assert (fake.native(1), fake.native(7)) == ([], [CHIP])

    async def test_a_tag_an_active_spool_holds_stays_there(self):
        fake = FakeSpoolman()
        fake.add_spool(1, tags=[CHIP])
        fake.add_spool(7)
        client = client_for(fake)

        assert await client.claim_native_tag(7, CHIP) == 1
        assert (fake.native(1), fake.native(7)) == ([CHIP], [])


class TestAddNativeTags:
    async def test_adds_what_is_missing_and_skips_what_cannot_go(self):
        fake = FakeSpoolman()
        spool = fake.add_spool(7, tags=[TRAY_UUID])
        fake.add_spool(9, tags=["AABBCCDD"])
        client = client_for(fake)

        added = await client.add_native_tags(spool, [f'"{TRAY_UUID}"', CHIP.lower(), "00000000", None, "AABBCCDD"])

        # The tray UUID it had, the zero UID and the missing value are skipped; spool 9
        # keeps its tag without the call failing.
        assert added == 1
        assert fake.native(7) == sorted([TRAY_UUID, CHIP])
        assert fake.native(9) == ["AABBCCDD"]
        assert fake.asked("POST /spool/7/tag") == 2

    async def test_an_older_server_gets_no_tag_request(self):
        fake = FakeSpoolman(tag_api=False)
        spool = fake.add_spool(7)
        client = client_for(fake)

        assert await client.add_native_tags(spool, [TRAY_UUID, CHIP]) == 0
        assert fake.tag_writes() == []

    async def test_a_refused_tag_is_not_asked_for_on_every_update(self, caplog):
        """The AMS sync calls this on every AMS update; a refusal must not repeat each time."""
        fake = FakeSpoolman()
        fake.add_spool(1, tags=[CHIP])
        spool = fake.add_spool(7)
        client = client_for(fake)
        now = [1000.0]

        with patch("backend.app.services.spoolman.time.monotonic", lambda: now[0]):
            for _ in range(5):
                await client.add_native_tags(spool, [CHIP])
                now[0] += 60
            assert fake.asked("POST /spool/7/tag") == 1

            # The conflict is settled in Spoolman; the next try after the wait picks it up.
            fake.spools[1]["tags"] = []
            now[0] += TAG_REFUSAL_RETRY_SECONDS
            assert await client.add_native_tags(spool, [CHIP]) == 1

        assert fake.native(7) == [CHIP]
        warnings = [r for r in caplog.records if r.levelname == "WARNING" and CHIP in r.getMessage()]
        assert len(warnings) == 1

    async def test_a_scan_asks_again_at_once_and_still_holds_off_the_ams_sync(self):
        fake = FakeSpoolman()
        fake.add_spool(1, tags=[CHIP])
        spool = fake.add_spool(7)
        client = client_for(fake)

        await client.add_native_tags(spool, [CHIP], retry_refused=True)
        await client.add_native_tags(spool, [CHIP], retry_refused=True)
        assert fake.asked("POST /spool/7/tag") == 2

        await client.add_native_tags(spool, [CHIP])  # the AMS sync, right after
        assert fake.asked("POST /spool/7/tag") == 2


class TestUnlinkAllNativeTags:
    async def test_every_tag_of_the_spool_goes(self):
        fake = FakeSpoolman()
        spool = fake.add_spool(7, tags=[TRAY_UUID, CHIP])
        client = client_for(fake)

        await client.unlink_all_native_tags(spool)

        assert fake.native(7) == []


class TestAmsSync:
    async def test_a_known_spool_collects_the_tray_uuid_and_the_chips_own_uid(self):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=TRAY_UUID)
        client = client_for(fake)

        result = await client.sync_ams_tray(_tray(), "X1C", _NoCatalog())

        assert result["id"] == 7
        assert fake.native(7) == sorted([TRAY_UUID, CHIP])
        assert CHIP_FROM_AMS not in fake.native(7)

    async def test_a_spool_a_reader_linked_by_its_chip_alone_is_found(self):
        """SpoolBuddy stores the chip's own 4 bytes; the AMS reports them padded."""
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=CHIP, tags=[CHIP])
        client = client_for(fake)

        result = await client.sync_ams_tray(_tray(), "X1C", _NoCatalog(), cached_spools=await client.get_spools())

        assert result["id"] == 7
        assert fake.native(7) == sorted([TRAY_UUID, CHIP])
        assert not any(e == "POST /spool" for e in fake.log), "the spool was created a second time"

    async def test_an_older_server_is_synced_without_tag_requests(self):
        fake = FakeSpoolman(tag_api=False)
        fake.add_spool(7, extra_tag=TRAY_UUID)
        client = client_for(fake)

        result = await client.sync_ams_tray(_tray(), "X1C", _NoCatalog())

        assert result["id"] == 7
        assert fake.tag_writes() == []
