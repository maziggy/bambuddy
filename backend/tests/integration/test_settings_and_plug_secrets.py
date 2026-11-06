"""Settings and smart plug secrets are write-only.

Responses show a set secret as a fixed placeholder. Saving the placeholder back
keeps the stored value, an empty value clears it, and a stored secret only goes
to the destination it was entered for: changing the destination needs the
secret entered again. Connection tests fill in a stored secret for those who
may edit it, for its own destination only.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.secret_fields import SECRET_PLACEHOLDER

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

M = SECRET_PLACEHOLDER

SETTINGS_SECRETS = {
    "mqtt_password": "mqtt-pw",
    "ha_token": "ha-tok",
    "prometheus_token": "prom-tok",
    "obico_ml_token": "obico-tok",
}


async def _admin(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "ssadmin", "admin_password": "AdminPass1!"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "ssadmin", "password": "AdminPass1!"})
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


async def _stored_setting(db_session, key: str) -> str | None:
    db_session.expire_all()
    row = await db_session.scalar(select(Settings).where(Settings.key == key))
    return row.value if row else None


async def _seed_settings(client: AsyncClient, admin: dict[str, str]) -> None:
    with patch("backend.app.services.mqtt_relay.mqtt_relay.configure", new_callable=AsyncMock):
        response = await client.put(
            "/api/v1/settings/",
            headers=admin,
            json={
                **SETTINGS_SECRETS,
                "mqtt_broker": "broker.example",
                "mqtt_port": 1883,
                "mqtt_use_tls": False,
                "ha_url": "http://ha.example:8123",
                "obico_ml_url": "http://obico.example:3333",
            },
        )
    assert response.status_code == 200, response.text


async def _put_settings(client: AsyncClient, headers: dict[str, str], body: dict):
    with patch("backend.app.services.mqtt_relay.mqtt_relay.configure", new_callable=AsyncMock):
        return await client.put("/api/v1/settings/", headers=headers, json=body)


# --- settings ---


async def test_a_settings_reader_gets_placeholders(async_client: AsyncClient):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)
    viewer = await _user(async_client, admin, "sviewer", ["settings:read"])

    shown = (await async_client.get("/api/v1/settings/", headers=viewer)).json()
    saved_response = (await _put_settings(async_client, admin, {"date_format": "iso"})).json()

    for key in SETTINGS_SECRETS:
        assert shown[key] == M, key
        assert saved_response[key] == M, key


async def test_an_unset_settings_secret_is_shown_empty(async_client: AsyncClient):
    admin = await _admin(async_client)

    shown = (await async_client.get("/api/v1/settings/", headers=admin)).json()

    assert shown["mqtt_password"] == ""


async def test_settings_saved_back_as_shown_keep_their_secrets(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)
    shown = (await async_client.get("/api/v1/settings/", headers=admin)).json()

    response = await _put_settings(async_client, admin, shown)

    assert response.status_code == 200, response.text
    for key, value in SETTINGS_SECRETS.items():
        assert await _stored_setting(db_session, key) == value, key


async def test_an_empty_settings_secret_clears_it(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)

    await _put_settings(async_client, admin, {"prometheus_token": ""})

    assert await _stored_setting(db_session, "prometheus_token") == ""


@pytest.mark.parametrize(
    ("secret", "change"),
    [
        ("mqtt_password", {"mqtt_broker": "broker.elsewhere.example"}),
        ("mqtt_password", {"mqtt_port": 8883}),
        ("mqtt_password", {"mqtt_use_tls": True}),
        ("ha_token", {"ha_url": "http://ha.elsewhere.example:8123"}),
        ("obico_ml_token", {"obico_ml_url": "http://obico.elsewhere.example:3333"}),
    ],
)
@pytest.mark.parametrize("secret_sent", [True, False])
async def test_a_kept_settings_secret_does_not_follow_a_new_destination(
    async_client: AsyncClient, db_session, secret, change, secret_sent
):
    """Kept means sent as the placeholder, or not sent at all."""
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)

    body = {**change, **({secret: M} if secret_sent else {})}
    response = await _put_settings(async_client, admin, body)

    assert response.status_code == 400, response.text
    field = next(iter(change))
    shown_before = {"mqtt_broker": "broker.example", "mqtt_port": "1883", "mqtt_use_tls": "false"}
    assert (
        await _stored_setting(db_session, field)
        == {
            **shown_before,
            "ha_url": "http://ha.example:8123",
            "obico_ml_url": "http://obico.example:3333",
        }[field]
    )


async def test_a_new_settings_destination_with_its_secret_is_saved(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)

    response = await _put_settings(
        async_client, admin, {"ha_url": "http://ha.new.example:8123", "ha_token": "new-ha-tok"}
    )

    assert response.status_code == 200, response.text
    assert await _stored_setting(db_session, "ha_token") == "new-ha-tok"


async def test_the_placeholder_cannot_stand_for_a_settings_secret_never_set(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)

    response = await _put_settings(async_client, admin, {"prometheus_token": M})

    assert response.status_code == 400, response.text
    assert not await _stored_setting(db_session, "prometheus_token")


async def test_an_api_key_reader_still_gets_secrets_blank(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)
    key = await async_client.post("/api/v1/api-keys/", headers=admin, json={"name": "reader", "can_read_status": True})
    assert key.status_code in (200, 201), key.text
    api_key = key.json()["key"]

    shown = await async_client.get("/api/v1/settings/", headers={"X-API-Key": api_key})

    assert shown.status_code == 200, shown.text
    for name in SETTINGS_SECRETS:
        assert shown.json()[name] in ("", M), name


# --- Home Assistant connection test ---


async def _ha_test(client: AsyncClient, headers: dict[str, str], body: dict):
    with patch(
        "backend.app.api.routes.smart_plugs.homeassistant_service.test_connection",
        new_callable=AsyncMock,
        return_value={"success": True, "message": "ok", "error": None},
    ) as test:
        response = await client.post("/api/v1/smart-plugs/ha/test-connection", headers=headers, json=body)
    return response, test


async def test_the_ha_test_uses_the_stored_token_for_the_stored_url(async_client: AsyncClient):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)

    response, test = await _ha_test(async_client, admin, {"url": "http://ha.example:8123", "token": M})

    assert response.status_code == 200, response.text
    test.assert_awaited_once_with("http://ha.example:8123", "ha-tok")


async def test_the_ha_test_does_not_send_the_stored_token_elsewhere(async_client: AsyncClient):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)

    response, test = await _ha_test(async_client, admin, {"url": "http://ha.elsewhere.example:8123", "token": M})

    assert response.status_code == 400, response.text
    test.assert_not_awaited()


async def test_the_ha_test_with_the_stored_token_needs_settings_update(async_client: AsyncClient):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)
    controller = await _user(async_client, admin, "plugctl", ["smart_plugs:control", "settings:read"])

    response, test = await _ha_test(async_client, controller, {"url": "http://ha.example:8123", "token": M})

    assert response.status_code == 403, response.text
    test.assert_not_awaited()


# --- smart plugs ---


TASMOTA = {"name": "T", "plug_type": "tasmota", "ip_address": "10.0.0.5", "username": "admin", "password": "tas-pw"}
REST = {
    "name": "R",
    "plug_type": "rest",
    "rest_on_url": "http://plug.example/on",
    "rest_off_url": "http://plug.example/off",
    "rest_headers": '{"Authorization": "Bearer rest-tok"}',
}


async def _create_plug(client: AsyncClient, headers: dict[str, str], body: dict) -> dict:
    response = await client.post("/api/v1/smart-plugs/", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def _stored_plug(db_session, plug_id: int) -> SmartPlug:
    db_session.expire_all()
    return await db_session.get(SmartPlug, plug_id)


async def test_a_plug_reader_gets_placeholders(async_client: AsyncClient):
    admin = await _admin(async_client)
    viewer = await _user(async_client, admin, "pviewer", ["smart_plugs:read"])
    tasmota = await _create_plug(async_client, admin, TASMOTA)
    rest = await _create_plug(async_client, admin, REST)

    listed = {p["id"]: p for p in (await async_client.get("/api/v1/smart-plugs/", headers=viewer)).json()}
    single = (await async_client.get(f"/api/v1/smart-plugs/{tasmota['id']}", headers=viewer)).json()

    assert tasmota["password"] == M and rest["rest_headers"] == M
    assert listed[tasmota["id"]]["password"] == M
    assert listed[rest["id"]]["rest_headers"] == M
    assert single["password"] == M


async def test_a_plug_saved_back_as_shown_keeps_its_secrets(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    tasmota = await _create_plug(async_client, admin, TASMOTA)
    rest = await _create_plug(async_client, admin, REST)

    for plug in (tasmota, rest):
        body = {
            k: plug[k]
            for k in (
                "name",
                "plug_type",
                "ip_address",
                "username",
                "password",
                "rest_on_url",
                "rest_off_url",
                "rest_headers",
            )
        }
        response = await async_client.patch(f"/api/v1/smart-plugs/{plug['id']}", headers=admin, json=body)
        assert response.status_code == 200, response.text

    assert (await _stored_plug(db_session, tasmota["id"])).password == "tas-pw"
    assert (await _stored_plug(db_session, rest["id"])).rest_headers == REST["rest_headers"]


@pytest.mark.parametrize(
    ("plug", "change"),
    [
        (TASMOTA, {"ip_address": "10.0.0.66"}),
        (REST, {"rest_on_url": "http://elsewhere.example/on"}),
        (REST, {"rest_status_url": "http://elsewhere.example/status"}),
        (TASMOTA, {"plug_type": "rest", "rest_on_url": "http://elsewhere.example/on"}),
    ],
)
@pytest.mark.parametrize("secret_sent", [True, False])
async def test_a_kept_plug_secret_does_not_follow_a_new_destination(
    async_client: AsyncClient, db_session, plug, change, secret_sent
):
    admin = await _admin(async_client)
    created = await _create_plug(async_client, admin, plug)
    secret = "password" if plug is TASMOTA else "rest_headers"

    body = {**change, **({secret: M} if secret_sent else {})}
    response = await async_client.patch(f"/api/v1/smart-plugs/{created['id']}", headers=admin, json=body)

    assert response.status_code == 400, response.text
    stored = await _stored_plug(db_session, created["id"])
    assert getattr(stored, secret) == plug[secret]
    assert stored.plug_type == plug["plug_type"]


async def test_a_new_plug_destination_with_its_secret_is_saved(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    created = await _create_plug(async_client, admin, TASMOTA)

    response = await async_client.patch(
        f"/api/v1/smart-plugs/{created['id']}", headers=admin, json={"ip_address": "10.0.0.7", "password": "new-pw"}
    )

    assert response.status_code == 200, response.text
    assert (await _stored_plug(db_session, created["id"])).password == "new-pw"


async def test_a_new_plug_cannot_take_the_placeholder_as_its_secret(async_client: AsyncClient):
    admin = await _admin(async_client)

    response = await async_client.post("/api/v1/smart-plugs/", headers=admin, json={**TASMOTA, "password": M})

    assert response.status_code == 400, response.text


async def test_the_tasmota_test_uses_the_stored_password_for_its_own_ip(async_client: AsyncClient):
    admin = await _admin(async_client)
    created = await _create_plug(async_client, admin, TASMOTA)

    with patch(
        "backend.app.api.routes.smart_plugs.tasmota_service.test_connection",
        new_callable=AsyncMock,
        return_value={"success": True, "state": "ON"},
    ) as test:
        same = await async_client.post(
            "/api/v1/smart-plugs/test-connection",
            headers=admin,
            json={"ip_address": "10.0.0.5", "username": "admin", "password": M, "plug_id": created["id"]},
        )
        moved = await async_client.post(
            "/api/v1/smart-plugs/test-connection",
            headers=admin,
            json={"ip_address": "10.0.0.66", "username": "admin", "password": M, "plug_id": created["id"]},
        )

    assert same.status_code == 200, same.text
    test.assert_awaited_once_with("10.0.0.5", "admin", "tas-pw")
    assert moved.status_code == 400, moved.text


async def test_a_plug_test_with_a_stored_secret_needs_plug_update(async_client: AsyncClient):
    admin = await _admin(async_client)
    controller = await _user(async_client, admin, "pctl", ["smart_plugs:read", "smart_plugs:control"])
    created = await _create_plug(async_client, admin, REST)

    with patch(
        "backend.app.api.routes.smart_plugs.rest_smart_plug_service.test_connection",
        new_callable=AsyncMock,
        return_value={"success": True},
    ) as test:
        response = await async_client.post(
            "/api/v1/smart-plugs/rest/test-connection",
            headers=controller,
            json={"url": "http://plug.example/on", "method": "POST", "headers": M, "plug_id": created["id"]},
        )

    assert response.status_code == 403, response.text
    test.assert_not_awaited()


async def test_the_rest_test_uses_the_stored_headers_for_its_own_urls_only(async_client: AsyncClient):
    admin = await _admin(async_client)
    created = await _create_plug(async_client, admin, REST)

    with patch(
        "backend.app.api.routes.smart_plugs.rest_smart_plug_service.test_connection",
        new_callable=AsyncMock,
        return_value={"success": True},
    ) as test:
        same = await async_client.post(
            "/api/v1/smart-plugs/rest/test-connection",
            headers=admin,
            json={"url": "http://plug.example/off", "method": "POST", "headers": M, "plug_id": created["id"]},
        )
        moved = await async_client.post(
            "/api/v1/smart-plugs/rest/test-connection",
            headers=admin,
            json={"url": "http://elsewhere.example/x", "method": "POST", "headers": M, "plug_id": created["id"]},
        )

    assert same.status_code == 200, same.text
    test.assert_awaited_once_with("http://plug.example/off", "POST", REST["rest_headers"])
    assert moved.status_code == 400, moved.text


async def test_a_test_without_a_plug_does_not_send_the_placeholder(async_client: AsyncClient):
    admin = await _admin(async_client)

    with patch("backend.app.api.routes.smart_plugs.tasmota_service.test_connection", new_callable=AsyncMock) as test:
        response = await async_client.post(
            "/api/v1/smart-plugs/test-connection",
            headers=admin,
            json={"ip_address": "10.0.0.5", "username": "admin", "password": M},
        )

    assert response.status_code == 400, response.text
    test.assert_not_awaited()


async def test_a_plug_test_cannot_use_the_secret_of_a_plug_outside_ones_printers(
    async_client: AsyncClient, printer_factory
):
    admin = await _admin(async_client)
    mine, theirs = await printer_factory(name="Mine"), await printer_factory(name="Theirs")
    group = await async_client.post(
        "/api/v1/groups/",
        headers=admin,
        json={
            "name": "g_scoped",
            "permissions": ["smart_plugs:read", "smart_plugs:control", "smart_plugs:update"],
            "restrict_printers": True,
            "printer_ids": [mine.id],
        },
    )
    assert group.status_code in (200, 201), group.text
    await async_client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": "scoped", "password": "UserPass1!", "group_ids": [group.json()["id"]]},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "scoped", "password": "UserPass1!"})
    scoped = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = await _create_plug(async_client, admin, {**TASMOTA, "printer_id": theirs.id})

    with patch("backend.app.api.routes.smart_plugs.tasmota_service.test_connection", new_callable=AsyncMock) as test:
        response = await async_client.post(
            "/api/v1/smart-plugs/test-connection",
            headers=scoped,
            json={"ip_address": "10.0.0.5", "username": "admin", "password": M, "plug_id": created["id"]},
        )

    assert response.status_code == 404, response.text
    test.assert_not_awaited()


# --- Obico connection test ---


async def _obico_test(client: AsyncClient, headers: dict[str, str], body: dict):
    with patch(
        "backend.app.api.routes.obico.obico_detection_service.test_connection",
        new_callable=AsyncMock,
        return_value={"ok": True, "status_code": 200, "body": "", "error": None, "auth_ok": True},
    ) as test:
        response = await client.post("/api/v1/obico/test-connection", headers=headers, json=body)
    return response, test


@pytest.mark.parametrize("token", [M, None])
async def test_the_obico_test_uses_the_stored_token_for_the_stored_url_only(async_client: AsyncClient, token):
    """The placeholder, or no token at all, means the stored token."""
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)
    body = {} if token is None else {"token": token}

    same, test = await _obico_test(async_client, admin, {"url": "http://obico.example:3333", **body})
    assert same.status_code == 200, same.text
    test.assert_awaited_once_with("http://obico.example:3333", "obico-tok")

    moved, test = await _obico_test(async_client, admin, {"url": "http://obico.elsewhere.example:3333", **body})
    assert moved.status_code == 400, moved.text
    test.assert_not_awaited()


async def test_the_obico_test_with_a_typed_token_tests_that_token(async_client: AsyncClient):
    admin = await _admin(async_client)
    await _seed_settings(async_client, admin)

    response, test = await _obico_test(
        async_client, admin, {"url": "http://obico.elsewhere.example:3333", "token": "typed"}
    )

    assert response.status_code == 200, response.text
    test.assert_awaited_once_with("http://obico.elsewhere.example:3333", "typed")


# --- round 2: env HA token, SMTP, LDAP, email security, REST header logs ---


async def test_an_env_ha_token_lets_the_url_change_and_is_never_overwritten(
    async_client: AsyncClient, db_session, monkeypatch
):
    """A token from HA_TOKEN is the deployment's, not a stored secret to re-enter:
    the URL may change, and the placeholder sent back is not written anywhere."""
    admin = await _admin(async_client)
    monkeypatch.setenv("HA_TOKEN", "env-ha-tok")
    monkeypatch.delenv("HA_URL", raising=False)

    response = await _put_settings(async_client, admin, {"ha_url": "http://ha.new.example:8123", "ha_token": M})

    assert response.status_code == 200, response.text
    assert await _stored_setting(db_session, "ha_url") == "http://ha.new.example:8123"
    assert await _stored_setting(db_session, "ha_token") in (None, "")


async def test_a_kept_smtp_password_does_not_follow_a_new_host(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    smtp = {
        "smtp_host": "smtp.example",
        "smtp_port": 587,
        "smtp_username": "u",
        "smtp_password": "smtp-pw",
        "smtp_security": "starttls",
        "smtp_auth_enabled": True,
        "smtp_from_email": "a@example.com",
    }
    assert (await async_client.post("/api/v1/auth/smtp", headers=admin, json=smtp)).status_code == 200

    for change in ({"smtp_host": "smtp.elsewhere.example"}, {"smtp_port": 25}, {"smtp_security": "none"}):
        moved = await async_client.post(
            "/api/v1/auth/smtp", headers=admin, json={**smtp, "smtp_password": None, **change}
        )
        assert moved.status_code == 400, (change, moved.text)
    assert await _stored_setting(db_session, "smtp_host") == "smtp.example"

    kept = await async_client.post("/api/v1/auth/smtp", headers=admin, json={**smtp, "smtp_password": None})
    assert kept.status_code == 200, kept.text
    assert await _stored_setting(db_session, "smtp_password") == "smtp-pw"

    entered = await async_client.post(
        "/api/v1/auth/smtp", headers=admin, json={**smtp, "smtp_host": "smtp.new.example", "smtp_password": "new-pw"}
    )
    assert entered.status_code == 200, entered.text
    assert await _stored_setting(db_session, "smtp_password") == "new-pw"


async def test_a_new_smtp_host_without_authentication_drops_the_stored_password(async_client: AsyncClient, db_session):
    """Nothing to log in with now; the stored password must not be there for later."""
    admin = await _admin(async_client)
    smtp = {
        "smtp_host": "smtp.example",
        "smtp_port": 587,
        "smtp_username": "u",
        "smtp_password": "smtp-pw",
        "smtp_security": "starttls",
        "smtp_auth_enabled": True,
        "smtp_from_email": "a@example.com",
    }
    await async_client.post("/api/v1/auth/smtp", headers=admin, json=smtp)

    response = await async_client.post(
        "/api/v1/auth/smtp",
        headers=admin,
        json={**smtp, "smtp_host": "relay.example", "smtp_password": None, "smtp_auth_enabled": False},
    )

    assert response.status_code == 200, response.text
    assert not await _stored_setting(db_session, "smtp_password")


async def test_a_kept_ldap_bind_password_does_not_follow_a_new_server(async_client: AsyncClient, db_session):
    admin = await _admin(async_client)
    await _put_settings(
        async_client,
        admin,
        {"ldap_server_url": "ldaps://ldap.example", "ldap_security": "ldaps", "ldap_bind_password": "bind-pw"},
    )

    for change in ({"ldap_server_url": "ldap://ldap.elsewhere.example"}, {"ldap_security": "starttls"}):
        response = await _put_settings(async_client, admin, change)
        assert response.status_code == 400, (change, response.text)
    assert await _stored_setting(db_session, "ldap_server_url") == "ldaps://ldap.example"

    response = await _put_settings(
        async_client, admin, {"ldap_server_url": "ldaps://ldap.new.example", "ldap_bind_password": "new-bind"}
    )
    assert response.status_code == 200, response.text
    assert await _stored_setting(db_session, "ldap_bind_password") == "new-bind"


async def test_malformed_rest_headers_are_not_logged(caplog):
    from backend.app.services.rest_smart_plug import rest_smart_plug_service

    with caplog.at_level("DEBUG"):
        assert rest_smart_plug_service._parse_headers('{"Authorization": "Bearer leak-me",}') == {}

    assert "leak-me" not in caplog.text
    assert "REST headers" in caplog.text
