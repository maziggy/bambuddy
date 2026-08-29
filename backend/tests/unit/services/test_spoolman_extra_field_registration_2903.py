"""Extra-field registration travels with the write that needs it (issue #2903).

Spoolman rejects any spool payload carrying an ``extra`` key it has not been
told about, answering HTTP 400 ``Unknown extra field <name>.``. Bambuddy used
to register those keys from three hand-maintained lists that ran when the
integration was *set up* -- the connect route, application startup, and two
inline blocks in the inventory routes. Enabling Spoolman from Settings runs
none of them, so the first "Sync AMS Data" against a fresh Spoolman failed on
every slot.

The fake below is the point of these tests: it enforces Spoolman's rule rather
than mocking it away, so every test here fails against the old code for the
same reason the reporter's install did.
"""

import asyncio
import json

import httpx
import pytest

from backend.app.services.spoolman import AMSTray, SpoolmanClient


class FakeSpoolman:
    """A Spoolman that rejects unregistered extra keys, the way the real one does."""

    def __init__(self, *, registered: set[str] | None = None, field_status: int = 200):
        self.registered: set[str] = set(registered or ())
        # Lets a test make registration fail without breaking anything else.
        self.field_status = field_status
        self.spools: dict[int, dict] = {}
        self.log: list[str] = []
        self._next_id = 1

    def _reject_unknown_extra(self, body: dict) -> httpx.Response | None:
        for name in body.get("extra") or {}:
            if name not in self.registered:
                return httpx.Response(400, json={"message": f"Unknown extra field {name}."})
        return None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        # Yield to the event loop on every call, so two coroutines driving this
        # fake genuinely interleave. Without it MockTransport answers without
        # ever suspending, and a "concurrent" test runs each request to
        # completion in turn -- proving nothing about the locking below.
        await asyncio.sleep(0)
        path = request.url.path.removeprefix("/api/v1")
        self.log.append(f"{request.method} {path}")
        body = json.loads(request.content) if request.content else {}

        if path == "/field/spool" and request.method == "GET":
            if self.field_status != 200:
                return httpx.Response(self.field_status)
            return httpx.Response(
                200,
                json=[
                    {"key": name, "name": name, "field_type": "text", "entity_type": "spool"}
                    for name in sorted(self.registered)
                ],
            )

        if path.startswith("/field/spool/"):
            name = path.rsplit("/", 1)[-1]
            # Spoolman declares only POST and DELETE here -- its own OpenAPI
            # document says so, and the live server answers 405. Modelling this
            # as a working existence probe is what let the bug in issue #2983
            # sit unnoticed: the check could never succeed, and the POST that
            # followed silently overwrote whatever the user had customised.
            if request.method != "POST":
                return httpx.Response(405, json={"detail": "Method Not Allowed"})
            if self.field_status != 200:
                return httpx.Response(self.field_status)
            self.registered.add(name)
            return httpx.Response(200, json={"name": name})

        if path == "/spool" and request.method == "POST":
            if (rejection := self._reject_unknown_extra(body)) is not None:
                return rejection
            spool = {"id": self._next_id, **body}
            self.spools[self._next_id] = spool
            self._next_id += 1
            return httpx.Response(200, json=spool)

        if path.startswith("/spool/"):
            spool_id = int(path.rsplit("/", 1)[-1])
            if request.method == "GET":
                return httpx.Response(200, json=self.spools[spool_id])
            if (rejection := self._reject_unknown_extra(body)) is not None:
                return rejection
            self.spools[spool_id].update(body)
            return httpx.Response(200, json=self.spools[spool_id])

        if path == "/vendor":
            if request.method == "GET":
                return httpx.Response(200, json=[{"id": 1, "name": "Bambu Lab"}])
            return httpx.Response(200, json={"id": 1, "name": body.get("name", "")})

        if path == "/filament":
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"id": 7, **body})

        if path == "/external/filament":
            return httpx.Response(200, json=[])

        return httpx.Response(200, json=[])

    def field_calls(self, name: str) -> list[str]:
        """Every request this client made about ``name``: the listing read that
        answers "does it exist", plus any creation of that specific field."""
        return [entry for entry in self.log if entry == "GET /field/spool" or entry.endswith(f"/field/spool/{name}")]


def _client(fake: FakeSpoolman) -> SpoolmanClient:
    client = SpoolmanClient("https://spoolman.test")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return client


def _tray(tray_uuid: str) -> AMSTray:
    return AMSTray(
        ams_id=0,
        tray_id=0,
        tray_type="PLA",
        tray_sub_brands="PLA Basic",
        tray_color="000000FF",
        remain=100,
        tag_uid="",
        tray_uuid=tray_uuid,
        tray_info_idx="GFA00",
        tray_weight=1000,
    )


class TestTheReportedCase:
    """A fresh Spoolman, a fresh Bambuddy, and the first AMS sync."""

    @pytest.mark.asyncio
    async def test_syncing_a_slot_no_longer_fails_on_a_fresh_spoolman(self):
        fake = FakeSpoolman()  # GET /field/spool returns nothing: no custom fields at all
        client = _client(fake)

        result = await client.sync_ams_tray(_tray("D144798DEF394926ACAE9D69ABA910CC"), "OJIMPO-X2D-01")

        assert result is not None, "spool creation was rejected -- this is the reported 400"
        assert result["extra"]["tag"] == json.dumps("D144798DEF394926ACAE9D69ABA910CC")
        assert "tag" in fake.registered

    @pytest.mark.asyncio
    async def test_all_three_slots_sync_rather_than_erroring(self):
        """The report's exact shape: "Synced 0 spools with 3 errors"."""
        fake = FakeSpoolman()
        client = _client(fake)
        tags = [
            "D144798DEF394926ACAE9D69ABA910CC",
            "1880BE1371014F4CA951BE6A30C99E44",
            "1D1F3C49046246DBBADBC3631B7F1B61",
        ]

        synced = [await client.sync_ams_tray(_tray(tag), "OJIMPO-X2D-01") for tag in tags]

        assert all(s is not None for s in synced)
        assert [s["extra"]["tag"] for s in synced] == [json.dumps(t) for t in tags]

    @pytest.mark.asyncio
    async def test_the_field_is_registered_before_the_spool_is_posted(self):
        """Ordering is the whole fix -- registering afterwards rescues nothing."""
        fake = FakeSpoolman()
        client = _client(fake)

        await client.create_spool(filament_id=7, extra={"tag": json.dumps("ABC")})

        assert fake.log.index("POST /field/spool/tag") < fake.log.index("POST /spool")


class TestItAsksSpoolmanOnlyOnce:
    @pytest.mark.asyncio
    async def test_a_second_write_does_not_ask_again(self):
        fake = FakeSpoolman()
        client = _client(fake)

        await client.create_spool(filament_id=7, extra={"tag": json.dumps("A")})
        await client.create_spool(filament_id=7, extra={"tag": json.dumps("B")})

        assert fake.field_calls("tag") == ["GET /field/spool", "POST /field/spool/tag"]

    @pytest.mark.asyncio
    async def test_an_already_registered_field_is_never_created(self):
        fake = FakeSpoolman(registered={"tag"})
        client = _client(fake)

        await client.create_spool(filament_id=7, extra={"tag": json.dumps("A")})

        assert fake.field_calls("tag") == ["GET /field/spool"]

    @pytest.mark.asyncio
    async def test_a_write_carrying_no_extra_asks_nothing(self):
        fake = FakeSpoolman()
        client = _client(fake)

        await client.create_spool(filament_id=7, remaining_weight=500.0)

        assert fake.field_calls("tag") == []

    @pytest.mark.asyncio
    async def test_concurrent_syncs_ask_exactly_once_between_them(self):
        """Two slots syncing at once must not race into a duplicate POST.

        The exact call list, rather than just the POST count: the loser of the
        race should re-read the memo once it holds the lock and find the answer
        already there, rather than repeating the round-trip the winner just
        made.
        """
        fake = FakeSpoolman()
        client = _client(fake)

        await asyncio.gather(
            client.create_spool(filament_id=7, extra={"tag": json.dumps("A")}),
            client.create_spool(filament_id=7, extra={"tag": json.dumps("B")}),
        )

        assert fake.field_calls("tag") == ["GET /field/spool", "POST /field/spool/tag"]

    @pytest.mark.asyncio
    async def test_another_client_does_not_inherit_the_answer(self):
        """The memo describes one Spoolman, so a re-pointed client starts over."""
        fake = FakeSpoolman()
        await _client(fake).create_spool(filament_id=7, extra={"tag": json.dumps("A")})

        second_fake = FakeSpoolman()
        await _client(second_fake).create_spool(filament_id=7, extra={"tag": json.dumps("B")})

        assert "GET /field/spool" in second_fake.log


class TestEveryWritePathThatCarriesExtra:
    @pytest.mark.asyncio
    async def test_update_spool(self):
        fake = FakeSpoolman()
        client = _client(fake)
        spool = await client.create_spool(filament_id=7)

        updated = await client.update_spool(spool_id=spool["id"], extra={"tag": json.dumps("A")})

        assert updated["extra"]["tag"] == json.dumps("A")

    @pytest.mark.asyncio
    async def test_update_spool_full(self):
        fake = FakeSpoolman()
        client = _client(fake)
        spool = await client.create_spool(filament_id=7)

        updated = await client.update_spool_full(spool_id=spool["id"], extra={"tag": json.dumps("A")})

        assert updated["extra"]["tag"] == json.dumps("A")

    @pytest.mark.asyncio
    async def test_merge_spool_extra(self):
        """The funnel behind linking and unlinking a tag from the inventory."""
        fake = FakeSpoolman()
        client = _client(fake)
        spool = await client.create_spool(filament_id=7)

        updated = await client.merge_spool_extra(spool["id"], {"tag": json.dumps("A")})

        assert updated["extra"]["tag"] == json.dumps("A")

    @pytest.mark.asyncio
    async def test_a_key_no_registration_list_ever_named(self):
        """``bambu_color_name`` is absent from the connect and startup lists.

        It survives today only because two call sites remember to register it
        by hand. Keying off the payload is what stops that being load-bearing.
        """
        fake = FakeSpoolman()
        client = _client(fake)
        spool = await client.create_spool(filament_id=7)

        updated = await client.merge_spool_extra(spool["id"], {"bambu_color_name": json.dumps("Jade White")})

        assert updated["extra"]["bambu_color_name"] == json.dumps("Jade White")
        assert "bambu_color_name" in fake.registered

    @pytest.mark.asyncio
    async def test_every_key_of_a_multi_key_write(self):
        fake = FakeSpoolman()
        client = _client(fake)
        spool = await client.create_spool(filament_id=7)

        await client.merge_spool_extra(
            spool["id"],
            {"bambu_slicer_filament": json.dumps("GFA00"), "bambu_color_name": json.dumps("Black")},
        )

        assert {"bambu_slicer_filament", "bambu_color_name"} <= fake.registered


class TestWhenRegistrationItselfFails:
    @pytest.mark.asyncio
    async def test_the_write_is_still_attempted(self):
        """Best-effort: a failed registration must not swallow the write.

        Spoolman still rejects the payload, exactly as it did before this
        change -- the caller's error handling is what reports that, and it is
        deliberately left untouched.
        """
        from backend.app.services.spoolman import SpoolmanClientError

        fake = FakeSpoolman(field_status=500)
        client = _client(fake)

        with pytest.raises(SpoolmanClientError):
            await client.create_spool(filament_id=7, extra={"tag": json.dumps("A")})

        assert "POST /spool" in fake.log

    @pytest.mark.asyncio
    async def test_a_later_write_tries_registering_again(self):
        """A failure is not cached -- Spoolman may simply have been restarting."""
        from backend.app.services.spoolman import SpoolmanClientError

        fake = FakeSpoolman(field_status=500)
        client = _client(fake)
        with pytest.raises(SpoolmanClientError):
            await client.create_spool(filament_id=7, extra={"tag": json.dumps("A")})

        fake.field_status = 200
        result = await client.create_spool(filament_id=7, extra={"tag": json.dumps("B")})

        assert result["extra"]["tag"] == json.dumps("B")
