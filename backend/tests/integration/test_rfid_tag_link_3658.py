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
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Matte",
                    "tray_color": "333333FF",
                    "tray_info_idx": "GFA01",
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
        "material": "PLA",
        "subtype": "Matte",
        "rgba": "333333FF",
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
        opened_id = (await _spool(db_session, weight_used=664, note="On the shelf")).id

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
        opened = await _spool(db_session, weight_used=664)

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
        target = await _spool(db_session, weight_used=664)

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
        right = await _spool(db_session, weight_used=664)
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
        assert new.weight_used == 664
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
            json={"tag_uid": TAG_UID, "tray_uuid": TRAY_UUID, "move": True},
        )

        assert resp.status_code == 200, resp.text
        old = await _reload(db_session, holder.id)
        assert (old.tag_uid, old.tray_uuid) == (None, None)
