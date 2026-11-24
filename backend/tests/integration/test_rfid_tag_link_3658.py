"""Choosing and moving the spool an AMS RFID tag belongs to, in built-in inventory (#3658).

Three pieces:

- the AMS handler asks which spool a tag is when the matching untagged spools
  differ, instead of linking the oldest, whatever the auto-add setting says;
- ``PATCH /inventory/spools/{id}/link-tag`` with the slot assigns the spool
  there the way an RFID match does (``auto_assign_spool``), so the swap
  history is written during a print;
- with ``move`` it takes the tag from the spool that carries it instead of
  refusing with 409. That spool keeps the usage it was charged.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment

TAG_UID = "AABBCCDD11223344"
OTHER_SIDE_UID = "5566778899AABBCC"
TRAY_UUID = "AABBCCDDEEFF0011AABBCCDDEEFF0011"


def _ams_data(tray_uuid: str = TRAY_UUID, tag_uid: str = TAG_UID) -> list[dict]:
    return [
        {
            "id": 0,
            "tray": [
                {
                    "id": 0,
                    "tray_type": "PETG",
                    "tray_sub_brands": "PETG HF",
                    "tray_color": "179CB0FF",
                    "tray_info_idx": "GFG00",
                    "tray_uuid": tray_uuid,
                    "tag_uid": tag_uid,
                    "remain": 40,
                }
            ],
        }
    ]


def _printer_manager(pm: MagicMock) -> MagicMock:
    pm.get_status.return_value = SimpleNamespace(raw_data={"ams": {"ams": _ams_data()}}, state="IDLE", connected=True)
    pm.get_client.return_value = MagicMock()
    pm.get_model.return_value = "X1C"
    pm.get_printer.return_value = MagicMock(name="X1C", serial_number="RFID3658")
    return pm


async def _spool(db_session, **kwargs) -> Spool:
    defaults = {
        "material": "PETG",
        "subtype": "HF",
        "rgba": "179CB0FF",
        "brand": "Bambu Lab",
        "label_weight": 1000,
        "core_weight": 250,
        "weight_used": 0,
    }
    defaults.update(kwargs)
    spool = Spool(**defaults)
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)
    return spool


async def _slot_rows(db_session) -> list[tuple[int, int, int, int]]:
    rows = await db_session.execute(
        select(SpoolAssignment.spool_id, SpoolAssignment.printer_id, SpoolAssignment.ams_id, SpoolAssignment.tray_id)
    )
    return [tuple(r) for r in rows.all()]


async def _reload(db_session, spool_id: int) -> Spool:
    result = await db_session.execute(
        select(Spool).where(Spool.id == spool_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _all_spools(db_session) -> list[Spool]:
    result = await db_session.execute(select(Spool).execution_options(populate_existing=True))
    return list(result.scalars().all())


class TestAmsHandlerAsks:
    async def _run(self, test_engine, printer_id: int, auto_add: str | None):
        from backend.app.main import on_ams_change

        real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async def _setting(_db, key):
            return auto_add if key == "auto_add_unknown_rfid" else None

        with (
            patch("backend.app.main.async_session", real_db),
            patch("backend.app.core.database.async_session", real_db),
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main.mqtt_relay") as relay,
            patch("backend.app.main.ws_manager") as ws,
            patch("backend.app.main._broadcast_unknown_tag", new_callable=AsyncMock) as prompt,
            patch("backend.app.api.routes.settings.get_setting", side_effect=_setting),
        ):
            _printer_manager(pm)
            relay.on_ams_change = AsyncMock()
            ws.send_printer_status = AsyncMock()
            ws.broadcast = AsyncMock()
            await on_ams_change(printer_id, _ams_data())
        return prompt

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("auto_add", ["true", "false", None])
    async def test_spools_that_differ_are_offered_not_guessed(self, test_engine, db_session, printer_factory, auto_add):
        printer_id = (await printer_factory()).id
        sealed_id = (await _spool(db_session)).id
        opened_id = (await _spool(db_session, weight_used=175, note="On the shelf")).id

        prompt = await self._run(test_engine, printer_id, auto_add)

        assert await _slot_rows(db_session) == []
        spools = await _all_spools(db_session)
        # Nothing linked, and auto-add made no third spool.
        assert len(spools) == 2
        assert all(s.tag_uid is None and s.tray_uuid is None for s in spools)
        prompt.assert_awaited_once()
        kwargs = prompt.await_args.kwargs
        assert (kwargs["printer_id"], kwargs["ams_id"], kwargs["tray_id"]) == (printer_id, 0, 0)
        assert kwargs["candidate_spool_ids"] == [sealed_id, opened_id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_identical_sealed_spools_are_still_linked(self, test_engine, db_session, printer_factory):
        printer_id = (await printer_factory()).id
        first_id = (await _spool(db_session)).id
        await _spool(db_session)

        prompt = await self._run(test_engine, printer_id, "true")

        prompt.assert_not_awaited()
        assert await _slot_rows(db_session) == [(first_id, printer_id, 0, 0)]
        linked = await _reload(db_session, first_id)
        assert linked.tray_uuid == TRAY_UUID
        assert linked.data_origin == "rfid_linked"


class TestLinkTagWithSlot:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_link_assigns_the_slot_through_the_rfid_path(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        printer = await printer_factory()
        opened = await _spool(db_session, weight_used=175)

        with (
            patch("backend.app.services.printer_manager.printer_manager") as pm,
            patch(
                "backend.app.services.spool_tag_matcher.spool_swaps.record_assignment", new_callable=AsyncMock
            ) as record,
            patch("backend.app.services.spool_relocation.release_other_slots", new_callable=AsyncMock) as release,
            patch("backend.app.api.routes.inventory.ws_manager") as ws,
        ):
            _printer_manager(pm)
            ws.broadcast = AsyncMock()
            resp = await async_client.patch(
                f"/api/v1/inventory/spools/{opened.id}/link-tag",
                json={
                    "tag_uid": TAG_UID,
                    "tray_uuid": TRAY_UUID,
                    "tag_type": "bambulab",
                    "data_origin": "rfid_linked",
                    "printer_id": printer.id,
                    "ams_id": 0,
                    "tray_id": 0,
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["tray_uuid"], body["tag_type"], body["data_origin"]) == (TRAY_UUID, "bambulab", "rfid_linked")
        assert await _slot_rows(db_session) == [(opened.id, printer.id, 0, 0)]
        # The swap history hook runs, so a print that is running is charged to this spool from now on.
        record.assert_awaited_once()
        assert record.await_args.args[1:5] == (printer.id, 0, 0, opened.id)
        release.assert_awaited_once()
        assert release.await_args.args[1:] == (opened.id, (printer.id, 0, 0))
        # The RFID path sends no filament setting, which would drop the slot's RFID state.
        pm.get_client.return_value.ams_set_filament_setting.assert_not_called()
        ws.broadcast.assert_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_part_of_a_slot_is_refused(self, async_client: AsyncClient, db_session):
        spool = await _spool(db_session)

        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{spool.id}/link-tag", json={"tag_uid": TAG_UID, "printer_id": 1}
        )

        assert resp.status_code == 422


class TestMoveTag:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_without_move_a_held_tag_is_still_refused(self, async_client: AsyncClient, db_session):
        holder = await _spool(db_session, tray_uuid=TRAY_UUID, tag_uid=TAG_UID, data_origin="rfid_linked")
        target = await _spool(db_session, weight_used=175)

        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tag_uid": TAG_UID, "tray_uuid": TRAY_UUID}
        )

        assert resp.status_code == 409
        assert resp.json()["detail"]["spool_id"] == holder.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_takes_the_tag_and_the_slot_and_leaves_the_usage(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        printer = await printer_factory()
        # The sealed spool the matcher picked, charged 12 g before the mistake was seen.
        wrong = await _spool(
            db_session,
            tray_uuid=TRAY_UUID,
            tag_uid=TAG_UID,
            tag_type="bambulab",
            data_origin="rfid_linked",
            weight_used=12,
        )
        right = await _spool(db_session, weight_used=175)
        db_session.add(SpoolAssignment(spool_id=wrong.id, printer_id=printer.id, ams_id=0, tray_id=0))
        await db_session.commit()

        with (
            patch("backend.app.services.printer_manager.printer_manager") as pm,
            patch(
                "backend.app.services.spool_tag_matcher.spool_swaps.record_assignment", new_callable=AsyncMock
            ) as record,
            patch("backend.app.services.spool_relocation.release_other_slots", new_callable=AsyncMock),
            patch("backend.app.api.routes.inventory.ws_manager") as ws,
        ):
            _printer_manager(pm)
            ws.broadcast = AsyncMock()
            resp = await async_client.patch(
                f"/api/v1/inventory/spools/{right.id}/link-tag",
                json={
                    "tag_uid": TAG_UID,
                    "tray_uuid": TRAY_UUID,
                    "tag_type": "bambulab",
                    "data_origin": "rfid_linked",
                    "move": True,
                    "source_spool_id": wrong.id,
                    "printer_id": printer.id,
                    "ams_id": 0,
                    "tray_id": 0,
                },
            )

        assert resp.status_code == 200, resp.text
        old = await _reload(db_session, wrong.id)
        new = await _reload(db_session, right.id)
        assert (old.tag_uid, old.tray_uuid, old.tag_type, old.data_origin) == (None, None, None, None)
        assert old.weight_used == 12
        assert (new.tag_uid, new.tray_uuid, new.tag_type, new.data_origin) == (
            TAG_UID,
            TRAY_UUID,
            "bambulab",
            "rfid_linked",
        )
        assert new.weight_used == 175
        assert await _slot_rows(db_session) == [(right.id, printer.id, 0, 0)]
        assert record.await_args.args[1:5] == (printer.id, 0, 0, right.id)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_clears_a_holder_found_by_the_other_half_of_the_tag(self, async_client: AsyncClient, db_session):
        """A Bambu spool has a tag on each side: one tray UUID, two tag UIDs."""
        holder = await _spool(db_session, tray_uuid=TRAY_UUID, tag_uid=OTHER_SIDE_UID, data_origin="rfid_linked")
        target = await _spool(db_session)

        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{target.id}/link-tag",
            json={"tag_uid": TAG_UID, "tray_uuid": TRAY_UUID, "move": True, "source_spool_id": holder.id},
        )

        assert resp.status_code == 200, resp.text
        old = await _reload(db_session, holder.id)
        assert (old.tag_uid, old.tray_uuid) == (None, None)


@pytest.mark.asyncio
async def test_swap_log(async_client, db_session, printer_factory):
    from datetime import datetime

    from backend.app.models.active_print_session import ActivePrintSession
    from backend.app.services import spool_swaps

    printer = await printer_factory()
    old = await _spool(db_session, tag_uid=TAG_UID, tray_uuid=TRAY_UUID)
    target = await _spool(db_session)
    start = [spool_swaps.entry(spool_swaps.INVENTORY, 0, 0, old.id, 0, start=True)]
    db_session.add(ActivePrintSession(printer_id=printer.id, started_at=datetime.now(), spool_change_log=start))
    db_session.add(SpoolAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spool_id=old.id))
    await db_session.commit()
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        _printer_manager(pm)
        live = pm.get_status.return_value
        live.state = "RUNNING"
        live.layer_num = 57
        live.spool_change_log = list(start)
        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{target.id}/link-tag",
            json={
                "tag_uid": TAG_UID,
                "tray_uuid": TRAY_UUID,
                "move": True,
                "source_spool_id": old.id,
                "printer_id": printer.id,
                "ams_id": 0,
                "tray_id": 0,
            },
        )
    assert resp.status_code == 200, resp.text
    row = await db_session.get(ActivePrintSession, printer.id)
    await db_session.refresh(row)
    assert row.spool_change_log == live.spool_change_log
    assert spool_swaps.segments(row.spool_change_log, spool_swaps.INVENTORY, 0, 0) == [(old.id, 0), (target.id, 57)]


@pytest.mark.asyncio
async def test_stale_choice_cannot_replace_a_tag_or_duplicate_a_spool(async_client, db_session, printer_factory):
    printer = await printer_factory()
    target = await _spool(db_session)
    slot = {"printer_id": printer.id, "ams_id": 0, "tray_id": 0}
    route = f"/api/v1/inventory/spools/{target.id}/link-tag"
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        _printer_manager(pm)
        for action, url in [(async_client.patch, route), (async_client.post, "/api/v1/inventory/spools/from-slot")]:
            response = await action(url, json={**slot, "tray_uuid": "E1" * 16})
            assert response.status_code == 409
        linked = await async_client.patch(route, json={**slot, "tray_uuid": TRAY_UUID})
        assert linked.status_code == 200
        added = await async_client.post("/api/v1/inventory/spools/from-slot", json={**slot, "tray_uuid": TRAY_UUID})
        assert added.status_code == 409
    assert len(await _all_spools(db_session)) == 1


@pytest.mark.asyncio
async def test_shelf_move_requires_current_owner_and_preserves_entire_tag(async_client, db_session):
    uid = "04112233445566778899"
    source = await _spool(db_session, tag_uid=uid, tray_uuid=TRAY_UUID)
    target = await _spool(db_session)
    other = await _spool(db_session, tray_uuid="E1" * 16)
    payload = {"source_spool_id": source.id, "move": True, "tag_uid": uid}

    async def move(spool):
        return await async_client.patch(f"/api/v1/inventory/spools/{spool.id}/link-tag", json=payload)

    assert (await move(other)).status_code == 409
    moved = await move(target)
    assert moved.status_code == 200
    assert (moved.json()["tag_uid"], moved.json()["tray_uuid"]) == (uid, TRAY_UUID)
    assert (await move(other)).status_code == 409
    assert (await _reload(db_session, other.id)).tray_uuid == "E1" * 16


@pytest.mark.asyncio
@pytest.mark.parametrize(("ams_id", "tray_id", "raw"), [(128, 0, "ams"), (255, 0, "vt_tray")])
async def test_slot_link_uses_live_preset_and_fingerprint(
    async_client, db_session, printer_factory, ams_id, tray_id, raw
):
    printer = await printer_factory()
    target = await _spool(db_session)
    tray = {
        "id": 254 if raw == "vt_tray" else 0,
        "tray_type": "PETG",
        "tray_color": "17A0BBFF",
        "tag_uid": TAG_UID,
        "tray_info_idx": "GFG00",
    }
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        _printer_manager(pm)
        pm.get_status.return_value.raw_data = {raw: tray if raw == "vt_tray" else [{"id": ams_id, "tray": [tray]}]}
        result = await async_client.patch(
            f"/api/v1/inventory/spools/{target.id}/link-tag",
            json={"tag_uid": TAG_UID, "printer_id": printer.id, "ams_id": ams_id, "tray_id": tray_id},
        )
    assert result.status_code == 200
    assert result.json()["tag_type"] == "bambulab"
    assignment = (await db_session.execute(select(SpoolAssignment))).scalar_one()
    assert (assignment.fingerprint_type, assignment.fingerprint_color) == ("PETG", "17A0BBFF")


@pytest.mark.asyncio
async def test_concurrent_moves_have_one_winner(test_engine, db_session):
    import asyncio

    from fastapi import HTTPException

    from backend.app.api.routes.inventory import LinkTagRequest, link_tag_to_spool
    from backend.app.core.printer_scope import PrinterScope

    source = await _spool(db_session, tag_uid=TAG_UID)
    targets = [await _spool(db_session), await _spool(db_session)]
    request = LinkTagRequest(tag_uid=TAG_UID, move=True, source_spool_id=source.id)
    gate = asyncio.Event()

    async def move(target):
        async with AsyncSession(test_engine, expire_on_commit=False) as db:
            await gate.wait()
            try:
                await link_tag_to_spool(target.id, request, db, None, PrinterScope())
            except HTTPException as exc:
                return exc.status_code
            return 200

    tasks = [asyncio.create_task(move(target)) for target in targets]
    gate.set()
    assert sorted(await asyncio.gather(*tasks)) == [200, 409]
    holders = (await db_session.scalars(select(Spool.id).where(Spool.tag_uid == TAG_UID))).all()
    assert len(holders) == 1 and holders[0] in {target.id for target in targets}


@pytest.mark.asyncio
async def test_validation_checks_spool_before_partial_slot(async_client, db_session):
    from datetime import datetime, timezone

    archived = await _spool(db_session, archived_at=datetime.now(timezone.utc))
    for spool_id, expected_status in [(archived.id, 400), (archived.id + 100, 404)]:
        result = await async_client.patch(f"/api/v1/inventory/spools/{spool_id}/link-tag", json={"ams_id": 0})
        assert result.status_code == expected_status


@pytest.mark.asyncio
async def test_moving_a_uid_with_a_zero_uuid_preserves_other_spools(async_client, db_session):
    source = await _spool(
        db_session, tag_uid="AA:BB:CC:DD:11:22:33:44", tray_uuid="0" * 32, tag_type="ntag", data_origin="nfc_link"
    )
    duplicate = await _spool(db_session, tag_uid="aa-bb-cc-dd-11-22-33-44")
    unrelated = await _spool(db_session, tag_uid=OTHER_SIDE_UID, tray_uuid="0" * 32)
    target = await _spool(db_session)
    response = await async_client.patch(
        f"/api/v1/inventory/spools/{target.id}/link-tag",
        json={"source_spool_id": source.id, "move": True, "tag_uid": TAG_UID, "tray_uuid": "0" * 32},
    )
    assert response.status_code == 200
    moved = response.json()
    assert (moved["tag_uid"], moved["tray_uuid"], moved["tag_type"], moved["data_origin"]) == (
        TAG_UID,
        None,
        "ntag",
        "nfc_link",
    )
    assert (await _reload(db_session, source.id)).tag_uid is None
    assert (await _reload(db_session, duplicate.id)).tag_uid is None
    assert (await _reload(db_session, unrelated.id)).tag_uid == OTHER_SIDE_UID


class TestSlotSafeguards:
    """What a link or move must refuse, so a stale dialog or a limited caller
    cannot put a tag or a slot on the wrong spool."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_slot_on_a_printer_outside_the_scope_is_not_found(self, db_session, printer_factory):
        from fastapi import HTTPException

        from backend.app.api.routes.inventory import LinkTagRequest, link_tag_to_spool
        from backend.app.core.printer_scope import PrinterScope

        printer, other = await printer_factory(), await printer_factory()
        target = await _spool(db_session)
        request = LinkTagRequest(tag_uid=TAG_UID, tray_uuid=TRAY_UUID, printer_id=printer.id, ams_id=0, tray_id=0)

        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            _printer_manager(pm)
            with pytest.raises(HTTPException) as exc:
                await link_tag_to_spool(target.id, request, db_session, None, PrinterScope(frozenset({other.id})))

        assert exc.value.status_code == 404
        assert await _slot_rows(db_session) == []
        assert (await _reload(db_session, target.id)).tray_uuid is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_slot_assigned_to_another_spool_since_is_refused(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        printer = await printer_factory()
        occupant = await _spool(db_session)
        target = await _spool(db_session, weight_used=175)
        db_session.add(SpoolAssignment(spool_id=occupant.id, printer_id=printer.id, ams_id=0, tray_id=0))
        await db_session.commit()

        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            _printer_manager(pm)
            resp = await async_client.patch(
                f"/api/v1/inventory/spools/{target.id}/link-tag",
                json={"tag_uid": TAG_UID, "tray_uuid": TRAY_UUID, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert resp.status_code == 409
        assert await _slot_rows(db_session) == [(occupant.id, printer.id, 0, 0)]
        assert (await _reload(db_session, target.id)).tray_uuid is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_shelf_move_from_a_spool_in_a_slot_is_refused(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        printer = await printer_factory()
        source = await _spool(db_session, tag_uid=TAG_UID, tray_uuid=TRAY_UUID, data_origin="rfid_linked")
        target = await _spool(db_session)
        db_session.add(SpoolAssignment(spool_id=source.id, printer_id=printer.id, ams_id=0, tray_id=0))
        await db_session.commit()

        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{target.id}/link-tag",
            json={"tag_uid": TAG_UID, "tray_uuid": TRAY_UUID, "move": True, "source_spool_id": source.id},
        )

        assert resp.status_code == 409
        kept = await _reload(db_session, source.id)
        assert (kept.tag_uid, kept.tray_uuid) == (TAG_UID, TRAY_UUID)
        assert (await _reload(db_session, target.id)).tray_uuid is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_from_slot_refuses_a_tag_a_shelf_spool_carries(
        self, async_client: AsyncClient, db_session, printer_factory
    ):
        printer = await printer_factory()
        holder = await _spool(db_session, tag_uid=TAG_UID, tray_uuid=TRAY_UUID, data_origin="rfid_linked")

        with patch("backend.app.services.printer_manager.printer_manager") as pm:
            _printer_manager(pm)
            resp = await async_client.post(
                "/api/v1/inventory/spools/from-slot", json={"printer_id": printer.id, "ams_id": 0, "tray_id": 0}
            )

        assert resp.status_code == 409
        assert resp.json()["detail"]["spool_id"] == holder.id
        assert [s.id for s in await _all_spools(db_session)] == [holder.id]
        assert await _slot_rows(db_session) == []
