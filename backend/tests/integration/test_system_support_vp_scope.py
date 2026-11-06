"""System info, support logs and virtual printers keep to the caller's printers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.log_health import LogFinding, ScanResult
from backend.app.services.printer_manager import printer_manager
from backend.app.services.virtual_printer import virtual_printer_manager
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PERMS = ["printers:read", "system:read", "settings:read", "settings:update"]


@pytest.fixture(autouse=True)
def _no_vp_services():
    with (
        patch.object(virtual_printer_manager, "sync_from_db", AsyncMock()),
        patch.object(virtual_printer_manager, "configure", AsyncMock()),
    ):
        yield


async def _setup(client: AsyncClient, printer_factory, permissions=PERMS):
    a = await printer_factory(name="Ours", serial_number="00M09A000000111")
    b = await printer_factory(name="Theirs", serial_number="00M09A000000222")
    token = await _admin_token(client)
    perms = await _group(client, token, "perms", permissions=permissions)
    team = await _group(client, token, "team", printer_ids=[a.id])
    member = _auth((await _user(client, token, "member", [perms, team]))[0])
    return a.id, b.id, _auth(token), member


async def test_system_info_lists_and_counts_only_the_caller_printers(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member = await _setup(async_client, printer_factory)
    connected = SimpleNamespace(state=SimpleNamespace(connected=True, state="IDLE"))
    with patch.dict(printer_manager._clients, {a_id: connected, b_id: connected}):
        mine = (await async_client.get("/api/v1/system/info", headers=member)).json()["printers"]
        everyone = (await async_client.get("/api/v1/system/info", headers=admin)).json()["printers"]

    assert [p["name"] for p in mine["connected_list"]] == ["Ours"]
    assert (mine["total"], mine["connected"]) == (1, 1)
    assert (everyone["total"], everyone["connected"]) == (2, 2)


async def test_health_findings_come_without_log_lines_for_a_limited_caller(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member = await _setup(async_client, printer_factory)

    def scan(**_):
        finding = LogFinding(
            signature_id="x",
            severity="error",
            category="bug",
            wiki_anchor="x",
            count=1,
            first_seen="t",
            last_seen="t",
            sample="a line",
        )
        return ScanResult(findings=[finding], scanned_entries=1, log_available=True, summary={"total": 1})

    with patch("backend.app.api.routes.system.scan_logs", scan):
        mine = (await async_client.get("/api/v1/system/health", headers=member)).json()
        everyone = (await async_client.get("/api/v1/system/health", headers=admin)).json()

    assert [(f["signature_id"], f["sample"]) for f in mine["findings"]] == [("x", "")]
    assert everyone["findings"][0]["sample"] == "a line"


async def test_logs_and_the_support_bundle_need_settings_update(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member = await _setup(
        async_client, printer_factory, permissions=["printers:read", "settings:read"]
    )

    logs = await async_client.get("/api/v1/support/logs", headers=member)
    bundle = await async_client.get("/api/v1/support/bundle", headers=member)

    assert (logs.status_code, bundle.status_code) == (403, 403)


async def test_a_virtual_printer_bridging_to_another_printer_is_not_shown(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member = await _setup(async_client, printer_factory)
    created = await async_client.post(
        "/api/v1/virtual-printers", headers=admin, json={"name": "proxy", "mode": "proxy", "target_printer_id": b_id}
    )
    vp_id = created.json()["id"]
    plain = (await async_client.post("/api/v1/virtual-printers", headers=admin, json={"name": "plain"})).json()["id"]

    listed = (await async_client.get("/api/v1/virtual-printers", headers=member)).json()
    got = await async_client.get(f"/api/v1/virtual-printers/{vp_id}", headers=member)
    diagnosed = await async_client.get(f"/api/v1/virtual-printers/{vp_id}/diagnostic", headers=member)
    missing = await async_client.get("/api/v1/virtual-printers/999999", headers=member)

    entries = listed["printers"] if isinstance(listed, dict) else listed
    assert [vp["id"] for vp in entries] == [plain]
    assert (
        (got.status_code, got.json())
        == (diagnosed.status_code, diagnosed.json())
        == (
            missing.status_code,
            missing.json(),
        )
    )
    assert missing.status_code == 404


async def test_a_member_cannot_point_a_virtual_printer_at_another_printer(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member = await _setup(async_client, printer_factory)
    theirs = (
        await async_client.post(
            "/api/v1/virtual-printers", headers=admin, json={"name": "t", "mode": "proxy", "target_printer_id": b_id}
        )
    ).json()["id"]
    ours = (
        await async_client.post(
            "/api/v1/virtual-printers", headers=member, json={"name": "o", "mode": "proxy", "target_printer_id": a_id}
        )
    ).json()["id"]

    created = await async_client.post(
        "/api/v1/virtual-printers", headers=member, json={"name": "x", "mode": "proxy", "target_printer_id": b_id}
    )
    missing = await async_client.post(
        "/api/v1/virtual-printers", headers=member, json={"name": "x", "mode": "proxy", "target_printer_id": 999_999}
    )
    retargeted = await async_client.put(
        f"/api/v1/virtual-printers/{ours}", headers=member, json={"target_printer_id": b_id}
    )
    changed = await async_client.put(f"/api/v1/virtual-printers/{theirs}", headers=member, json={"name": "mine"})
    deleted = await async_client.delete(f"/api/v1/virtual-printers/{theirs}", headers=member)
    renamed = await async_client.put(f"/api/v1/virtual-printers/{ours}", headers=member, json={"name": "ours2"})

    await async_client.put(
        f"/api/v1/virtual-printers/{theirs}", headers=admin, json={"bind_ip": "192.0.2.10", "enabled": True}
    )
    clashing = await async_client.put(
        f"/api/v1/virtual-printers/{ours}", headers=member, json={"bind_ip": "192.0.2.10", "enabled": True}
    )
    assert clashing.status_code == 400
    assert "'" not in clashing.json()["detail"]
    assert created.status_code == missing.status_code == 400
    assert created.json()["detail"] == f"Printer with ID {b_id} not found"
    assert retargeted.status_code == 400
    assert (changed.status_code, deleted.status_code) == (404, 404)
    assert renamed.status_code == 200
    kept = (await async_client.get(f"/api/v1/virtual-printers/{theirs}", headers=admin)).json()
    assert (kept["name"], kept["target_printer_id"]) == ("t", b_id)
    assert (await async_client.get(f"/api/v1/virtual-printers/{ours}", headers=admin)).json()[
        "target_printer_id"
    ] == a_id


async def test_the_legacy_virtual_printer_settings_keep_to_the_caller_printers(
    async_client: AsyncClient, printer_factory, db_session
):
    from backend.app.models.settings import Settings

    a_id, b_id, admin, member = await _setup(async_client, printer_factory)
    # Stored by an older version, which still let this route aim it.
    db_session.add(Settings(key="virtual_printer_target_printer_id", value=str(b_id)))
    await db_session.commit()

    changed = await async_client.put("/api/v1/settings/virtual-printer?archive_name_source=filename", headers=member)

    assert changed.status_code == 404

    # The settings also reach the first virtual printer: with the stored target
    # now one the member may use, the 404 comes from that row alone.
    from sqlalchemy import update

    await db_session.execute(
        update(Settings).where(Settings.key == "virtual_printer_target_printer_id").values(value=str(a_id))
    )
    await db_session.commit()
    allowed = await async_client.put("/api/v1/settings/virtual-printer?archive_name_source=filename", headers=member)
    assert allowed.status_code == 200, allowed.text
    await async_client.post(
        "/api/v1/virtual-printers", headers=admin, json={"name": "first", "mode": "proxy", "target_printer_id": b_id}
    )
    toggled = await async_client.put("/api/v1/settings/virtual-printer?tailscale_disabled=false", headers=member)
    assert toggled.status_code == 404


async def test_the_legacy_status_leaves_out_what_names_the_target(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member = await _setup(async_client, printer_factory)
    status = {
        "running": True,
        "name": "N",
        "serial": "S",
        "target_printer_ip": "192.0.2.1",
        "proxy": {"target_host": "192.0.2.1"},
    }

    with patch.object(virtual_printer_manager, "get_status", return_value=status):
        mine = (await async_client.get("/api/v1/settings/virtual-printer", headers=member)).json()["status"]
        everyone = (await async_client.get("/api/v1/settings/virtual-printer", headers=admin)).json()["status"]

    assert mine == {"running": True}
    assert everyone == status
