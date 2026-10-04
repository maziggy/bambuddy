"""Printer-scoped access (#1727).

A group with ``restrict_printers`` set limits its members to the printers it
lists. These tests pin the contract end to end:

* a member sees and acts only on the team's printers; any other printer reads
  as missing (404), never as forbidden, so its id isn't confirmed;
* groups without the flag narrow nothing, so a user in no restricted group
  keeps every printer (upgrades are a no-op) and a permission group combined
  with a team group doesn't widen the team;
* a restricted group with no printers grants none -- it does not fall back to
  every printer;
* API keys, camera-stream, Cam Wall and WebSocket tokens all carry the scope of
  whoever created them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

TEAM_PERMISSIONS = [
    "printers:read",
    "printers:control",
    "camera:view",
    "queue:read_all",
    "queue:create",
    "archives:read_all",
    "archives:reprint_all",
    "archives:delete_all",
    "websocket:connect",
    "api_keys:create",
]


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "scopeadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "scopeadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _group(
    async_client: AsyncClient,
    admin_jwt: str,
    name: str,
    *,
    permissions: list[str] | None = None,
    printer_ids: list[int] | None = None,
) -> int:
    body: dict = {"name": name, "permissions": permissions or []}
    if printer_ids is not None:
        body.update(restrict_printers=True, printer_ids=printer_ids)
    response = await async_client.post("/api/v1/groups/", headers=_auth(admin_jwt), json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _user(async_client: AsyncClient, admin_jwt: str, username: str, group_ids: list[int]) -> tuple[str, int]:
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin_jwt),
        json={"username": username, "password": "UserPass1!", "group_ids": group_ids},
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"], created.json()["id"]


async def _team_member(async_client: AsyncClient, admin_jwt: str, username: str, printer_ids: list[int]):
    """A user in a permission group plus a team group restricted to *printer_ids*."""
    perms = await _group(async_client, admin_jwt, f"perms_{username}", permissions=TEAM_PERMISSIONS)
    team = await _group(async_client, admin_jwt, f"team_{username}", printer_ids=printer_ids)
    return await _user(async_client, admin_jwt, username, [perms, team])


async def _listed_ids(async_client: AsyncClient, headers: dict[str, str]) -> set[int]:
    response = await async_client.get("/api/v1/printers/", headers=headers)
    assert response.status_code == 200, response.text
    return {p["id"] for p in response.json()}


class TestVisibility:
    async def test_user_in_no_restricted_group_sees_every_printer(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        perms = await _group(async_client, admin, "plain", permissions=TEAM_PERMISSIONS)
        jwt, _ = await _user(async_client, admin, "plain_user", [perms])

        assert await _listed_ids(async_client, _auth(jwt)) == {a.id, b.id}

    async def test_team_member_sees_only_team_printers(self, async_client, printer_factory):
        a, _b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])

        # The permission group (no flag) must not widen the team group
        assert await _listed_ids(async_client, _auth(jwt)) == {a.id}

    async def test_hidden_printer_reads_as_missing(self, async_client, printer_factory, mock_printer_manager):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])
        headers = _auth(jwt)

        assert (await async_client.get(f"/api/v1/printers/{b.id}", headers=headers)).status_code == 404
        assert (await async_client.get(f"/api/v1/printers/{b.id}/status", headers=headers)).status_code == 404
        with patch("backend.app.api.routes.printers.printer_manager") as manager:
            stop = await async_client.post(f"/api/v1/printers/{b.id}/print/stop", headers=headers)
            assert stop.status_code == 404
            manager.stop_print.assert_not_called()
        # The same 404 a printer id that doesn't exist gets
        missing = await async_client.get("/api/v1/printers/999999", headers=headers)
        assert missing.status_code == 404
        assert (await async_client.get(f"/api/v1/printers/{a.id}", headers=headers)).status_code == 200

    async def test_scope_is_the_union_of_restricted_groups(self, async_client, printer_factory):
        a = await printer_factory(name="A")
        b = await printer_factory(name="B")
        await printer_factory(name="C")
        admin = await _admin_token(async_client)
        perms = await _group(async_client, admin, "perms", permissions=TEAM_PERMISSIONS)
        team_a = await _group(async_client, admin, "team_a", printer_ids=[a.id])
        team_b = await _group(async_client, admin, "team_b", printer_ids=[b.id])
        jwt, _ = await _user(async_client, admin, "both", [perms, team_a, team_b])

        assert await _listed_ids(async_client, _auth(jwt)) == {a.id, b.id}

    async def test_restricted_group_without_printers_grants_none(self, async_client, printer_factory):
        await printer_factory(name="A")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "locked_out", [])

        assert await _listed_ids(async_client, _auth(jwt)) == set()

    async def test_admin_sees_every_printer(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        await _team_member(async_client, admin, "member", [a.id])

        assert await _listed_ids(async_client, _auth(admin)) == {a.id, b.id}


class TestGroupApi:
    async def test_round_trip(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        group_id = await _group(async_client, admin, "team", printer_ids=[a.id])

        detail = (await async_client.get(f"/api/v1/groups/{group_id}", headers=_auth(admin))).json()
        assert detail["restrict_printers"] is True
        assert detail["printer_ids"] == [a.id]

        patched = await async_client.patch(
            f"/api/v1/groups/{group_id}", headers=_auth(admin), json={"printer_ids": [a.id, b.id]}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["printer_ids"] == [a.id, b.id]

        listed = (await async_client.get("/api/v1/groups/", headers=_auth(admin))).json()
        assert next(g for g in listed if g["id"] == group_id)["printer_ids"] == [a.id, b.id]

        # Turning the flag off keeps the selection
        off = await async_client.patch(
            f"/api/v1/groups/{group_id}", headers=_auth(admin), json={"restrict_printers": False}
        )
        assert off.json()["restrict_printers"] is False
        assert off.json()["printer_ids"] == [a.id, b.id]

    async def test_unknown_printer_is_rejected(self, async_client, printer_factory):
        await printer_factory(name="A")
        admin = await _admin_token(async_client)
        response = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin),
            json={"name": "team", "restrict_printers": True, "printer_ids": [424242]},
        )
        assert response.status_code == 400
        assert "424242" in response.json()["detail"]

    async def test_administrators_cannot_be_restricted(self, async_client):
        admin = await _admin_token(async_client)
        groups = (await async_client.get("/api/v1/groups/", headers=_auth(admin))).json()
        admins = next(g for g in groups if g["name"] == "Administrators")

        response = await async_client.patch(
            f"/api/v1/groups/{admins['id']}", headers=_auth(admin), json={"restrict_printers": True}
        )
        assert response.status_code == 400

    async def test_deleting_a_printer_drops_it_from_groups(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        group_id = await _group(async_client, admin, "team", printer_ids=[a.id, b.id])

        with patch("backend.app.api.routes.printers.printer_manager"):
            deleted = await async_client.delete(f"/api/v1/printers/{b.id}", headers=_auth(admin))
        assert deleted.status_code == 200, deleted.text

        detail = (await async_client.get(f"/api/v1/groups/{group_id}", headers=_auth(admin))).json()
        assert detail["printer_ids"] == [a.id]

    async def test_deleting_a_group_drops_its_printer_rows(self, async_client, printer_factory, db_session):
        from sqlalchemy import select

        from backend.app.models.group import group_printers

        a = await printer_factory(name="A")
        admin = await _admin_token(async_client)
        group_id = await _group(async_client, admin, "team", printer_ids=[a.id])

        assert (await async_client.delete(f"/api/v1/groups/{group_id}", headers=_auth(admin))).status_code == 204

        rows = await db_session.execute(select(group_printers).where(group_printers.c.group_id == group_id))
        assert rows.first() is None


class TestApiKeys:
    async def test_key_is_narrowed_to_its_owners_printers(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])

        # The key itself is unrestricted, but it can't out-rank its owner
        created = await async_client.post(
            "/api/v1/api-keys/", headers=_auth(jwt), json={"name": "k", "can_read_status": True}
        )
        assert created.status_code == 200, created.text
        key_headers = {"X-API-Key": created.json()["key"]}

        assert await _listed_ids(async_client, key_headers) == {a.id}
        assert (await async_client.get(f"/api/v1/printers/{b.id}", headers=key_headers)).status_code == 404
        webhook = await async_client.get(f"/api/v1/webhook/printer/{b.id}/status", headers=key_headers)
        assert webhook.status_code == 404

    async def test_key_printer_ids_now_bind_every_printer_route(self, async_client, printer_factory):
        """``printer_ids`` used to be checked by the file routes and webhooks only."""
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        created = await async_client.post(
            "/api/v1/api-keys/",
            headers=_auth(admin),
            json={"name": "k", "can_read_status": True, "can_control_printer": True, "printer_ids": [a.id]},
        )
        key_headers = {"X-API-Key": created.json()["key"]}

        assert await _listed_ids(async_client, key_headers) == {a.id}
        with patch("backend.app.api.routes.printers.printer_manager") as manager:
            stop = await async_client.post(f"/api/v1/printers/{b.id}/print/stop", headers=key_headers)
            assert stop.status_code == 404
            manager.stop_print.assert_not_called()


class TestTokens:
    async def test_camera_stream_token_carries_its_minters_scope(self, async_client, printer_factory):
        from backend.app.core.auth import verify_camera_stream_token

        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])

        minted = await async_client.post("/api/v1/printers/camera/stream-token", headers=_auth(jwt))
        token = minted.json()["token"]

        scope = await verify_camera_stream_token(token)
        assert scope is not None and scope.printer_ids == frozenset({a.id})
        snapshot = await async_client.get(f"/api/v1/printers/{b.id}/camera/snapshot?token={token}")
        assert snapshot.status_code == 404

    async def test_camwall_token_lists_only_its_owners_printers(self, async_client, printer_factory):
        a, _b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])

        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers=_auth(jwt),
            json={"name": "wall", "expires_in_days": 30, "scope": "camwall"},
        )
        assert created.status_code in (200, 201), created.text
        token = created.json()["token"]

        wall = await async_client.get(f"/api/v1/camwall/printers?token={token}")
        assert wall.status_code == 200, wall.text
        assert [p["id"] for p in wall.json()] == [a.id]

    async def test_websocket_token_carries_its_minters_scope(self, async_client, printer_factory):
        from backend.app.core.auth import principal_printer_scope, verify_websocket_token_principal
        from backend.app.core.database import async_session

        a, _b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])

        token = (await async_client.post("/api/v1/auth/ws-token", headers=_auth(jwt))).json()["token"]
        principal = await verify_websocket_token_principal(token)
        assert principal == ("member", None)
        async with async_session() as db:
            scope = await principal_printer_scope(db, *principal)
        assert scope.printer_ids == frozenset({a.id})

    async def test_websocket_token_minted_by_a_key_carries_the_keys_scope(self, async_client, printer_factory):
        from backend.app.core.auth import principal_printer_scope, verify_websocket_token_principal
        from backend.app.core.database import async_session

        a, _b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        created = await async_client.post(
            "/api/v1/api-keys/",
            headers=_auth(admin),
            json={"name": "k", "can_read_status": True, "printer_ids": [a.id]},
        )
        key_headers = {"X-API-Key": created.json()["key"]}

        token = (await async_client.post("/api/v1/auth/ws-token", headers=key_headers)).json()["token"]
        principal = await verify_websocket_token_principal(token)
        assert principal == ("", created.json()["id"])
        async with async_session() as db:
            scope = await principal_printer_scope(db, *principal)
        assert scope.printer_ids == frozenset({a.id})


class TestQueueAndHistory:
    async def test_queue_hides_and_refuses_other_printers(self, async_client, printer_factory, archive_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        archive = await archive_factory(a.id)
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])

        on_b = await async_client.post(
            "/api/v1/queue/", headers=_auth(admin), json={"archive_id": archive.id, "printer_id": b.id}
        )
        assert on_b.status_code == 200, on_b.text

        listed = await async_client.get("/api/v1/queue/", headers=_auth(jwt))
        assert on_b.json()["id"] not in {item["id"] for item in listed.json()}
        item = await async_client.get(f"/api/v1/queue/{on_b.json()['id']}", headers=_auth(jwt))
        assert item.status_code == 404

        refused = await async_client.post(
            "/api/v1/queue/", headers=_auth(jwt), json={"archive_id": archive.id, "printer_id": b.id}
        )
        assert refused.status_code == 404

    async def test_limited_key_cannot_queue_to_any_printer_of_a_model(
        self, async_client, printer_factory, archive_factory
    ):
        """A key's jobs record no creator, so "any X1C" would escape its printers."""
        a = await printer_factory(name="A", model="X1C")
        await printer_factory(name="B", model="X1C")
        archive = await archive_factory(a.id)
        admin = await _admin_token(async_client)
        created = await async_client.post(
            "/api/v1/api-keys/",
            headers=_auth(admin),
            json={"name": "k", "can_queue": True, "printer_ids": [a.id]},
        )
        key_headers = {"X-API-Key": created.json()["key"]}

        refused = await async_client.post(
            "/api/v1/queue/", headers=key_headers, json={"archive_id": archive.id, "target_model": "X1C"}
        )
        assert refused.status_code == 400
        pinned = await async_client.post(
            "/api/v1/queue/", headers=key_headers, json={"archive_id": archive.id, "printer_id": a.id}
        )
        assert pinned.status_code == 200, pinned.text

    async def test_library_add_to_queue_keeps_to_the_scope(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        created = await async_client.post(
            "/api/v1/api-keys/",
            headers=_auth(admin),
            json={"name": "k", "can_queue": True, "printer_ids": [a.id]},
        )
        key_headers = {"X-API-Key": created.json()["key"]}

        # Both are refused for the whole request, before any file is looked at
        hidden = await async_client.post(
            "/api/v1/library/files/add-to-queue", headers=key_headers, json={"file_ids": [1], "printer_id": b.id}
        )
        assert hidden.status_code == 404
        any_model = await async_client.post(
            "/api/v1/library/files/add-to-queue", headers=key_headers, json={"file_ids": [1]}
        )
        assert any_model.status_code == 400

    async def test_limited_user_may_queue_to_any_printer_of_a_model(
        self, async_client, printer_factory, archive_factory
    ):
        """The job carries the user's id, so the scheduler keeps it to their printers."""
        a = await printer_factory(name="A", model="X1C")
        await printer_factory(name="B", model="X1C")
        archive = await archive_factory(a.id)
        admin = await _admin_token(async_client)
        jwt, user_id = await _team_member(async_client, admin, "member", [a.id])

        queued = await async_client.post(
            "/api/v1/queue/", headers=_auth(jwt), json={"archive_id": archive.id, "target_model": "X1C"}
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["created_by_id"] == user_id

    async def test_archive_list_keeps_to_the_team_printers(self, async_client, printer_factory, archive_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        on_a = await archive_factory(a.id, print_name="on-a")
        on_b = await archive_factory(b.id, print_name="on-b")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])

        listed = await async_client.get("/api/v1/archives/", headers=_auth(jwt))
        ids = {row["id"] for row in listed.json()}
        assert on_a.id in ids
        assert on_b.id not in ids


class TestScheduler:
    async def test_model_matching_stays_within_the_scope(self, db_session, printer_factory):
        from backend.app.core.printer_scope import PrinterScope
        from backend.app.services.print_scheduler import PrintScheduler

        a = await printer_factory(name="A", model="X1C")
        await printer_factory(name="B", model="X1C")

        printers = await PrintScheduler()._printers_for_model(
            db_session, "X1C", printer_scope=PrinterScope(frozenset({a.id}))
        )
        assert [p.id for p in printers] == [a.id]

    async def test_deactivated_creator_reaches_no_printer(self, async_client, db_session, printer_factory):
        from backend.app.core.printer_scope import resolve_user_id_printer_scope

        await printer_factory(name="A")
        admin = await _admin_token(async_client)
        perms = await _group(async_client, admin, "perms", permissions=TEAM_PERMISSIONS)
        _jwt, user_id = await _user(async_client, admin, "gone", [perms])
        await async_client.patch(f"/api/v1/users/{user_id}", headers=_auth(admin), json={"is_active": False})

        scope = await resolve_user_id_printer_scope(db_session, user_id)
        assert scope.printer_ids == frozenset()


class TestWebSocketRefresh:
    async def test_group_change_rescopes_open_sockets(self, async_client, printer_factory):
        from types import SimpleNamespace

        from backend.app.core.printer_scope import ALL_PRINTERS
        from backend.app.core.websocket import ws_manager

        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])
        groups = (await async_client.get("/api/v1/groups/", headers=_auth(admin))).json()
        team_id = next(g["id"] for g in groups if g["name"] == "team_member")

        socket = SimpleNamespace(
            state=SimpleNamespace(bambuddy_printer_scope=ALL_PRINTERS, bambuddy_scope_principal=("member", None)),
            send_text=AsyncMock(),
        )
        ws_manager.active_connections.append(socket)
        try:
            await async_client.patch(f"/api/v1/groups/{team_id}", headers=_auth(admin), json={"printer_ids": [b.id]})
            assert socket.state.bambuddy_printer_scope.printer_ids == frozenset({b.id})

            await ws_manager.send_printer_status(a.id, {})
            socket.send_text.assert_not_awaited()
            await ws_manager.send_printer_status(b.id, {})
            socket.send_text.assert_awaited_once()
        finally:
            ws_manager.active_connections.remove(socket)


class TestByIdAndMedia:
    """Rows reached by id or by an ``<img>`` media token follow the same scope."""

    async def _archive_with_thumbnail(self, archive_factory, printer_id: int, name: str):
        import os
        from pathlib import Path

        from backend.app.core.config import settings

        rel = f"test_thumbs_1727_{os.getpid()}/{name}.png"
        thumb = Path(settings.base_dir) / rel
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return await archive_factory(printer_id, thumbnail_path=rel)

    async def test_archives_by_id_and_by_media_token(self, async_client, printer_factory, archive_factory):
        import os
        import shutil
        from pathlib import Path

        from backend.app.core.config import settings

        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        on_a = await self._archive_with_thumbnail(archive_factory, a.id, "on_a")
        on_b = await self._archive_with_thumbnail(archive_factory, b.id, "on_b")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [a.id])
        try:
            assert (await async_client.get(f"/api/v1/archives/{on_a.id}", headers=_auth(jwt))).status_code == 200
            assert (await async_client.get(f"/api/v1/archives/{on_b.id}", headers=_auth(jwt))).status_code == 404
            deleted = await async_client.delete(f"/api/v1/archives/{on_b.id}", headers=_auth(jwt))
            assert deleted.status_code == 404

            # <img> requests carry a media token and no headers
            member_token = (await async_client.post("/api/v1/auth/media-token", headers=_auth(jwt))).json()["token"]
            admin_token = (await async_client.post("/api/v1/auth/media-token", headers=_auth(admin))).json()["token"]
            own = await async_client.get(f"/api/v1/archives/{on_a.id}/thumbnail?token={member_token}")
            assert own.status_code == 200
            hidden = await async_client.get(f"/api/v1/archives/{on_b.id}/thumbnail?token={member_token}")
            assert hidden.status_code == 404
            # An admin's thumbnails keep loading: no headers must not mean "no printers"
            admin_view = await async_client.get(f"/api/v1/archives/{on_b.id}/thumbnail?token={admin_token}")
            assert admin_view.status_code == 200
        finally:
            shutil.rmtree(Path(settings.base_dir) / f"test_thumbs_1727_{os.getpid()}", ignore_errors=True)

    async def test_camera_stop_is_scoped(self, async_client, printer_factory):
        _a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin_token(async_client)
        jwt, _ = await _team_member(async_client, admin, "member", [_a.id])

        response = await async_client.post(f"/api/v1/printers/{b.id}/camera/stop", headers=_auth(jwt))
        assert response.status_code == 404

    async def test_clearing_the_print_log_keeps_other_printers_entries(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        from sqlalchemy import select

        from backend.app.models.print_log import PrintLogEntry

        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        await archive_factory(a.id)
        await archive_factory(b.id)
        admin = await _admin_token(async_client)
        perms = await _group(async_client, admin, "perms", permissions=TEAM_PERMISSIONS)
        team = await _group(async_client, admin, "team", printer_ids=[a.id])
        jwt, _ = await _user(async_client, admin, "member", [perms, team])

        cleared = await async_client.delete("/api/v1/print-log/", headers=_auth(jwt))
        assert cleared.status_code == 200, cleared.text

        left = (await db_session.execute(select(PrintLogEntry.printer_id))).scalars().all()
        assert left == [b.id]


async def _location_team(
    async_client: AsyncClient,
    admin_jwt: str,
    username: str,
    locations: list[str],
    *,
    printer_ids: list[int] | None = None,
    permissions: list[str] | None = None,
):
    """A member of a team group given *locations* (and optionally single printers)."""
    perms = await _group(async_client, admin_jwt, f"perms_{username}", permissions=permissions or TEAM_PERMISSIONS)
    response = await async_client.post(
        "/api/v1/groups/",
        headers=_auth(admin_jwt),
        json={
            "name": f"team_{username}",
            "restrict_printers": True,
            "printer_ids": printer_ids or [],
            "locations": locations,
        },
    )
    assert response.status_code == 201, response.text
    team = response.json()["id"]
    jwt, _ = await _user(async_client, admin_jwt, username, [perms, team])
    return jwt, team


class TestLocations:
    async def test_location_grants_its_printers_plus_picked_ones(self, async_client, printer_factory):
        lab_1 = await printer_factory(name="L1", location="Lab A")
        lab_2 = await printer_factory(name="L2", location="Lab A")
        picked = await printer_factory(name="P", location="Lab B")
        await printer_factory(name="Other", location="Lab B")
        await printer_factory(name="Nowhere")
        admin = await _admin_token(async_client)
        jwt, _ = await _location_team(async_client, admin, "lab", ["Lab A"], printer_ids=[picked.id])

        assert await _listed_ids(async_client, _auth(jwt)) == {lab_1.id, lab_2.id, picked.id}

    async def test_printer_added_to_a_location_is_reached_without_ticking_it(self, async_client, printer_factory):
        await printer_factory(name="L1", location="Lab A")
        admin = await _admin_token(async_client)
        jwt, _ = await _location_team(async_client, admin, "lab", ["Lab A"])

        later = await printer_factory(name="L2", location="Lab A")
        assert later.id in await _listed_ids(async_client, _auth(jwt))

    async def test_location_no_printer_has_grants_nothing(self, async_client, printer_factory):
        await printer_factory(name="A", location="Lab A")
        admin = await _admin_token(async_client)
        jwt, _ = await _location_team(async_client, admin, "lab", ["Basement"])

        assert await _listed_ids(async_client, _auth(jwt)) == set()

    async def test_locations_ignored_while_the_group_is_not_restricted(self, async_client, printer_factory):
        a = await printer_factory(name="A", location="Lab A")
        b = await printer_factory(name="B", location="Lab B")
        admin = await _admin_token(async_client)
        jwt, team = await _location_team(async_client, admin, "lab", ["Lab A"])
        off = await async_client.patch(
            f"/api/v1/groups/{team}", headers=_auth(admin), json={"restrict_printers": False}
        )
        assert off.json()["locations"] == ["Lab A"]

        assert await _listed_ids(async_client, _auth(jwt)) == {a.id, b.id}

    async def test_round_trip_trims_and_dedupes(self, async_client):
        admin = await _admin_token(async_client)
        created = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin),
            json={"name": "team", "restrict_printers": True, "locations": [" Lab A ", "Lab A", "", "Lab B"]},
        )
        assert created.status_code == 201, created.text
        group_id = created.json()["id"]
        assert created.json()["locations"] == ["Lab A", "Lab B"]

        patched = await async_client.patch(
            f"/api/v1/groups/{group_id}", headers=_auth(admin), json={"locations": ["Lab C"]}
        )
        assert patched.json()["locations"] == ["Lab C"]
        detail = (await async_client.get(f"/api/v1/groups/{group_id}", headers=_auth(admin))).json()
        assert detail["locations"] == ["Lab C"]
        listed = (await async_client.get("/api/v1/groups/", headers=_auth(admin))).json()
        assert next(g for g in listed if g["id"] == group_id)["locations"] == ["Lab C"]

        # Leaving the field out keeps it
        untouched = await async_client.patch(
            f"/api/v1/groups/{group_id}", headers=_auth(admin), json={"description": "x"}
        )
        assert untouched.json()["locations"] == ["Lab C"]

    async def test_overlong_location_is_rejected(self, async_client):
        admin = await _admin_token(async_client)
        response = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin),
            json={"name": "team", "restrict_printers": True, "locations": ["x" * 101]},
        )
        assert response.status_code == 400

    async def test_deleting_a_group_drops_its_location_rows(self, async_client, db_session):
        from sqlalchemy import select

        from backend.app.models.group import group_locations

        admin = await _admin_token(async_client)
        _, team = await _location_team(async_client, admin, "lab", ["Lab A"])
        assert (await async_client.delete(f"/api/v1/groups/{team}", headers=_auth(admin))).status_code == 204

        rows = await db_session.execute(select(group_locations).where(group_locations.c.group_id == team))
        assert rows.first() is None


class TestMovingPrinters:
    """A location grant turns a printer's location into an access setting."""

    async def test_non_admin_cannot_move_a_printer_out_of_a_granted_location(self, async_client, printer_factory):
        a = await printer_factory(name="A", location="Lab A")
        admin = await _admin_token(async_client)
        jwt, _ = await _location_team(
            async_client, admin, "lab", ["Lab A"], permissions=[*TEAM_PERMISSIONS, "printers:update"]
        )

        response = await async_client.patch(f"/api/v1/printers/{a.id}", headers=_auth(jwt), json={"location": "Lab B"})
        assert response.status_code == 403
        assert (await async_client.get(f"/api/v1/printers/{a.id}", headers=_auth(admin))).json()["location"] == "Lab A"

    async def test_non_admin_cannot_move_a_printer_into_a_granted_location(self, async_client, printer_factory):
        a = await printer_factory(name="A", location="Lab B")
        admin = await _admin_token(async_client)
        await _location_team(async_client, admin, "lab", ["Lab A"])
        perms = await _group(async_client, admin, "editors", permissions=["printers:read", "printers:update"])
        jwt, _ = await _user(async_client, admin, "editor", [perms])

        response = await async_client.patch(f"/api/v1/printers/{a.id}", headers=_auth(jwt), json={"location": "Lab A"})
        assert response.status_code == 403

    async def test_non_admin_may_move_between_locations_no_group_was_given(self, async_client, printer_factory):
        a = await printer_factory(name="A", location="Shelf 1")
        admin = await _admin_token(async_client)
        await _location_team(async_client, admin, "lab", ["Lab A"])
        perms = await _group(async_client, admin, "editors", permissions=["printers:read", "printers:update"])
        jwt, _ = await _user(async_client, admin, "editor", [perms])

        response = await async_client.patch(
            f"/api/v1/printers/{a.id}", headers=_auth(jwt), json={"location": " Shelf 2 "}
        )
        assert response.status_code == 200, response.text
        # Trimmed, so it can match a location given to a group later
        assert response.json()["location"] == "Shelf 2"

    async def test_admin_move_rescopes_members_and_open_sockets(self, async_client, printer_factory):
        from types import SimpleNamespace

        from backend.app.core.printer_scope import ALL_PRINTERS
        from backend.app.core.websocket import ws_manager

        a = await printer_factory(name="A", location="Lab A")
        b = await printer_factory(name="B", location="Lab B")
        admin = await _admin_token(async_client)
        jwt, _ = await _location_team(async_client, admin, "lab", ["Lab A"])

        socket = SimpleNamespace(
            state=SimpleNamespace(bambuddy_printer_scope=ALL_PRINTERS, bambuddy_scope_principal=("lab", None)),
            send_text=AsyncMock(),
        )
        ws_manager.active_connections.append(socket)
        try:
            moved = await async_client.patch(
                f"/api/v1/printers/{b.id}", headers=_auth(admin), json={"location": "Lab A"}
            )
            assert moved.status_code == 200, moved.text
            assert socket.state.bambuddy_printer_scope.printer_ids == frozenset({a.id, b.id})
        finally:
            ws_manager.active_connections.remove(socket)

        assert await _listed_ids(async_client, _auth(jwt)) == {a.id, b.id}


class TestLocationsPage:
    """The Printer Locations page (#2962) moves printers too, so it keeps to the same rules."""

    async def _editor(self, async_client, admin):
        perms = await _group(async_client, admin, "editors", permissions=["printers:read", "printers:update"])
        jwt, _ = await _user(async_client, admin, "editor", [perms])
        return jwt

    async def test_list_shows_a_limited_caller_only_their_locations(self, async_client, printer_factory):
        mine = await printer_factory(name="M", location="Lab A")
        await printer_factory(name="T", location="Lab A")
        await printer_factory(name="O", location="Lab B")
        admin = await _admin_token(async_client)
        await async_client.post("/api/v1/printer-locations/", headers=_auth(admin), json={"name": "Empty room"})
        jwt, _ = await _team_member(async_client, admin, "member", [mine.id])

        listed = (await async_client.get("/api/v1/printer-locations/", headers=_auth(jwt))).json()
        assert [(loc["name"], loc["printer_count"]) for loc in listed] == [("Lab A", 1)]
        everything = (await async_client.get("/api/v1/printer-locations/", headers=_auth(admin))).json()
        assert {loc["name"] for loc in everything} == {"Lab A", "Lab B", "Empty room"}

    async def test_rename_carries_the_grant_so_nobody_loses_access(self, async_client, printer_factory, db_session):
        from sqlalchemy import select

        from backend.app.models.group import group_locations

        a = await printer_factory(name="A", location="Lab A")
        admin = await _admin_token(async_client)
        member, team = await _location_team(async_client, admin, "lab", ["Lab A"])
        editor = await self._editor(async_client, admin)

        response = await async_client.patch(
            "/api/v1/printer-locations/", headers=_auth(editor), json={"name": "Lab A", "new_name": "Room 101"}
        )
        assert response.status_code == 200, response.text
        assert a.id in await _listed_ids(async_client, _auth(member))
        grants = (
            await db_session.execute(select(group_locations.c.location).where(group_locations.c.group_id == team))
        ).scalars()
        assert list(grants) == ["Room 101"]

    async def test_rename_onto_a_granted_name_is_for_admins(self, async_client, printer_factory):
        await printer_factory(name="A", location="Shelf")
        admin = await _admin_token(async_client)
        await _location_team(async_client, admin, "lab", ["Lab A"])  # granted, holds no printer yet
        editor = await self._editor(async_client, admin)

        body = {"name": "Shelf", "new_name": "Lab A"}
        response = await async_client.patch("/api/v1/printer-locations/", headers=_auth(editor), json=body)
        assert response.status_code == 403
        response = await async_client.patch("/api/v1/printer-locations/", headers=_auth(admin), json=body)
        assert response.status_code == 200, response.text

    async def test_delete_of_a_granted_location_is_for_admins_and_takes_the_grant(
        self, async_client, printer_factory, db_session
    ):
        from sqlalchemy import select

        from backend.app.models.group import group_locations

        await printer_factory(name="A", location="Lab A")
        admin = await _admin_token(async_client)
        member, _ = await _location_team(async_client, admin, "lab", ["Lab A"])
        editor = await self._editor(async_client, admin)

        body = {"names": ["Lab A"]}
        assert (
            await async_client.post("/api/v1/printer-locations/delete", headers=_auth(editor), json=body)
        ).status_code == 403
        assert (
            await async_client.post("/api/v1/printer-locations/delete", headers=_auth(admin), json=body)
        ).status_code == 200
        assert (await db_session.execute(select(group_locations))).first() is None

        # A later location of the same name is not handed to the old group.
        later = await printer_factory(name="B", location="Lab A")
        assert later.id not in await _listed_ids(async_client, _auth(member))

    async def test_assign_into_or_out_of_a_granted_location_is_for_admins(self, async_client, printer_factory):
        inside = await printer_factory(name="In", location="Lab A")
        outside = await printer_factory(name="Out", location="Shelf")
        admin = await _admin_token(async_client)
        await _location_team(async_client, admin, "lab", ["Lab A"])
        editor = await self._editor(async_client, admin)

        for body in (
            {"printer_ids": [outside.id], "location": "Lab A"},
            {"printer_ids": [inside.id], "location": None},
        ):
            response = await async_client.post("/api/v1/printer-locations/assign", headers=_auth(editor), json=body)
            assert response.status_code == 403, body
        ungranted = {"printer_ids": [outside.id], "location": "Shelf 2"}
        response = await async_client.post("/api/v1/printer-locations/assign", headers=_auth(editor), json=ungranted)
        assert response.status_code == 200, response.text

    async def test_a_limited_caller_cannot_move_or_rename_what_they_cannot_see(self, async_client, printer_factory):
        mine = await printer_factory(name="M", location="Lab A")
        theirs = await printer_factory(name="T", location="Lab A")
        admin = await _admin_token(async_client)
        perms = await _group(
            async_client, admin, "perms", permissions=["printers:read", "printers:update", *TEAM_PERMISSIONS]
        )
        team = await _group(async_client, admin, "team", printer_ids=[mine.id])
        jwt, _ = await _user(async_client, admin, "member", [perms, team])

        moved = await async_client.post(
            "/api/v1/printer-locations/assign",
            headers=_auth(jwt),
            json={"printer_ids": [theirs.id], "location": "Elsewhere"},
        )
        assert moved.status_code == 404
        renamed = await async_client.patch(
            "/api/v1/printer-locations/", headers=_auth(jwt), json={"name": "Lab A", "new_name": "Lab Z"}
        )
        assert renamed.status_code == 403
        assert (await async_client.get(f"/api/v1/printers/{theirs.id}", headers=_auth(admin))).json()[
            "location"
        ] == "Lab A"
