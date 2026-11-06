"""Notification providers and logs keep to the caller's printers."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.notification import NotificationLog
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PERMS = [
    "printers:read",
    "notifications:read",
    "notifications:create",
    "notifications:update",
    "notifications:delete",
]


def _provider(name: str, printer_id: int | None) -> dict:
    return {
        "name": name,
        "provider_type": "callmebot",
        "enabled": True,
        "config": {"phone_number": "+1234567890", "api_key": "test-api-key"},
        "printer_id": printer_id,
    }


async def _setup(client: AsyncClient, printer_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(client)
    perms = await _group(client, admin, "perms", permissions=PERMS)
    team = await _group(client, admin, "team", printer_ids=[a.id])
    member = _auth((await _user(client, admin, "member", [perms, team]))[0])
    ids = {}
    for name, printer in (("ours", a.id), ("theirs", b.id), ("everyone", None)):
        created = await client.post("/api/v1/notifications/", headers=_auth(admin), json=_provider(name, printer))
        assert created.status_code == 200, created.text
        ids[name] = created.json()["id"]
    return a, b, member, ids


async def test_a_member_sees_and_manages_only_providers_of_their_printers(async_client: AsyncClient, printer_factory):
    a, b, member, ids = await _setup(async_client, printer_factory)

    listed = await async_client.get("/api/v1/notifications/", headers=member)

    assert [p["name"] for p in listed.json()] == ["ours"]
    for name in ("theirs", "everyone"):
        pid = ids[name]
        assert (await async_client.get(f"/api/v1/notifications/{pid}", headers=member)).status_code == 404
        patched = await async_client.patch(f"/api/v1/notifications/{pid}", headers=member, json={"name": "mine now"})
        assert patched.status_code == 404
        assert (await async_client.post(f"/api/v1/notifications/{pid}/test", headers=member)).status_code == 404
        assert (await async_client.delete(f"/api/v1/notifications/{pid}", headers=member)).status_code == 404
    assert (await async_client.get(f"/api/v1/notifications/{ids['ours']}", headers=member)).status_code == 200


async def test_a_member_binds_providers_to_their_printers_only(async_client: AsyncClient, printer_factory):
    a, b, member, ids = await _setup(async_client, printer_factory)

    to_other = await async_client.post("/api/v1/notifications/", headers=member, json=_provider("x", b.id))
    to_all = await async_client.post("/api/v1/notifications/", headers=member, json=_provider("y", None))
    to_own = await async_client.post("/api/v1/notifications/", headers=member, json=_provider("z", a.id))
    retarget_other = await async_client.patch(
        f"/api/v1/notifications/{ids['ours']}", headers=member, json={"printer_id": b.id}
    )
    retarget_all = await async_client.patch(
        f"/api/v1/notifications/{ids['ours']}", headers=member, json={"printer_id": None}
    )

    assert (to_other.status_code, to_all.status_code, to_own.status_code) == (404, 400, 200)
    assert (retarget_other.status_code, retarget_all.status_code) == (404, 400)


async def test_logs_and_their_counts_keep_to_the_team_printers(async_client: AsyncClient, db_session, printer_factory):
    a, b, member, ids = await _setup(async_client, printer_factory)
    a_id, b_id = a.id, b.id
    old = datetime.utcnow() - timedelta(days=40)
    for printer, when in ((a_id, None), (b_id, None), (None, None), (a_id, old), (b_id, old), (None, old)):
        row = NotificationLog(
            provider_id=ids["ours"], event_type="print_start", title="t", message="m", printer_id=printer
        )
        if when is not None:
            row.created_at = when
        db_session.add(row)
    await db_session.commit()

    listed = await async_client.get("/api/v1/notifications/logs", headers=member)
    stats = await async_client.get("/api/v1/notifications/logs/stats", headers=member)
    cleared = await async_client.delete("/api/v1/notifications/logs", headers=member, params={"older_than_days": 30})

    assert sorted((row["printer_id"] or 0) for row in listed.json()) == [0, a_id]
    assert stats.json()["total"] == 2
    assert cleared.json()["deleted"] == 1
    db_session.expire_all()
    left = sorted((pid or 0) for (pid,) in (await db_session.execute(select(NotificationLog.printer_id))).all())
    assert left == sorted([a_id, b_id, 0, b_id, 0])


async def test_testing_a_stored_provider_answers_a_hidden_one_like_a_missing_one(
    async_client: AsyncClient, printer_factory
):
    a, b, member, ids = await _setup(async_client, printer_factory)
    body = {"provider_type": "callmebot", "config": {"phone_number": "+1234567890", "api_key": "********"}}

    hidden = await async_client.post(
        "/api/v1/notifications/test-config", headers=member, json={**body, "provider_id": ids["theirs"]}
    )
    missing = await async_client.post(
        "/api/v1/notifications/test-config", headers=member, json={**body, "provider_id": 999_999}
    )

    assert (
        (hidden.status_code, hidden.json())
        == (missing.status_code, missing.json())
        == (
            404,
            {"detail": "Notification provider not found"},
        )
    )


async def test_app_messages_reach_only_the_team_printers_channels(async_client: AsyncClient, printer_factory):
    from unittest.mock import AsyncMock, patch

    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    perms = await _group(async_client, admin, "perms", permissions=PERMS)
    team = await _group(async_client, admin, "team", printer_ids=[a.id])
    member = _auth((await _user(async_client, admin, "member", [perms, team]))[0])
    for name, printer in (("ours", a.id), ("theirs", b.id), ("everyone", None)):
        created = await async_client.post(
            "/api/v1/notifications/", headers=_auth(admin), json={**_provider(name, printer), "on_app_message": True}
        )
        assert created.status_code == 200, created.text

    channels = await async_client.get("/api/v1/notifications/app-message/channels", headers=member)
    with patch(
        "backend.app.services.notification_service.NotificationService._send_to_providers", new=AsyncMock()
    ) as send:
        sent = await async_client.post(
            "/api/v1/notifications/app-message", headers=member, json={"title": "t", "message": "m"}
        )

    assert [c["name"] for c in channels.json()] == ["ours"]
    assert sent.json()["channels"] == 1
    assert [p.name for p in send.await_args.args[0]] == ["ours"]


async def test_the_log_does_not_name_providers_the_caller_cannot_see(
    async_client: AsyncClient, db_session, printer_factory
):
    a, b, member, ids = await _setup(async_client, printer_factory)
    a_id = a.id
    for provider in ("ours", "everyone"):
        db_session.add(
            NotificationLog(
                provider_id=ids[provider],
                event_type="print_start",
                title="t",
                message="m",
                printer_id=a_id,
                success=False,
                error_message=f"failed for {provider}",
            )
        )
    await db_session.commit()

    listed = await async_client.get("/api/v1/notifications/logs", headers=member)
    stats = await async_client.get("/api/v1/notifications/logs/stats", headers=member)

    rows = sorted(listed.json(), key=lambda row: str(row["provider_name"]))
    assert [(row["provider_name"], row["error_message"]) for row in rows] == [
        (None, None),
        ("ours", "failed for ours"),
    ]
    assert stats.json()["by_provider"] == {"ours": 1, "Other": 1}


async def test_log_entries_without_a_printer_stay_with_their_provider(
    async_client: AsyncClient, db_session, printer_factory
):
    a, b, member, ids = await _setup(async_client, printer_factory)
    for provider in ("ours", "everyone"):
        db_session.add(
            NotificationLog(
                provider_id=ids[provider], event_type="app:key", title=provider, message="m", printer_id=None
            )
        )
    await db_session.commit()

    listed = await async_client.get("/api/v1/notifications/logs", headers=member)
    stats = await async_client.get("/api/v1/notifications/logs/stats", headers=member)

    assert [row["title"] for row in listed.json()] == ["ours"]
    assert stats.json()["total"] == 1


async def test_a_log_entry_whose_provider_is_gone_shows_no_error(
    async_client: AsyncClient, db_session, printer_factory
):
    if db_session.bind.dialect.name != "sqlite":
        pytest.skip("only SQLite can keep such an entry")
    a, b, member, ids = await _setup(async_client, printer_factory)
    db_session.add(
        NotificationLog(
            provider_id=999_999,
            event_type="print_start",
            title="t",
            message="m",
            printer_id=a.id,
            success=False,
            error_message="failed",
        )
    )
    await db_session.commit()

    listed = await async_client.get("/api/v1/notifications/logs", headers=member)

    assert [(row["provider_name"], row["error_message"]) for row in listed.json()] == [(None, None)]
