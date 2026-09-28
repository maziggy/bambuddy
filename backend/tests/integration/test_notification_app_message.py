"""Messages from other applications through the notification channels.

The contract these tests pin:

  ``POST /notifications/app-message`` hands an app's message to every enabled
  channel with ``on_app_message`` on, and to no other. An API key needs the
  opt-in ``can_send_notifications`` scope, and its owner the
  ``notifications:update`` permission (a key can't do what its owner may not).
  Users need ``notifications:update``. Text is plain, the link http(s) only,
  and each caller is rate-limited.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import notifications as notification_routes
from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.notification import NotificationProvider
from backend.app.models.user import User

URL = "/api/v1/notifications/app-message"


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    notification_routes._app_message_times.clear()
    yield
    notification_routes._app_message_times.clear()


async def _admin_token(client: AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "notifyadmin",
            "admin_password": "AdminPass1!",  # pragma: allowlist secret
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "notifyadmin", "password": "AdminPass1!"},  # pragma: allowlist secret
    )
    return login.json()["access_token"]


async def _user_id(db: AsyncSession, username: str) -> int:
    return (await db.execute(select(User).where(User.username == username))).scalar_one().id


async def _key(db: AsyncSession, *, owner_id: int | None, allowed: bool, name: str = "Bambuddy Orders") -> str:
    full_key, key_hash, key_prefix = generate_api_key()
    db.add(
        APIKey(name=name, key_hash=key_hash, key_prefix=key_prefix, user_id=owner_id, can_send_notifications=allowed)
    )
    await db.commit()
    return full_key


async def _channels(db: AsyncSession) -> None:
    for name, on, enabled in (
        ("Telegram", True, True),
        ("ntfy", True, True),
        ("Email", False, True),
        ("Old", True, False),
    ):
        db.add(NotificationProvider(name=name, provider_type="ntfy", enabled=enabled, config="{}", on_app_message=on))
    await db.commit()


def _sent():
    return patch(
        "backend.app.services.notification_service.notification_service._send_to_provider",
        new=AsyncMock(return_value=(True, "")),
    )


class TestAppMessage:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_goes_to_the_channels_that_accept_app_messages(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        await _channels(db_session)
        key = await _key(db_session, owner_id=await _user_id(db_session, "notifyadmin"), allowed=True)

        with _sent() as send:
            resp = await async_client.post(
                URL,
                headers={"X-API-Key": key},
                json={
                    "title": "3 orders need you",
                    "message": "#1004, #1009, #1010",
                    "url": "http://orders.lan:8090/todo",
                },
            )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"channels": 2}
        names = sorted(call.args[0].name for call in send.await_args_list)
        assert names == ["Telegram", "ntfy"]
        _, title, body = send.await_args_list[0].args[:3]
        assert title == "3 orders need you"
        assert body == "#1004, #1009, #1010\nhttp://orders.lan:8090/todo"
        assert send.await_args_list[0].kwargs["event_type"] == "app:Bambuddy Orders"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_lists_the_channels_by_name(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        await _channels(db_session)
        key = await _key(db_session, owner_id=await _user_id(db_session, "notifyadmin"), allowed=True)
        resp = await async_client.get(f"{URL}/channels", headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json() == [{"name": "Telegram", "provider_type": "ntfy"}, {"name": "ntfy", "provider_type": "ntfy"}]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_key_without_the_scope_is_refused(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        key = await _key(db_session, owner_id=await _user_id(db_session, "notifyadmin"), allowed=False)
        resp = await async_client.post(URL, headers={"X-API-Key": key}, json={"title": "t", "message": "m"})
        assert resp.status_code == 403
        assert "send_notifications" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_key_cant_do_what_its_owner_may_not(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        db_session.add(User(username="nobody", password_hash="x", role="user", is_active=True))
        await db_session.commit()
        key = await _key(db_session, owner_id=await _user_id(db_session, "nobody"), allowed=True)
        resp = await async_client.post(URL, headers={"X-API-Key": key}, json={"title": "t", "message": "m"})
        assert resp.status_code == 403
        assert "notifications:update" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_admin_user_may_send(self, async_client: AsyncClient, db_session):
        token = await _admin_token(async_client)
        with _sent():
            resp = await async_client.post(
                URL, headers={"Authorization": f"Bearer {token}"}, json={"title": "Test", "message": "Hello"}
            )
        assert resp.status_code == 200
        assert resp.json() == {"channels": 0}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unauthenticated_is_refused(self, async_client: AsyncClient):
        await _admin_token(async_client)
        resp = await async_client.post(URL, json={"title": "t", "message": "m"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "payload",
        [
            {"title": "", "message": "m"},
            {"title": "t", "message": "   "},
            {"title": "t" * 121, "message": "m"},
            {"title": "t", "message": "m", "url": "javascript:alert(1)"},
            {"title": "t", "message": "m", "url": "http://a b"},
        ],
    )
    async def test_refuses_what_isnt_plain_text_or_a_web_link(self, async_client: AsyncClient, db_session, payload):
        await _admin_token(async_client)
        key = await _key(db_session, owner_id=await _user_id(db_session, "notifyadmin"), allowed=True)
        resp = await async_client.post(URL, headers={"X-API-Key": key}, json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_control_characters_are_dropped(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        await _channels(db_session)
        key = await _key(db_session, owner_id=await _user_id(db_session, "notifyadmin"), allowed=True)
        with _sent() as send:
            await async_client.post(URL, headers={"X-API-Key": key}, json={"title": "Hi\x07", "message": "a\nb\x1b"})
        assert send.await_args_list[0].args[1:3] == ("Hi", "a\nb")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_each_caller_is_rate_limited(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        owner = await _user_id(db_session, "notifyadmin")
        key = await _key(db_session, owner_id=owner, allowed=True)
        other = await _key(db_session, owner_id=owner, allowed=True, name="Other app")
        with _sent():
            codes = [
                (
                    await async_client.post(URL, headers={"X-API-Key": key}, json={"title": "t", "message": "m"})
                ).status_code
                for _ in range(notification_routes.APP_MESSAGE_LIMIT + 1)
            ]
            other_code = (
                await async_client.post(URL, headers={"X-API-Key": other}, json={"title": "t", "message": "m"})
            ).status_code
        assert codes[:-1] == [200] * notification_routes.APP_MESSAGE_LIMIT
        assert codes[-1] == 429
        assert other_code == 200


class TestProviderAndKeyFlags:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_channel_switch_round_trips(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/notifications/",
            json={"name": "Phone", "provider_type": "ntfy", "config": {"server": "https://ntfy.sh", "topic": "x"}},
        )
        assert resp.status_code == 200, resp.text
        provider = resp.json()
        assert provider["on_app_message"] is False
        resp = await async_client.patch(f"/api/v1/notifications/{provider['id']}", json={"on_app_message": True})
        assert resp.json()["on_app_message"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_key_scope_round_trips_and_defaults_off(self, async_client: AsyncClient):
        token = await _admin_token(async_client)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await async_client.post("/api/v1/api-keys/", headers=headers, json={"name": "orders"})
        assert resp.json()["can_send_notifications"] is False
        resp = await async_client.patch(
            f"/api/v1/api-keys/{resp.json()['id']}", headers=headers, json={"can_send_notifications": True}
        )
        assert resp.json()["can_send_notifications"] is True
