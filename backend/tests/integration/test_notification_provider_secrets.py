"""Notification provider secrets are write-only.

Provider responses return every secret field as a fixed placeholder. Sending
the placeholder back keeps the stored value, an empty value clears it, and a
stored secret only goes to the destination it was entered for: changing the
destination needs the secret entered again. Testing an edited provider fills
in the stored secrets for those who may edit it.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.notification import NotificationProvider
from backend.app.services.secret_fields import SECRET_PLACEHOLDER

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

M = SECRET_PLACEHOLDER

# provider type -> (config with every secret set, its secret fields)
CONFIGS = {
    "callmebot": ({"phone": "+100", "apikey": "cmb-key"}, ["apikey"]),
    "ntfy": ({"server": "https://ntfy.example", "topic": "t", "auth_token": "ntfy-tok"}, ["auth_token"]),
    "pushover": ({"user_key": "po-user", "app_token": "po-app", "priority": "0"}, ["user_key", "app_token"]),
    "telegram": ({"bot_token": "123:tg", "chat_id": "42"}, ["bot_token"]),
    "email": (
        {
            "smtp_server": "smtp.example",
            "smtp_port": "587",
            "security": "starttls",
            "auth_enabled": "true",
            "username": "u",
            "password": "mail-pw",
            "from_email": "a@example.com",
            "to_email": "b@example.com",
        },
        ["password"],
    ),
    "discord": ({"webhook_url": "https://discord.example/api/webhooks/1/abc"}, ["webhook_url"]),
    "webhook": (
        {"webhook_url": "https://hooks.example/x?key=k", "auth_header": "Bearer wh", "payload_format": "generic"},
        ["webhook_url", "auth_header"],
    ),
    "bark": ({"device_key": "bark-key", "server": "https://bark.example"}, ["device_key"]),
    "gotify": ({"server": "https://gotify.example", "app_token": "go-tok"}, ["app_token"]),
    "notify": ({"device_id": "IO12345678901234", "token": "notify-tok"}, ["token"]),
    "homeassistant": ({"service": "notify.mobile"}, []),
}


async def _admin(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "nsadmin", "admin_password": "AdminPass1!"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "nsadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user(client: AsyncClient, admin: dict[str, str], name: str, permissions: list[str]) -> dict[str, str]:
    group = await client.post("/api/v1/groups/", headers=admin, json={"name": f"g_{name}", "permissions": permissions})
    assert group.status_code in (200, 201), group.text
    created = await client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": name, "password": "UserPass1!", "group_ids": [group.json()["id"]]},
    )
    assert created.status_code in (200, 201), created.text
    login = await client.post("/api/v1/auth/login", json={"username": name, "password": "UserPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _create(client: AsyncClient, headers: dict[str, str], provider_type: str, config: dict) -> dict:
    with (
        patch("backend.app.api.routes.notifications.notify_live_activities.schedule_cleanup", new_callable=AsyncMock),
        patch("backend.app.api.routes.notifications.notify_widgets.schedule_cleanup", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/v1/notifications/",
            headers=headers,
            json={"name": provider_type, "provider_type": provider_type, "config": config},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _stored(db_session, provider_id: int) -> dict:
    db_session.expire_all()
    provider = await db_session.scalar(select(NotificationProvider).where(NotificationProvider.id == provider_id))
    return json.loads(provider.config)


async def _patch(client: AsyncClient, headers: dict[str, str], provider_id: int, body: dict):
    with (
        patch(
            "backend.app.api.routes.notifications.notify_live_activities.schedule_cleanup", new_callable=AsyncMock
        ) as live_cleanup,
        patch("backend.app.api.routes.notifications.notify_widgets.schedule_cleanup", new_callable=AsyncMock),
    ):
        response = await client.patch(f"/api/v1/notifications/{provider_id}", headers=headers, json=body)
    return response, live_cleanup


@pytest.mark.parametrize("provider_type", sorted(CONFIGS))
async def test_a_reader_gets_placeholders_for_every_secret(async_client: AsyncClient, provider_type):
    config, secrets = CONFIGS[provider_type]
    admin = await _admin(async_client)
    viewer = await _user(async_client, admin, "viewer", ["notifications:read"])
    created = await _create(async_client, admin, provider_type, config)

    listed = (await async_client.get("/api/v1/notifications/", headers=viewer)).json()
    single = (await async_client.get(f"/api/v1/notifications/{created['id']}", headers=viewer)).json()

    expected = {key: (M if key in secrets else value) for key, value in config.items()}
    assert created["config"] == expected
    assert listed[0]["config"] == expected
    assert single["config"] == expected


async def test_an_unset_secret_is_not_shown_as_set(async_client: AsyncClient):
    admin = await _admin(async_client)
    created = await _create(
        async_client, admin, "ntfy", {"server": "https://ntfy.example", "topic": "t", "auth_token": ""}
    )

    assert created["config"]["auth_token"] == ""


async def test_sending_the_placeholder_back_keeps_the_secret(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "telegram", CONFIGS["telegram"][0])

    response, _ = await _patch(async_client, admin, created["id"], {"config": {**created["config"], "chat_id": "43"}})

    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"bot_token": M, "chat_id": "43"}
    assert await _stored(db_session, created["id"]) == {"bot_token": "123:tg", "chat_id": "43"}


@pytest.mark.parametrize("provider_type", sorted(CONFIGS))
async def test_every_type_saved_back_as_shown_stays_as_it_was(async_client: AsyncClient, db_session, provider_type):
    """The edit dialog sends the config back as it was shown."""
    admin = await _admin(async_client)
    created = await _create(async_client, admin, provider_type, CONFIGS[provider_type][0])

    response, _ = await _patch(async_client, admin, created["id"], {"config": created["config"]})

    assert response.status_code == 200, response.text
    assert await _stored(db_session, created["id"]) == CONFIGS[provider_type][0]


async def test_the_placeholder_cannot_stand_for_a_secret_never_set(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    created = await _create(
        async_client, admin, "ntfy", {"server": "https://ntfy.example", "topic": "t", "auth_token": ""}
    )

    response, _ = await _patch(async_client, admin, created["id"], {"config": {**created["config"], "auth_token": M}})

    assert response.status_code == 400, response.text
    assert (await _stored(db_session, created["id"]))["auth_token"] == ""


async def test_an_empty_secret_clears_it_and_a_new_one_replaces_it(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "ntfy", CONFIGS["ntfy"][0])

    await _patch(async_client, admin, created["id"], {"config": {**created["config"], "auth_token": ""}})
    assert (await _stored(db_session, created["id"]))["auth_token"] == ""

    await _patch(async_client, admin, created["id"], {"config": {**created["config"], "auth_token": "fresh"}})
    assert (await _stored(db_session, created["id"]))["auth_token"] == "fresh"


@pytest.mark.parametrize(
    ("provider_type", "change"),
    [
        ("email", {"smtp_server": "smtp.elsewhere.example"}),
        ("email", {"smtp_port": "25"}),
        ("email", {"security": "none"}),
        ("ntfy", {"server": "https://ntfy.elsewhere.example"}),
        ("gotify", {"server": "https://gotify.elsewhere.example"}),
        ("bark", {"server": "https://bark.elsewhere.example"}),
        ("webhook", {"webhook_url": "https://elsewhere.example/hook"}),
    ],
)
async def test_a_kept_secret_does_not_follow_a_new_destination(
    async_client: AsyncClient, db_session, provider_type, change
):
    admin = await _admin(async_client)
    created = await _create(async_client, admin, provider_type, CONFIGS[provider_type][0])

    response, _ = await _patch(async_client, admin, created["id"], {"config": {**created["config"], **change}})

    assert response.status_code == 400, response.text
    assert await _stored(db_session, created["id"]) == CONFIGS[provider_type][0]


async def test_a_new_destination_with_its_secret_entered_again_is_saved(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "email", CONFIGS["email"][0])

    response, _ = await _patch(
        async_client,
        admin,
        created["id"],
        {"config": {**created["config"], "smtp_server": "smtp.new.example", "password": "new-pw"}},
    )

    assert response.status_code == 200, response.text
    stored = await _stored(db_session, created["id"])
    assert stored["smtp_server"] == "smtp.new.example" and stored["password"] == "new-pw"


async def test_a_kept_secret_does_not_follow_a_new_provider_type(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "webhook", CONFIGS["webhook"][0])

    response, _ = await _patch(
        async_client,
        admin,
        created["id"],
        {"provider_type": "discord", "config": {"webhook_url": M}},
    )

    assert response.status_code == 400, response.text
    assert await _stored(db_session, created["id"]) == CONFIGS["webhook"][0]


async def test_the_placeholder_is_not_a_secret_of_its_own(async_client: AsyncClient):
    admin = await _admin(async_client)

    response = await async_client.post(
        "/api/v1/notifications/",
        headers=admin,
        json={"name": "tg", "provider_type": "telegram", "config": {"bot_token": M, "chat_id": "42"}},
    )

    assert response.status_code == 400, response.text


async def test_a_notify_provider_kept_as_is_is_not_cleaned_up(async_client: AsyncClient):
    """Its live activities and widgets are only cleaned up when its device or
    token changed; the placeholder in the response is not a change."""
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "notify", CONFIGS["notify"][0])

    response, live_cleanup = await _patch(async_client, admin, created["id"], {"config": created["config"]})

    assert response.status_code == 200, response.text
    live_cleanup.assert_not_awaited()


# --- testing an edited provider ---


async def _test_config(client: AsyncClient, headers: dict[str, str], body: dict):
    with patch(
        "backend.app.api.routes.notifications.notification_service.send_test_notification",
        new_callable=AsyncMock,
        return_value=(True, "ok"),
    ) as send:
        response = await client.post("/api/v1/notifications/test-config", headers=headers, json=body)
    return response, send


async def test_an_editor_tests_with_the_stored_secret(async_client: AsyncClient):
    admin = await _admin(async_client)
    editor = await _user(
        async_client, admin, "editor", ["notifications:read", "notifications:create", "notifications:update"]
    )
    created = await _create(async_client, admin, "telegram", CONFIGS["telegram"][0])

    response, send = await _test_config(
        async_client,
        editor,
        {"provider_type": "telegram", "config": created["config"], "provider_id": created["id"]},
    )

    assert response.status_code == 200, response.text
    assert send.await_args.args[1] == {"bot_token": "123:tg", "chat_id": "42"}


async def test_someone_who_may_only_create_cannot_test_with_a_stored_secret(async_client: AsyncClient):
    admin = await _admin(async_client)
    creator = await _user(async_client, admin, "creator", ["notifications:read", "notifications:create"])
    created = await _create(async_client, admin, "telegram", CONFIGS["telegram"][0])

    response, send = await _test_config(
        async_client,
        creator,
        {"provider_type": "telegram", "config": created["config"], "provider_id": created["id"]},
    )

    assert response.status_code == 403, response.text
    send.assert_not_awaited()


async def test_a_test_does_not_send_a_stored_secret_to_a_new_destination(async_client: AsyncClient):
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "gotify", CONFIGS["gotify"][0])

    response, send = await _test_config(
        async_client,
        admin,
        {
            "provider_type": "gotify",
            "config": {**created["config"], "server": "https://gotify.elsewhere.example"},
            "provider_id": created["id"],
        },
    )

    assert response.status_code == 400, response.text
    send.assert_not_awaited()


async def test_a_test_without_a_provider_does_not_send_the_placeholder(async_client: AsyncClient):
    admin = await _admin(async_client)

    response, send = await _test_config(
        async_client, admin, {"provider_type": "telegram", "config": {"bot_token": M, "chat_id": "42"}}
    )

    assert response.status_code == 400, response.text
    send.assert_not_awaited()


async def test_a_new_provider_is_tested_as_entered(async_client: AsyncClient):
    admin = await _admin(async_client)

    response, send = await _test_config(
        async_client, admin, {"provider_type": "telegram", "config": {"bot_token": "123:tg", "chat_id": "42"}}
    )

    assert response.status_code == 200, response.text
    assert send.await_args.args[1] == {"bot_token": "123:tg", "chat_id": "42"}


async def test_a_type_change_needs_a_config_of_its_own(async_client: AsyncClient, db_session):
    """Changing only the type would keep the stored secrets under a type that
    neither masks nor guards them."""
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "email", CONFIGS["email"][0])

    response, _ = await _patch(async_client, admin, created["id"], {"provider_type": "homeassistant"})

    assert response.status_code == 400, response.text
    db_session.expire_all()
    provider = await db_session.get(NotificationProvider, created["id"])
    assert provider.provider_type == "email"


async def test_a_secret_named_for_another_type_is_masked_too(async_client: AsyncClient, db_session):
    """Leftover keys a provider's type does not use are still not shown, and
    saving them back as shown keeps them."""
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "homeassistant", {"service": "notify.x", "password": "left-over"})

    assert created["config"] == {"service": "notify.x", "password": M}
    response, _ = await _patch(async_client, admin, created["id"], {"config": created["config"]})
    assert response.status_code == 200, response.text
    assert (await _stored(db_session, created["id"]))["password"] == "left-over"


@pytest.mark.parametrize(
    ("stored", "sent"),
    [({"smtp_port": 587}, {"smtp_port": "587"}), ({"smtp_port": None}, {"smtp_port": ""})],
)
async def test_an_unchanged_destination_in_another_spelling_is_not_a_change(
    async_client: AsyncClient, db_session, stored, sent
):
    """The edit dialog sends every value back as text."""
    admin = await _admin(async_client)
    created = await _create(async_client, admin, "email", {**CONFIGS["email"][0], **stored})

    response, _ = await _patch(async_client, admin, created["id"], {"config": {**created["config"], **sent}})

    assert response.status_code == 200, response.text
    assert (await _stored(db_session, created["id"]))["password"] == "mail-pw"


@pytest.mark.parametrize("security", ["", " starttls", "none"])
async def test_a_kept_smtp_password_is_not_sent_with_weaker_security(async_client: AsyncClient, db_session, security):
    """With no security stored, the sender uses STARTTLS; anything else it reads
    as plain SMTP, which would send the stored password unencrypted."""
    admin = await _admin(async_client)
    config = {key: value for key, value in CONFIGS["email"][0].items() if key != "security"}
    created = await _create(async_client, admin, "email", config)

    response, _ = await _patch(
        async_client, admin, created["id"], {"config": {**created["config"], "security": security}}
    )

    assert response.status_code == 400, response.text
    assert await _stored(db_session, created["id"]) == config
