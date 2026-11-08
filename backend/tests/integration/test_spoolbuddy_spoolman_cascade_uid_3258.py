"""SpoolBuddy scans in Spoolman mode against tag links stored from cascade level 1 (#3258).

SpoolBuddy daemons before #3258 read an NTAG only to cascade level 1 and reported
04ECE673D32A81 as 8804ECE6 (cascade tag 88 + its first three bytes), so Spoolman
spools linked on the scale carry that form as a native tag or in extra.tag. These
are the Spoolman-mode counterparts of the built-in inventory's tests in
``test_spool_tag_matcher.py``, with the same rules.

Most tests run the scan route against a mocked SpoolmanClient. The last class runs
a real client against the fake Spoolman of ``backend/tests/_fixtures/spoolman_tags.py``.
"""

import json
import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.services.spoolman import SpoolmanClient, SpoolmanClientError, SpoolmanUnavailableError
from backend.tests._fixtures.spoolman_tags import BASE_URL, FakeSpoolman, client_for

SCAN = "/api/v1/spoolbuddy/nfc/tag-scanned"

NTAG_UID = "04ECE673D32A81"
NTAG_LEVEL1 = "8804ECE6"
AMBIGUOUS = "fits the tag links of spools {} at cascade level 1; not guessing between them"


@pytest.fixture
async def spoolman_on(db_session: AsyncSession):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value=BASE_URL))
    await db_session.commit()


def _spool(spool_id: int, *, tags=(), extra_tag: str | None = None, archived: bool = False) -> dict:
    """A Spoolman spool as the API answers it; ``tags`` None for a server without native tags."""
    spool = {
        "id": spool_id,
        "filament": {
            "material": "PLA",
            "name": "PLA Basic",
            "color_hex": "FF0000",
            "weight": 1000.0,
            "spool_weight": 196.0,
            "vendor": {"name": "Bambu Lab"},
        },
        "used_weight": 0.0,
        "archived": archived,
        "registered": "2026-10-01T00:00:00Z",
        "extra": {"tag": json.dumps(extra_tag)} if extra_tag is not None else {},
    }
    if tags is not None:
        spool["tags"] = [{"uid": uid} for uid in tags]
    return spool


def _client(spools: list[dict], *, tag_api: bool = True, current: dict | None = None):
    """A mocked SpoolmanClient whose exact lookups miss and whose listing is ``spools``.

    ``current`` is what get_spool answers after the scan linked its tags.
    """
    client = create_autospec(SpoolmanClient, instance=True)
    client.base_url = BASE_URL
    client.has_tag_api.return_value = tag_api
    client.find_spool_by_native_tag.return_value = None
    client.find_spool_by_tag.return_value = None
    client.get_spools.return_value = spools
    client.add_native_tags.return_value = 1 if tag_api else 0
    client.get_spool.return_value = current
    client.merge_spool_extra.return_value = {}
    client.unlink_native_tag.return_value = None
    return client


@contextmanager
def serving(client):
    getter = AsyncMock(return_value=client)
    with (
        patch("backend.app.services.spoolman.get_spoolman_client", getter),
        patch("backend.app.services.spoolman.init_spoolman_client", getter),
        patch("backend.app.api.routes.spoolbuddy.ws_manager") as ws,
    ):
        ws.broadcast = AsyncMock()
        yield ws


async def _scan(async_client: AsyncClient, tag_uid: str, sak: int | None = None) -> dict:
    body = {"device_id": "sb-1", "tag_uid": tag_uid}
    if sak is not None:
        body["sak"] = sak
    resp = await async_client.post(SCAN, json=body)
    assert resp.status_code == 200
    return resp.json()


def _broadcast_types(ws) -> list[str]:
    return [call.args[0]["type"] for call in ws.broadcast.call_args_list]


def _native_uids_added(client) -> list:
    return [uid for call in client.add_native_tags.await_args_list for uid in call.args[1] if uid]


class TestFullUidScan:
    """A full 7- or 10-byte read finds the spool linked as 88 + its first three bytes."""

    async def test_the_88_native_tag_is_unlinked_once_the_full_uid_is_linked(self, async_client, spoolman_on):
        spool = _spool(7, tags=[NTAG_LEVEL1])
        client = _client([spool], current=_spool(7, tags=[NTAG_LEVEL1, NTAG_UID]))

        with serving(client) as ws:
            result = await _scan(async_client, NTAG_UID)

        assert result == {"status": "ok", "matched": True, "spool_id": 7}
        # The route's own add_native_tags call links the full UID.
        assert _native_uids_added(client) == [NTAG_UID]
        client.unlink_native_tag.assert_awaited_once_with(7, NTAG_LEVEL1)
        client.merge_spool_extra.assert_not_awaited()
        assert _broadcast_types(ws) == ["spoolbuddy_tag_matched", "inventory_changed"]

    async def test_an_88_extra_tag_is_rewritten_to_the_full_uid(self, async_client, spoolman_on):
        """A Spoolman without native tags: extra.tag is the only link."""
        client = _client([_spool(7, tags=None, extra_tag=NTAG_LEVEL1)], tag_api=False)

        with serving(client):
            result = await _scan(async_client, NTAG_UID)

        assert result["spool_id"] == 7
        client.merge_spool_extra.assert_awaited_once_with(7, {"tag": json.dumps(NTAG_UID)})
        client.unlink_native_tag.assert_not_awaited()

    async def test_both_88_links_move_to_the_full_uid(self, async_client, spoolman_on):
        spool = _spool(7, tags=[NTAG_LEVEL1], extra_tag=NTAG_LEVEL1)
        client = _client([spool], current=_spool(7, tags=[NTAG_LEVEL1, NTAG_UID], extra_tag=NTAG_UID))

        with serving(client):
            result = await _scan(async_client, NTAG_UID)

        assert result["spool_id"] == 7
        assert _native_uids_added(client) == [NTAG_UID]
        client.merge_spool_extra.assert_awaited_once_with(7, {"tag": json.dumps(NTAG_UID)})
        client.unlink_native_tag.assert_awaited_once_with(7, NTAG_LEVEL1)

    async def test_a_ten_byte_uid_finds_its_88_link(self, async_client, spoolman_on):
        full = "04112233445566778899"
        client = _client([_spool(7, tags=None, extra_tag="88041122")], tag_api=False)

        with serving(client):
            result = await _scan(async_client, full)

        assert result["spool_id"] == 7
        # Spoolman has no length limit, so the whole UID is stored.
        client.merge_spool_extra.assert_awaited_once_with(7, {"tag": json.dumps(full)})

    async def test_an_archived_spool_neither_matches_nor_makes_it_ambiguous(self, async_client, spoolman_on):
        client = _client(
            [_spool(1, extra_tag=NTAG_LEVEL1, archived=True), _spool(7, extra_tag=NTAG_LEVEL1)],
            current=_spool(7, tags=[NTAG_UID], extra_tag=NTAG_UID),
        )

        with serving(client):
            result = await _scan(async_client, NTAG_UID)

        assert result["spool_id"] == 7
        client.merge_spool_extra.assert_awaited_once_with(7, {"tag": json.dumps(NTAG_UID)})

    async def test_a_spool_matched_exactly_that_still_carries_the_88_form_finishes_the_move(
        self, async_client, spoolman_on
    ):
        """An earlier move stopped half-way: linked by the full UID, the 88 forms still there."""
        spool = _spool(7, tags=[NTAG_UID, NTAG_LEVEL1], extra_tag=NTAG_LEVEL1)
        client = _client([], current=spool)
        client.find_spool_by_native_tag.return_value = spool

        with serving(client):
            result = await _scan(async_client, NTAG_UID)

        assert result["spool_id"] == 7
        client.get_spools.assert_not_awaited()
        client.merge_spool_extra.assert_awaited_once_with(7, {"tag": json.dumps(NTAG_UID)})
        client.unlink_native_tag.assert_awaited_once_with(7, NTAG_LEVEL1)

    async def test_a_four_byte_uid_ignores_88_links(self, async_client, spoolman_on):
        client = _client([_spool(7, tags=[NTAG_LEVEL1], extra_tag=NTAG_LEVEL1)])

        with serving(client):
            result = await _scan(async_client, "04ECE673")

        assert result["matched"] is False
        client.add_native_tags.assert_not_awaited()


class TestLevel1Scan:
    """A level-1 read from a daemon not updated yet finds the spool linked by the full UID."""

    async def test_a_native_full_uid_is_matched_and_nothing_is_written(self, async_client, spoolman_on):
        client = _client([_spool(7, tags=[NTAG_UID])])

        with serving(client) as ws:
            result = await _scan(async_client, NTAG_LEVEL1)

        assert result == {"status": "ok", "matched": True, "spool_id": 7}
        # The 88 form is not linked: three bytes can begin more than one tag.
        assert _native_uids_added(client) == []
        client.merge_spool_extra.assert_not_awaited()
        client.unlink_native_tag.assert_not_awaited()
        client.get_spool.assert_not_awaited()
        assert _broadcast_types(ws) == ["spoolbuddy_tag_matched"]

    async def test_a_full_uid_in_extra_tag_is_matched_and_nothing_is_written(self, async_client, spoolman_on):
        client = _client([_spool(7, tags=None, extra_tag=NTAG_UID)], tag_api=False)

        with serving(client):
            result = await _scan(async_client, NTAG_LEVEL1)

        assert result["spool_id"] == 7
        client.merge_spool_extra.assert_not_awaited()

    async def test_an_old_daemon_reports_the_sak_of_level_1(self, async_client, spoolman_on):
        client = _client([_spool(7, tags=[NTAG_UID])])

        with serving(client):
            result = await _scan(async_client, NTAG_LEVEL1, sak=0x04)

        assert result["spool_id"] == 7

    async def test_a_complete_uid_beginning_with_88_is_no_level1_read(self, async_client, spoolman_on):
        """A SAK without bit 0x04 says the UID ended there: a 4-byte clone card, not a level-1 read."""
        client = _client([_spool(7, tags=[NTAG_UID])])

        with serving(client):
            result = await _scan(async_client, NTAG_LEVEL1, sak=0x08)

        assert result["matched"] is False

    async def test_only_a_seven_byte_uid_can_be_what_it_begins(self, async_client, spoolman_on):
        client = _client([_spool(7, tags=["04ECE673D32A8100"], extra_tag="04ECE673")])

        with serving(client):
            result = await _scan(async_client, NTAG_LEVEL1)

        assert result["matched"] is False


class TestTwoCandidates:
    """Two fitting spools may hold two different tags: no guess, nothing written."""

    async def test_full_uid_scan_with_two_88_links(self, async_client, spoolman_on, caplog):
        client = _client([_spool(3, tags=[NTAG_LEVEL1]), _spool(7, extra_tag=NTAG_LEVEL1)])

        with serving(client) as ws, caplog.at_level(logging.WARNING):
            result = await _scan(async_client, NTAG_UID)

        assert result == {"status": "ok", "matched": False, "spool_id": None}
        assert _broadcast_types(ws) == ["spoolbuddy_unknown_tag"]
        assert f"Tag {NTAG_UID} " + AMBIGUOUS.format([3, 7]) in caplog.text
        client.add_native_tags.assert_not_awaited()
        client.merge_spool_extra.assert_not_awaited()
        client.unlink_native_tag.assert_not_awaited()

    async def test_level1_scan_with_two_full_uids_it_begins(self, async_client, spoolman_on, caplog):
        client = _client([_spool(3, tags=[NTAG_UID]), _spool(7, extra_tag="04ECE6AABBCCDD")])

        with serving(client) as ws, caplog.at_level(logging.WARNING):
            result = await _scan(async_client, NTAG_LEVEL1)

        assert result["matched"] is False
        assert _broadcast_types(ws) == ["spoolbuddy_unknown_tag"]
        assert f"Tag {NTAG_LEVEL1} " + AMBIGUOUS.format([3, 7]) in caplog.text
        client.add_native_tags.assert_not_awaited()
        client.merge_spool_extra.assert_not_awaited()


class TestFailedWrites:
    """A refused or failed write keeps the match; the next scan tries the move again."""

    async def test_a_refused_extra_tag_rewrite_keeps_the_match_and_is_retried(self, async_client, spoolman_on):
        client = _client([_spool(7, tags=None, extra_tag=NTAG_LEVEL1)], tag_api=False)
        client.merge_spool_extra.side_effect = SpoolmanClientError("Spoolman rejected update", 400)

        with serving(client) as ws:
            first = await _scan(async_client, NTAG_UID)
            second = await _scan(async_client, NTAG_UID)

        assert first["spool_id"] == second["spool_id"] == 7
        assert client.merge_spool_extra.await_count == 2
        assert _broadcast_types(ws) == ["spoolbuddy_tag_matched", "spoolbuddy_tag_matched"]

    async def test_the_88_native_tag_stays_when_the_full_uid_was_not_linked(self, async_client, spoolman_on):
        """add_native_tags was refused: unlinking the 88 tag would leave the spool without a link."""
        client = _client([_spool(7, tags=[NTAG_LEVEL1])], current=_spool(7, tags=[NTAG_LEVEL1]))
        client.add_native_tags.return_value = 0

        with serving(client):
            result = await _scan(async_client, NTAG_UID)

        assert result["spool_id"] == 7
        client.unlink_native_tag.assert_not_awaited()

    async def test_a_failing_unlink_keeps_the_match(self, async_client, spoolman_on):
        client = _client([_spool(7, tags=[NTAG_LEVEL1])], current=_spool(7, tags=[NTAG_LEVEL1, NTAG_UID]))
        client.unlink_native_tag.side_effect = SpoolmanUnavailableError("Cannot reach Spoolman")

        with serving(client):
            result = await _scan(async_client, NTAG_UID)

        assert result == {"status": "ok", "matched": True, "spool_id": 7}


@contextmanager
def serving_fake(fake: FakeSpoolman):
    with serving(client_for(fake)) as ws:
        yield ws


class TestAgainstFakeSpoolman:
    """The same move with a real SpoolmanClient, against a server that enforces the tag rules."""

    async def test_both_88_links_move_and_the_next_scan_is_an_exact_match(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=NTAG_LEVEL1, tags=[NTAG_LEVEL1])

        with serving_fake(fake):
            first = await _scan(async_client, NTAG_UID)
            listings = fake.asked("GET /spool")
            second = await _scan(async_client, NTAG_UID)

        assert first["spool_id"] == second["spool_id"] == 7
        assert fake.native(7) == [NTAG_UID]
        assert fake.spools[7]["extra"]["tag"] == json.dumps(NTAG_UID)
        assert fake.asked("GET /spool") == listings, "the second scan loaded the inventory again"

    async def test_a_failed_extra_tag_write_is_finished_on_the_next_scan(self, async_client, spoolman_on):
        fake = FakeSpoolman()
        fake.add_spool(7, extra_tag=NTAG_LEVEL1, tags=[NTAG_LEVEL1])
        fake.fail_patch.add(7)

        with serving_fake(fake):
            first = await _scan(async_client, NTAG_UID)
            assert fake.spools[7]["extra"]["tag"] == json.dumps(NTAG_LEVEL1)
            fake.fail_patch.clear()
            second = await _scan(async_client, NTAG_UID)

        assert first["spool_id"] == second["spool_id"] == 7
        assert fake.native(7) == [NTAG_UID]
        assert fake.spools[7]["extra"]["tag"] == json.dumps(NTAG_UID)

    async def test_an_older_server_gets_extra_tag_rewritten_and_no_tag_writes(self, async_client, spoolman_on):
        fake = FakeSpoolman(tag_api=False)
        fake.add_spool(7, extra_tag=NTAG_LEVEL1)

        with serving_fake(fake):
            result = await _scan(async_client, NTAG_UID)

        assert result["spool_id"] == 7
        assert fake.spools[7]["extra"]["tag"] == json.dumps(NTAG_UID)
        assert fake.tag_writes() == []
