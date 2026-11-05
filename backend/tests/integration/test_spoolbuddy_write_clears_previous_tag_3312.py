"""A SpoolBuddy tag write takes the tag off every other spool (#3312).

Before binding the written UID to the target spool, write-result cleared it
from the previous holder with ``{"tag": ""}``. Spoolman stores ``extra``
values as JSON and answers a bare ``""`` with HTTP 400; the handler logged a
warning and bound the tag anyway, so two spools carried it and the next scan
could resolve to the old one. The lookup it used also found only the first
holder, and on Spoolman 0.27+ asked native tags first -- so when the target
already held the tag natively, the old ``extra.tag`` holder was never looked
at.

The fake Spoolman below refuses a non-JSON ``extra`` value the way the real
one does.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.models.settings import Settings
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice
from backend.app.services.spoolman import SpoolmanClient, SpoolmanClientError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

API = "/api/v1/spoolbuddy"
UID = "04DBD727B62A81"
TARGET = 3


class FakeSpoolman:
    """In-memory Spoolman: spools by id, ``extra`` checked like the server does."""

    def __init__(self, spools, *, tag_api=False):
        self.spools = {s["id"]: s for s in spools}
        self.tag_api = tag_api
        self.writes: list[tuple[int, dict]] = []

    async def get_spools(self):
        return [dict(s) for s in self.spools.values()]

    async def get_spool(self, spool_id):
        return dict(self.spools[spool_id])

    async def has_tag_api(self):
        return self.tag_api

    async def find_spool_by_tag(self, tag_uid, cached_spools=None):
        return await SpoolmanClient.find_spool_by_tag(self, tag_uid, cached_spools=cached_spools)  # type: ignore[arg-type]

    async def link_native_tag(self, spool_id, uid):
        self.spools[spool_id].setdefault("tags", []).append({"uid": uid})
        return None

    async def unlink_native_tag(self, spool_id, uid):
        self.spools[spool_id]["tags"] = [t for t in self.spools[spool_id].get("tags", []) if t["uid"] != uid]

    async def merge_spool_extra(self, spool_id, new_fields):
        self.writes.append((spool_id, dict(new_fields)))
        for value in new_fields.values():
            try:
                json.loads(value)
            except (TypeError, ValueError):
                raise SpoolmanClientError(f"Spoolman rejected update for spool {spool_id} (HTTP 400)", 400) from None
        spool = self.spools[spool_id]
        spool["extra"] = {**(spool.get("extra") or {}), **new_fields}
        return dict(spool)

    def holders(self):
        """Ids of the spools whose extra.tag still names UID."""
        return sorted(
            sid for sid, s in self.spools.items() if (s.get("extra") or {}).get("tag", "").strip('"').upper() == UID
        )


def _spool(spool_id, *, tag=None, native=()):
    return {
        "id": spool_id,
        "filament": {"material": "PLA", "name": "PLA Basic", "color_hex": "000000", "weight": 1000.0},
        "extra": {"tag": json.dumps(tag)} if tag is not None else {},
        "tags": [{"uid": u} for u in native],
    }


async def _write(async_client: AsyncClient, db_session, fake: FakeSpoolman):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://127.0.0.1:7912"))
    db_session.add(
        SpoolBuddyDevice(
            device_id="sb-3312",
            hostname="spoolbuddy",
            ip_address="10.0.0.9",
            firmware_version="1.0.0",
            has_nfc=True,
            has_scale=True,
            tare_offset=0,
            calibration_factor=1.0,
            last_seen=datetime.now(timezone.utc),
            pending_command="write_tag",
            pending_write_payload=json.dumps({"spool_id": TARGET, "ndef_data_hex": "DEAD", "data_origin": "spoolman"}),
        )
    )
    await db_session.commit()
    with (
        patch("backend.app.api.routes.spoolbuddy.ws_manager") as ws,
        patch("backend.app.api.routes.spoolbuddy._get_spoolman_client_or_none", new=AsyncMock(return_value=fake)),
    ):
        ws.broadcast = AsyncMock()
        resp = await async_client.post(
            f"{API}/nfc/write-result",
            json={"device_id": "sb-3312", "spool_id": TARGET, "tag_uid": UID.lower(), "success": True},
        )
    assert resp.status_code == 200, resp.text
    return ws


async def test_the_reported_case_leaves_one_holder(async_client: AsyncClient, db_session):
    fake = FakeSpoolman([_spool(1, tag=UID), _spool(TARGET)])
    await _write(async_client, db_session, fake)
    assert fake.holders() == [TARGET]
    assert fake.spools[1]["extra"]["tag"] == json.dumps("")
    # The target is written once, with its new tag -- never cleared first.
    assert [w for w in fake.writes if w[0] == TARGET] == [(TARGET, {"tag": json.dumps(UID)})]


async def test_every_previous_holder_is_cleared(async_client: AsyncClient, db_session):
    fake = FakeSpoolman([_spool(1, tag=UID), _spool(2, tag=UID.lower()), _spool(TARGET)])
    await _write(async_client, db_session, fake)
    assert fake.holders() == [TARGET]


async def test_cleared_when_the_target_already_holds_the_tag_natively(async_client: AsyncClient, db_session):
    fake = FakeSpoolman([_spool(1, tag=UID), _spool(TARGET, native=(UID,))], tag_api=True)
    await _write(async_client, db_session, fake)
    assert fake.holders() == [TARGET]


async def test_other_spools_and_other_extra_fields_are_left_alone(async_client: AsyncClient, db_session):
    other = _spool(2, tag="AABBCCDD")
    other["extra"]["note"] = json.dumps("keep")
    first = _spool(1, tag=UID)
    first["extra"]["note"] = json.dumps("mine")
    fake = FakeSpoolman([first, other, _spool(TARGET)])
    await _write(async_client, db_session, fake)
    assert fake.spools[2]["extra"] == {"tag": json.dumps("AABBCCDD"), "note": json.dumps("keep")}
    assert fake.spools[1]["extra"]["note"] == json.dumps("mine")


async def test_rewriting_the_targets_own_tag_does_not_clear_it_first(async_client: AsyncClient, db_session):
    fake = FakeSpoolman([_spool(1, tag=UID), _spool(TARGET, tag=UID)])
    await _write(async_client, db_session, fake)
    assert fake.holders() == [TARGET]
    assert [w for w in fake.writes if w[0] == TARGET] == [(TARGET, {"tag": json.dumps(UID)})]


async def test_pages_open_elsewhere_are_told_the_tag_moved(async_client: AsyncClient, db_session):
    """Their tag -> spool map still has the previous holder (#3402)."""
    fake = FakeSpoolman([_spool(1, tag=UID), _spool(TARGET)])
    ws = await _write(async_client, db_session, fake)
    assert {"type": "inventory_changed"} in [c.args[0] for c in ws.broadcast.await_args_list]
