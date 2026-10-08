"""Bulk reads (/api/v1/bulk/...) answer exactly what their single-id routes do.

The Printers page asked for each printer's status, slot presets, AMS labels,
plugs, sensor readings, firmware and queue once per printer, and the Archives
page for each card's folders once per archive; the frontend now batches those
calls into these routes. Each test compares a bulk answer with the single
route's answer for the same ids, and pins what a bulk answer leaves out: ids
the caller may not see, which the client then asks the single route for.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

BULK = "/api/v1/bulk"


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "bulkadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "bulkadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _user(async_client, admin_jwt, username, permissions, printer_ids=None) -> tuple[str, int]:
    groups = []
    perms = await async_client.post(
        "/api/v1/groups/", headers=_auth(admin_jwt), json={"name": f"perms_{username}", "permissions": permissions}
    )
    assert perms.status_code == 201, perms.text
    groups.append(perms.json()["id"])
    if printer_ids is not None:
        team = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin_jwt),
            json={"name": f"team_{username}", "permissions": [], "restrict_printers": True, "printer_ids": printer_ids},
        )
        assert team.status_code == 201, team.text
        groups.append(team.json()["id"])
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin_jwt),
        json={"username": username, "password": "UserPass1!", "group_ids": groups},
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"], created.json()["id"]


async def _get(async_client: AsyncClient, url: str, **kwargs):
    response = await async_client.get(url, **kwargs)
    assert response.status_code == 200, f"{url}: {response.text}"
    return response.json()


def _live_state(**overrides) -> MagicMock:
    """A connected printer's state, enough for the status route to build its answer."""
    state = MagicMock(
        connected=True,
        state="IDLE",
        current_print=None,
        subtask_name=None,
        gcode_file=None,
        progress=0,
        remaining_time=None,
        layer_num=0,
        total_layers=0,
        temperatures={"nozzle": 25.0, "bed": 25.0},
        raw_data={"ams": [{"id": 0, "sn": "AMS-SN-1", "tray": []}]},
        hms_errors=[],
        firmware_version="01.08.00.00",
        developer_mode=True,
        ams_filament_backup=None,
        fila_switch=None,
        subtask_id=None,
        kprofiles=[],
        nozzles=[],
        ams_switch_inlet={},
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


class TestPrinterStatuses:
    async def test_matches_the_single_route(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")

        bulk = await _get(async_client, f"{BULK}/printer-statuses", params={"ids": f"{b.id},{a.id}"})

        # In the order asked for, each the single route's answer
        assert [row["id"] for row in bulk] == [b.id, a.id]
        for row in bulk:
            assert row == await _get(async_client, f"/api/v1/printers/{row['id']}/status")

    async def test_matches_the_single_route_for_a_printing_printer(
        self, async_client, printer_factory, archive_factory
    ):
        # The full status path: live state, the cover, and the active print's archive
        from backend.app.services.bambu_mqtt import PrinterState

        printing, idle = await printer_factory(name="Busy"), await printer_factory(name="Idle")
        archive = await archive_factory(printing.id, subtask_id="sub-42")
        states = {}
        for printer, run_state in ((printing, "RUNNING"), (idle, "IDLE")):
            state = PrinterState()
            state.connected = True
            state.state = run_state
            states[printer.id] = state
        states[printing.id].gcode_file = "/data/Metadata/plate_1.gcode"
        states[printing.id].subtask_id = "sub-42"
        states[printing.id].progress = 40

        with patch("backend.app.api.routes.printers.printer_manager") as manager:
            manager.get_status = MagicMock(side_effect=lambda pid: states.get(pid))
            manager.is_awaiting_plate_clear = MagicMock(return_value=False)
            bulk = await _get(async_client, f"{BULK}/printer-statuses", params={"ids": f"{printing.id},{idle.id}"})
            singles = [await _get(async_client, f"/api/v1/printers/{pid}/status") for pid in (printing.id, idle.id)]

        assert bulk == singles
        assert bulk[0]["current_archive_id"] == archive.id
        assert bulk[0]["cover_url"] == f"/api/v1/printers/{printing.id}/cover"
        assert bulk[1]["connected"] is True

    async def test_unknown_printer_is_left_out(self, async_client, printer_factory):
        printer = await printer_factory()

        bulk = await _get(async_client, f"{BULK}/printer-statuses", params={"ids": f"{printer.id},99999"})

        assert [row["id"] for row in bulk] == [printer.id]

    async def test_printer_outside_the_scope_is_left_out(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "member", ["printers:read"], printer_ids=[a.id])

        bulk = await _get(
            async_client, f"{BULK}/printer-statuses", params={"ids": f"{a.id},{b.id}"}, headers=_auth(jwt)
        )

        assert [row["id"] for row in bulk] == [a.id]

    async def test_needs_printers_read(self, async_client, printer_factory):
        printer = await printer_factory()
        admin = await _admin_token(async_client)
        jwt, _ = await _user(async_client, admin, "nobody", ["queue:read_all"])

        response = await async_client.get(
            f"{BULK}/printer-statuses", params={"ids": str(printer.id)}, headers=_auth(jwt)
        )

        assert response.status_code == 403

    @pytest.mark.parametrize("ids", ["abc", "1,x", "-1", "0", ",".join(str(i) for i in range(1, 502))])
    async def test_rejects_bad_id_lists(self, async_client, ids):
        response = await async_client.get(f"{BULK}/printer-statuses", params={"ids": ids})

        assert response.status_code == 422


class TestSlotPresetsAndAmsLabels:
    async def test_slot_presets_match_the_single_route(self, async_client, db_session, printer_factory):
        from backend.app.models.slot_preset import SlotPresetMapping

        a, b = await printer_factory(), await printer_factory()
        db_session.add_all(
            [
                SlotPresetMapping(printer_id=a.id, ams_id=0, tray_id=1, preset_id="GFA00", preset_name="PLA"),
                SlotPresetMapping(printer_id=a.id, ams_id=255, tray_id=0, preset_id="GFB00", preset_name="ABS"),
            ]
        )
        await db_session.commit()

        bulk = await _get(async_client, f"{BULK}/slot-presets", params={"ids": f"{a.id},{b.id}"})

        assert set(bulk) == {str(a.id), str(b.id)}
        for printer_id in (a.id, b.id):
            assert bulk[str(printer_id)] == await _get(async_client, f"/api/v1/printers/{printer_id}/slot-presets")
        assert bulk[str(b.id)] == {}

    async def test_ams_labels_match_the_single_route(self, async_client, db_session, printer_factory):
        from backend.app.models.ams_label import AmsLabel

        a, b = await printer_factory(), await printer_factory()
        db_session.add(AmsLabel(ams_serial_number="AMS-SN-1", label="Left rack"))
        await db_session.commit()

        with patch("backend.app.api.routes.printers.printer_manager") as manager:
            manager.get_status = MagicMock(
                side_effect=lambda pid: _live_state() if pid == a.id else None,
            )
            bulk = await _get(async_client, f"{BULK}/ams-labels", params={"ids": f"{a.id},{b.id}"})
            singles = {pid: await _get(async_client, f"/api/v1/printers/{pid}/ams-labels") for pid in (a.id, b.id)}

        assert bulk == {str(pid): answer for pid, answer in singles.items()}
        assert bulk[str(a.id)] == {"0": "Left rack"}


class TestCardPlugs:
    async def test_matches_the_two_single_routes(self, async_client, printer_factory, smart_plug_factory):
        a, b = await printer_factory(), await printer_factory()
        await smart_plug_factory(name="Outlet", printer_id=a.id, ip_address="10.0.0.5")
        await smart_plug_factory(
            name="Lights",
            plug_type="homeassistant",
            ha_entity_id="script.lights",
            printer_id=a.id,
            show_on_printer_card=True,
        )

        bulk = await _get(async_client, f"{BULK}/card-plugs", params={"ids": f"{a.id},{b.id}"})

        for printer_id in (a.id, b.id):
            assert bulk[str(printer_id)]["plug"] == await _get(
                async_client, f"/api/v1/smart-plugs/by-printer/{printer_id}"
            )
            assert bulk[str(printer_id)]["scripts"] == await _get(
                async_client, f"/api/v1/smart-plugs/by-printer/{printer_id}/scripts"
            )
        assert bulk[str(a.id)]["plug"]["name"] == "Outlet"
        assert [p["name"] for p in bulk[str(a.id)]["scripts"]] == ["Lights"]
        assert bulk[str(b.id)] == {"plug": None, "scripts": []}


class TestHaSensorReadings:
    async def test_matches_the_single_route(self, async_client, db_session, printer_factory):
        from backend.app.models.printer_ha_sensor import PrinterHASensor

        a, b = await printer_factory(), await printer_factory()
        db_session.add_all(
            [
                PrinterHASensor(printer_id=a.id, name="Door", entity_id="binary_sensor.door", sort_order=2),
                PrinterHASensor(printer_id=a.id, name="Smoke", entity_id="binary_sensor.smoke", sort_order=1),
                PrinterHASensor(
                    printer_id=a.id, name="Hidden", entity_id="binary_sensor.hidden", show_on_printer_card=False
                ),
            ]
        )
        await db_session.commit()

        bulk = await _get(async_client, f"{BULK}/ha-sensor-readings", params={"ids": f"{a.id},{b.id}"})

        for printer_id in (a.id, b.id):
            assert bulk[str(printer_id)] == await _get(
                async_client, f"/api/v1/ha-sensors/by-printer/{printer_id}/readings"
            )
        assert [r["name"] for r in bulk[str(a.id)]] == ["Smoke", "Door"]
        assert bulk[str(b.id)] == []


class TestFirmwareUpdates:
    async def test_matches_the_single_route(self, async_client, printer_factory):
        a, b = await printer_factory(model="X1C"), await printer_factory(model="P1S")
        service = MagicMock()
        service.check_for_update = AsyncMock(
            return_value={
                "update_available": True,
                "latest_version": "01.09.00.00",
                "download_url": None,
                "release_notes": None,
                "available_versions": [],
            }
        )

        with (
            patch("backend.app.api.routes.bulk.get_firmware_service", return_value=service),
            patch("backend.app.api.routes.firmware.get_firmware_service", return_value=service),
        ):
            bulk = await _get(async_client, f"{BULK}/firmware-updates", params={"ids": f"{a.id},{b.id},99999"})
            singles = [await _get(async_client, f"/api/v1/firmware/updates/{pid}") for pid in (a.id, b.id)]

        assert bulk == singles

    async def test_switched_off_asks_nobody(self, async_client, db_session, printer_factory):
        from backend.app.models.settings import Settings

        a, b = await printer_factory(), await printer_factory()
        db_session.add(Settings(key="check_printer_firmware", value="false"))
        await db_session.commit()
        service = MagicMock()
        service.check_for_update = AsyncMock()

        with patch("backend.app.api.routes.bulk.get_firmware_service", return_value=service):
            bulk = await _get(async_client, f"{BULK}/firmware-updates", params={"ids": f"{a.id},{b.id}"})

        assert [row["update_available"] for row in bulk] == [False, False]
        service.check_for_update.assert_not_called()


class TestPrinterQueues:
    async def test_matches_the_single_route_including_model_jobs(
        self, async_client, db_session, printer_factory, archive_factory
    ):
        from backend.app.models.print_queue import PrintQueueItem

        x1_a = await printer_factory(model="X1C")
        x1_b = await printer_factory(model="X1C")
        p1 = await printer_factory(model="P1S")
        archive = await archive_factory(x1_a.id)
        db_session.add_all(
            [
                PrintQueueItem(archive_id=archive.id, printer_id=x1_a.id, status="pending", position=3),
                PrintQueueItem(
                    archive_id=archive.id, printer_id=None, target_model="x1c", status="pending", position=1
                ),
                PrintQueueItem(archive_id=archive.id, printer_id=p1.id, status="pending", position=2),
                PrintQueueItem(archive_id=archive.id, printer_id=x1_a.id, status="printing", position=4),
                PrintQueueItem(archive_id=archive.id, printer_id=x1_b.id, status="completed", position=5),
            ]
        )
        await db_session.commit()
        ids = [x1_a.id, x1_b.id, p1.id]

        for status in ("pending", "printing"):
            bulk = await _get(
                async_client,
                f"{BULK}/printer-queues",
                params={"ids": ",".join(map(str, ids)), "status": status},
            )
            for printer_id in ids:
                single = await _get(async_client, "/api/v1/queue/", params={"printer_id": printer_id, "status": status})
                assert bulk[str(printer_id)] == single, (status, printer_id)

        pending = await _get(
            async_client, f"{BULK}/printer-queues", params={"ids": ",".join(map(str, ids)), "status": "pending"}
        )
        # The "Any X1C" job sits under both X1Cs, ahead of the pinned one
        assert [i["printer_id"] for i in pending[str(x1_a.id)]] == [None, x1_a.id]
        assert [i["printer_id"] for i in pending[str(x1_b.id)]] == [None]
        assert [i["printer_id"] for i in pending[str(p1.id)]] == [p1.id]

    async def test_read_own_sees_only_own_jobs(self, async_client, db_session, printer_factory, archive_factory):
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        admin = await _admin_token(async_client)
        jwt, user_id = await _user(async_client, admin, "owner", ["queue:read_own"])
        db_session.add_all(
            [
                PrintQueueItem(archive_id=archive.id, printer_id=printer.id, status="pending", created_by_id=user_id),
                PrintQueueItem(archive_id=archive.id, printer_id=printer.id, status="pending"),
            ]
        )
        await db_session.commit()

        bulk = await _get(
            async_client,
            f"{BULK}/printer-queues",
            params={"ids": str(printer.id), "status": "pending"},
            headers=_auth(jwt),
        )

        assert [i["created_by_id"] for i in bulk[str(printer.id)]] == [user_id]


class TestArchiveFolders:
    async def test_matches_the_single_route(self, async_client, db_session, printer_factory, archive_factory):
        from backend.app.models.library import LibraryFolder

        printer = await printer_factory()
        linked, unlinked = await archive_factory(printer.id), await archive_factory(printer.id)
        db_session.add_all(
            [
                LibraryFolder(name="Zeta", archive_id=linked.id),
                LibraryFolder(name="Alpha", archive_id=linked.id),
                LibraryFolder(name="Elsewhere"),
            ]
        )
        await db_session.commit()

        bulk = await _get(async_client, f"{BULK}/archive-folders", params={"ids": f"{linked.id},{unlinked.id}"})

        for archive_id in (linked.id, unlinked.id):
            assert bulk[str(archive_id)] == await _get(async_client, f"/api/v1/library/folders/by-archive/{archive_id}")
        assert [f["name"] for f in bulk[str(linked.id)]] == ["Alpha", "Zeta"]
        assert bulk[str(unlinked.id)] == []
