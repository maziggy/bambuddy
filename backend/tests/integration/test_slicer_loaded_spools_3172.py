"""GET /slicer/loaded-spools — what the Slice dialog may filter on (#3172).

The dialog offers to narrow its lists to the printers that are online and the
spools loaded in them. The endpoint behind it has to:

* list only printers with a live connection, since an offline printer can't
  say what it has loaded;
* report every AMS slot as the printer does, empty ones included, with the
  profile Bambuddy saved for it, and an external holder only while it holds
  a spool;
* keep to the caller's printers (#1727) and to callers who may read printer
  status.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _team_member, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

URL = "/api/v1/slicer/loaded-spools"


def _status(raw_data: dict, connected: bool = True) -> SimpleNamespace:
    return SimpleNamespace(connected=connected, raw_data=raw_data)


def _ams(ams_id: int, trays: list[dict]) -> dict:
    return {"id": ams_id, "tray": trays}


def _tray(tray_id: int, tray_type: str = "", **kw) -> dict:
    return {"id": tray_id, "tray_type": tray_type, **kw}


def _patched_status(statuses: dict[int, SimpleNamespace | None]):
    return patch(
        "backend.app.api.routes.slicer_presets.printer_manager.get_status",
        side_effect=lambda pid: statuses.get(pid),
    )


class TestLoadedSpools:
    async def test_lists_only_connected_printers(self, async_client: AsyncClient, printer_factory):
        online = await printer_factory(name="A online", model="BL-P001")
        disconnected = await printer_factory(name="B disconnected")
        never_seen = await printer_factory(name="C never seen")
        inactive = await printer_factory(name="D inactive", is_active=False)

        statuses = {
            online.id: _status({"ams": []}),
            disconnected.id: _status({"ams": [_ams(0, [_tray(0, "PLA")])]}, connected=False),
            never_seen.id: None,
            inactive.id: _status({"ams": []}),
        }
        with _patched_status(statuses):
            response = await async_client.get(URL)

        assert response.status_code == 200, response.text
        printers = response.json()["printers"]
        assert [p["id"] for p in printers] == [online.id]
        # The SSDP model code is turned into the short name @BBL tags use.
        assert printers[0]["model"] == "X1C"

    async def test_no_connected_printer_is_an_empty_list(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory()
        with _patched_status({printer.id: None}):
            response = await async_client.get(URL)
        assert response.status_code == 200
        assert response.json() == {"printers": []}

    async def test_reports_slots_with_their_saved_profile(self, async_client: AsyncClient, printer_factory, db_session):
        from backend.app.models.slot_preset import SlotPresetMapping

        printer = await printer_factory(model="H2D")
        db_session.add_all(
            [
                SlotPresetMapping(
                    printer_id=printer.id,
                    ams_id=0,
                    tray_id=1,
                    preset_id="local_7",
                    preset_name="Overture PLA Matte",
                    preset_source="local",
                    tray_info_idx="GFL99",
                ),
                SlotPresetMapping(
                    printer_id=printer.id,
                    ams_id=255,
                    tray_id=1,
                    preset_id="GFSG99",
                    preset_name="Generic PETG",
                    preset_source="cloud",
                ),
            ]
        )
        await db_session.commit()

        raw = {
            "ams": [
                _ams(
                    0,
                    [
                        _tray(0, "PLA", tray_color="FF0000FF", tray_info_idx="GFA00", tray_sub_brands="PLA Basic"),
                        _tray(1, "PLA", tray_color="00FF00FF", tray_info_idx="GFL99"),
                        _tray(2, exists=True),
                        _tray(3, state=9),
                    ],
                ),
                _ams(128, [_tray(0, "PETG", tray_color="0000FFFF")]),
            ],
            "vt_tray": [_tray(254, ""), _tray(255, "PETG", tray_color="FFFFFFFF")],
        }
        with _patched_status({printer.id: _status(raw)}):
            response = await async_client.get(URL)

        assert response.status_code == 200, response.text
        [entry] = response.json()["printers"]
        regular, ht = entry["ams"]

        assert regular["is_ams_ht"] is False
        assert [t["tray_id"] for t in regular["trays"]] == [0, 1, 2, 3]
        first, second, unknown, empty = regular["trays"]
        assert first["tray_sub_brands"] == "PLA Basic"
        assert first["saved_preset"] is None
        assert second["saved_preset"] == {
            "preset_id": "local_7",
            "preset_name": "Overture PLA Matte",
            "preset_source": "local",
            "tray_info_idx": "GFL99",
        }
        # Empty slots stay in the list, so the grid matches the unit.
        assert unknown["tray_type"] is None and unknown["exists"] is True
        assert empty["tray_type"] is None and empty["state"] == 9

        assert ht["is_ams_ht"] is True and ht["id"] == 128

        # The empty left holder is left out; the right one is AMS 255, tray 1.
        [ext] = entry["external"]
        assert (ext["ams_id"], ext["tray_id"]) == (255, 1)
        assert ext["saved_preset"]["preset_name"] == "Generic PETG"
        assert entry["external_holders"] == 2

    async def test_tolerates_malformed_status(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory()
        raw = {"ams": ["garbage", {"id": 1, "tray": [None, _tray(0, "ABS")]}], "vt_tray": None}
        with _patched_status({printer.id: _status(raw)}):
            response = await async_client.get(URL)
        assert response.status_code == 200, response.text
        [entry] = response.json()["printers"]
        assert [u["id"] for u in entry["ams"]] == [1]
        assert entry["external"] == []


class TestLoadedSpoolsAccess:
    async def test_limited_user_sees_only_their_printers(self, async_client: AsyncClient, printer_factory):
        mine = await printer_factory(name="Mine")
        other = await printer_factory(name="Other")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "slicer3172", [mine.id])
        # The team permissions don't include slicing; add it in a second group.
        slicing = await _group(async_client, admin, "slicing3172", permissions=["library:upload"])
        me = await async_client.get("/api/v1/auth/me", headers=_auth(jwt))
        groups = [g["id"] for g in me.json()["groups"]] + [slicing]
        updated = await async_client.patch(
            f"/api/v1/users/{me.json()['id']}", headers=_auth(admin), json={"group_ids": groups}
        )
        assert updated.status_code == 200, updated.text

        statuses = {mine.id: _status({"ams": []}), other.id: _status({"ams": []})}
        with _patched_status(statuses):
            response = await async_client.get(URL, headers=_auth(jwt))

        assert response.status_code == 200, response.text
        assert [p["id"] for p in response.json()["printers"]] == [mine.id]

    async def test_needs_printer_read(self, async_client: AsyncClient, printer_factory):
        """The slots are printer status; slicing alone doesn't grant it."""
        await printer_factory()
        admin = await _admin_token(async_client)
        group = await _group(async_client, admin, "uploadonly3172", permissions=["library:upload"])
        jwt, _ = await _user(async_client, admin, "uploadonly3172", [group])

        response = await async_client.get(URL, headers=_auth(jwt))
        assert response.status_code == 403
