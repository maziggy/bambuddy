"""Extra-field registration reads Spoolman's listing (#2983, reported by @ngreatorex).

``ensure_extra_field`` used to probe ``GET /field/spool/{name}`` per field.
Spoolman declares only POST and DELETE at that path -- verified against its own
OpenAPI document -- so the probe answered 405 and the existence check could
never succeed. Every call fell through to ``POST /field/spool/{name}``, which is
an *upsert*: it answered 200 whether or not the field existed, so a field a user
had renamed or retyped in Spoolman's UI was reset to Bambuddy's version of it
on every client init, and an untrue "Created Spoolman extra field" was logged
each time.

Existence now comes from ``GET /field/spool``, matched on ``key``.

These drive ``ensure_extra_field``'s own branches directly, with the HTTP layer
stubbed, so the awkward answers can be posed one at a time -- a listing that
isn't a list, a row with no key, a transport error mid-read.
``test_spoolman_extra_field_registration_2903`` covers the other half from the
outside, through a fake Spoolman that enforces the real API's rules; its fake
answers the per-field path 405, as the real server does.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.spoolman import SpoolmanClient

BAMBU_FIELDS = ("tag", "bambu_slicer_filament", "bambu_slicer_filament_name", "bambu_color_name")


def _response(status: int, payload=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text if payload is None else json.dumps(payload)
    r.json = MagicMock(return_value=payload)
    return r


def _field(key: str, name: str | None = None) -> dict:
    """A Spoolman field row. `name` is the user-editable label, `key` the
    identifier the spool's extra dict is written under."""
    return {
        "key": key,
        "name": name if name is not None else key,
        "field_type": "text",
        "entity_type": "spool",
        "order": 0,
    }


LISTING_URL = "http://spoolman.test/api/v1/field/spool"


def _client_with(listing, post_status: int = 200) -> tuple[SpoolmanClient, MagicMock]:
    """A SpoolmanClient whose HTTP calls are recorded.

    ``listing`` is either the parsed body of ``GET /field/spool`` or a
    ready-made response for the failure cases.

    GET is routed by URL rather than answering everything with the listing.
    That matters: a mock that returns the listing for any GET also answers the
    old per-field probe with it, which would let the pre-fix code pass these
    tests. The real server answers ``GET /field/spool/{key}`` with **405**,
    because it declares only POST and DELETE there -- so that is what any URL
    other than the listing gets here.
    """
    listing_response = listing if isinstance(listing, MagicMock) else _response(200, listing)

    async def get(url, *args, **kwargs):
        if url == LISTING_URL:
            return listing_response
        return _response(405, None, '{"detail":"Method Not Allowed"}')

    http = MagicMock()
    http.get = AsyncMock(side_effect=get)
    http.post = AsyncMock(return_value=_response(post_status, {}))
    client = SpoolmanClient("http://spoolman.test")
    client._get_client = AsyncMock(return_value=http)
    return client, http


class TestTheExistenceCheck:
    @pytest.mark.asyncio
    async def test_reads_the_listing_endpoint_not_the_per_field_one(self):
        client, http = _client_with([_field("tag")])
        await client.ensure_extra_field("tag")
        (url,), _ = http.get.call_args
        assert url == "http://spoolman.test/api/v1/field/spool"

    @pytest.mark.asyncio
    async def test_an_existing_field_is_never_posted(self):
        """The whole point: POST is an upsert, so posting an existing field is
        what reset the user's customisation of it."""
        client, http = _client_with([_field("tag", "Bambu RFID Tag")])
        assert await client.ensure_extra_field("tag") is True
        http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_matches_on_key_not_on_the_user_facing_name(self):
        """A renamed field is still the same field. Matching on `name` would
        make a rename look like a deletion and re-create it -- clobbering the
        rename, which is exactly the reported behaviour."""
        client, http = _client_with([_field("bambu_color_name", "Bambu Colour")])
        assert await client.ensure_extra_field("bambu_color_name") is True
        http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_missing_field_is_created(self):
        client, http = _client_with([_field("tag")])
        assert await client.ensure_extra_field("bambu_color_name") is True
        http.post.assert_awaited_once()
        (url,), kwargs = http.post.call_args
        assert url == "http://spoolman.test/api/v1/field/spool/bambu_color_name"
        assert kwargs["json"] == {
            "name": "bambu_color_name",
            "field_type": "text",
            "default_value": None,
        }

    @pytest.mark.asyncio
    async def test_creates_every_field_when_spoolman_has_none(self):
        """An empty listing is a real answer -- "no extra fields yet" -- and
        must be acted on, unlike an unreadable one."""
        client, http = _client_with([])
        for name in BAMBU_FIELDS:
            assert await client.ensure_extra_field(name) is True
        assert http.post.await_count == len(BAMBU_FIELDS)

    @pytest.mark.asyncio
    async def test_honours_a_non_default_field_type_when_creating(self):
        client, http = _client_with([])
        await client.ensure_extra_field("some_number", field_type="integer")
        assert http.post.call_args.kwargs["json"]["field_type"] == "integer"


class TestRequestCount:
    @pytest.mark.asyncio
    async def test_registering_every_field_costs_one_listing_read(self):
        client, http = _client_with([_field(k) for k in BAMBU_FIELDS])
        await client._ensure_extra_fields(dict.fromkeys(BAMBU_FIELDS, "value"))
        assert http.get.await_count == 1
        http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creating_several_new_fields_still_reads_the_listing_once(self):
        client, http = _client_with([])
        await client._ensure_extra_fields(dict.fromkeys(BAMBU_FIELDS, "value"))
        assert http.get.await_count == 1
        assert http.post.await_count == len(BAMBU_FIELDS)

    @pytest.mark.asyncio
    async def test_a_second_pass_on_the_same_client_makes_no_requests(self):
        client, http = _client_with([_field(k) for k in BAMBU_FIELDS])
        await client._ensure_extra_fields(dict.fromkeys(BAMBU_FIELDS, "value"))
        http.get.reset_mock()
        await client._ensure_extra_fields(dict.fromkeys(BAMBU_FIELDS, "value"))
        http.get.assert_not_awaited()
        http.post.assert_not_awaited()


class TestDegradingWhenTheListingCannotBeRead:
    """An unreadable listing must not stop a write that might still succeed --
    registration is best-effort, and falling back to the blind POST is exactly
    what shipped before."""

    @pytest.mark.asyncio
    async def test_a_non_200_listing_falls_back_to_posting(self):
        client, http = _client_with(_response(404, None, "Not Found"))
        assert await client.ensure_extra_field("tag") is True
        http.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_transport_error_on_the_listing_falls_back_to_posting(self):
        client, http = _client_with([])
        http.get = AsyncMock(side_effect=OSError("connection reset"))
        assert await client.ensure_extra_field("tag") is True
        http.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_listing_that_is_not_a_list_falls_back_to_posting(self):
        client, http = _client_with(_response(200, {"detail": "Method Not Allowed"}))
        assert await client.ensure_extra_field("tag") is True
        http.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rows_without_a_usable_key_are_skipped_not_fatal(self):
        client, http = _client_with([{"name": "orphan"}, "junk", _field("tag")])
        assert await client.ensure_extra_field("tag") is True
        http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_field_created_blind_is_not_looked_up_again(self):
        """The blind POST succeeded, so that field demonstrably exists now.
        Re-reading the listing to confirm it would be a wasted request."""
        client, http = _client_with([])
        http.get = AsyncMock(side_effect=[_response(503), _response(200, [_field("tag")])])
        assert await client.ensure_extra_field("tag") is True
        assert await client.ensure_extra_field("tag") is True
        assert http.get.await_count == 1

    @pytest.mark.asyncio
    async def test_a_later_field_still_gets_a_listing_read(self):
        """Only a *successful* read is banked. A client that could not reach the
        listing once must not spend the rest of its life posting blind -- the
        next field it has never seen has to ask again, or a transient blip
        would clobber every remaining field's customisation."""
        client, http = _client_with([])
        http.get = AsyncMock(
            side_effect=[_response(503), _response(200, [_field("bambu_color_name")])],
        )
        await client.ensure_extra_field("tag")
        http.post.reset_mock()
        assert await client.ensure_extra_field("bambu_color_name") is True
        assert http.get.await_count == 2
        http.post.assert_not_awaited()


class TestFailureReporting:
    @pytest.mark.asyncio
    async def test_a_rejected_creation_returns_false(self):
        client, _ = _client_with([], post_status=400)
        assert await client.ensure_extra_field("tag") is False

    @pytest.mark.asyncio
    async def test_a_rejected_creation_is_not_remembered_as_ensured(self):
        client, _ = _client_with([], post_status=400)
        await client.ensure_extra_field("tag")
        assert "tag" not in client._ensured_extra_fields

    @pytest.mark.asyncio
    async def test_ensure_tag_extra_field_goes_through_the_same_path(self):
        client, http = _client_with([_field("tag", "Bambu RFID Tag")])
        assert await client.ensure_tag_extra_field() is True
        http.post.assert_not_awaited()
