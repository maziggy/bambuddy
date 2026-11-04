"""A supplied API key remains a scoped principal when user login is off."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.core.auth import generate_api_key, invalidate_auth_enabled_cache
from backend.app.core.config import settings
from backend.app.models.api_key import APIKey
from backend.app.models.group import Group
from backend.app.models.settings import Settings
from backend.app.models.user import User


async def _set_user_auth(db_session, enabled):
    row = await db_session.get(Settings, "auth_enabled")
    if row is None:
        db_session.add(Settings(key="auth_enabled", value="true" if enabled else "false"))
    else:
        row.value = "true" if enabled else "false"
    await db_session.commit()
    invalidate_auth_enabled_cache()


async def _key(db_session, **overrides):
    value, key_hash, key_prefix = generate_api_key()
    fields = {
        "name": "integration-test",
        "key_hash": key_hash,
        "key_prefix": key_prefix,
        "enabled": True,
        "can_read_status": True,
        "can_control_printer": False,
        "can_queue": False,
        "can_manage_library": False,
        "can_manage_inventory": False,
        "can_manage_maintenance": False,
        "can_manage_archives": False,
        "can_manage_projects": False,
    }
    fields.update(overrides)
    db_session.add(APIKey(**fields))
    await db_session.commit()
    return value


@pytest.fixture
def enforce_keys(monkeypatch):
    monkeypatch.setattr(settings, "api_key_auth_when_auth_disabled", True)


@pytest.mark.integration
@pytest.mark.parametrize("native_auth", [False, True])
async def test_key_scopes_and_printer_secrets_in_both_modes(
    async_client, db_session, printer_factory, enforce_keys, native_auth
):
    visible = await printer_factory(name="Allowed", access_code="secret-one")
    hidden = await printer_factory(name="Not allowed", access_code="secret-two")
    await _set_user_auth(db_session, native_auth)
    key = await _key(db_session, printer_ids=[visible.id])
    headers = {"X-API-Key": key}

    response = await async_client.get("/api/v1/printers/", headers=headers)
    assert response.status_code == 200, response.text
    assert [printer["id"] for printer in response.json()] == [visible.id]
    assert "access_code" not in response.json()[0]

    # A read-only key cannot control hardware, create keys or read a printer
    # outside its allowlist. These requests are denied before any action runs.
    assert (await async_client.post(f"/api/v1/printers/{visible.id}/print/stop", headers=headers)).status_code == 403
    assert (await async_client.post("/api/v1/api-keys/", headers=headers, json={"name": "other"})).status_code == 403
    assert (await async_client.get(f"/api/v1/printers/{hidden.id}", headers=headers)).status_code == 404


@pytest.mark.integration
@pytest.mark.parametrize("native_auth", [False, True])
@pytest.mark.parametrize("transport", ["header", "bearer"])
async def test_invalid_key_is_rejected_in_both_modes(async_client, db_session, enforce_keys, native_auth, transport):
    await _set_user_auth(db_session, native_auth)
    headers = {"X-API-Key": "bb_invalid"} if transport == "header" else {"Authorization": "Bearer bb_invalid"}
    response = await async_client.get("/api/v1/printers/", headers=headers)
    assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.parametrize("native_auth", [False, True])
async def test_valid_bearer_key_uses_the_same_scopes(
    async_client, db_session, printer_factory, enforce_keys, native_auth
):
    await _set_user_auth(db_session, native_auth)
    printer = await printer_factory(access_code="local-secret")
    key = await _key(db_session)
    headers = {"Authorization": f"Bearer {key}"}
    response = await async_client.get("/api/v1/printers/", headers=headers)
    assert response.status_code == 200
    assert "access_code" not in response.json()[0]
    assert (await async_client.post(f"/api/v1/printers/{printer.id}/print/stop", headers=headers)).status_code == 403


@pytest.mark.integration
@pytest.mark.parametrize("native_auth", [False, True])
@pytest.mark.parametrize("state", ["disabled", "expired"])
async def test_revoked_or_expired_key_is_rejected(async_client, db_session, enforce_keys, native_auth, state):
    await _set_user_auth(db_session, native_auth)
    key = await _key(
        db_session,
        enabled=state != "disabled",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1) if state == "expired" else None,
    )
    response = await async_client.get("/api/v1/printers/", headers={"X-API-Key": key})
    assert response.status_code == 401


@pytest.mark.integration
async def test_keyless_auth_disabled_behavior_is_preserved(async_client, db_session, printer_factory, enforce_keys):
    await _set_user_auth(db_session, False)
    await printer_factory(access_code="local-secret")
    response = await async_client.get("/api/v1/printers/")
    assert response.status_code == 200
    assert response.json()[0]["access_code"] == "local-secret"
    assert (await async_client.get("/api/v1/auth/status")).json()["auth_enabled"] is False


@pytest.mark.integration
async def test_disabled_option_preserves_legacy_behavior(async_client, db_session, printer_factory, monkeypatch):
    monkeypatch.setattr(settings, "api_key_auth_when_auth_disabled", False)
    await _set_user_auth(db_session, False)
    await printer_factory(access_code="local-secret")
    response = await async_client.get("/api/v1/printers/", headers={"X-API-Key": "bb_invalid"})
    assert response.status_code == 200
    assert response.json()[0]["access_code"] == "local-secret"


@pytest.mark.integration
async def test_key_cannot_use_public_setup_to_become_admin(async_client, db_session, enforce_keys):
    await _set_user_auth(db_session, False)
    key = await _key(db_session)
    response = await async_client.post(
        "/api/v1/auth/setup",
        headers={"X-API-Key": key},
        json={"auth_enabled": True, "admin_username": "unexpected", "admin_password": "TestPassword1!"},
    )
    assert response.status_code == 403
    assert (await async_client.get("/api/v1/auth/status")).json()["auth_enabled"] is False


@pytest.mark.integration
@pytest.mark.parametrize("native_auth", [False, True])
async def test_key_cannot_mint_a_broader_media_token(async_client, db_session, enforce_keys, native_auth):
    await _set_user_auth(db_session, native_auth)
    key = await _key(db_session)
    response = await async_client.post("/api/v1/auth/media-token", headers={"X-API-Key": key})
    assert response.status_code == 403


@pytest.mark.integration
async def test_key_has_no_announcement_inbox_when_user_login_is_off(async_client, db_session, enforce_keys):
    await _set_user_auth(db_session, False)
    key = await _key(db_session)
    response = await async_client.get("/api/v1/announcements", headers={"X-API-Key": key})
    assert response.status_code == 200
    assert response.json() == {"visible": False, "announcements": []}
    assert (
        await async_client.post("/api/v1/announcements/not-an-inbox/read", headers={"X-API-Key": key})
    ).status_code == 404


@pytest.mark.integration
@pytest.mark.parametrize("native_auth", [False, True])
async def test_key_cannot_out_rank_its_owner(async_client, db_session, printer_factory, enforce_keys, native_auth):
    await _set_user_auth(db_session, native_auth)
    printer = await printer_factory()
    group = Group(name="key-reader", permissions=["printers:read"], is_system=False)
    owner = User(username="key-owner", password_hash="unused", role="user", is_active=True, groups=[group])
    db_session.add(owner)
    await db_session.commit()
    key = await _key(db_session, user_id=owner.id, can_control_printer=True)
    headers = {"X-API-Key": key}
    assert (await async_client.get("/api/v1/printers/", headers=headers)).status_code == 200
    assert (await async_client.post(f"/api/v1/printers/{printer.id}/print/stop", headers=headers)).status_code == 403
    owner.is_active = False
    await db_session.commit()
    assert (await async_client.get("/api/v1/printers/", headers=headers)).status_code == 403


@pytest.mark.integration
async def test_special_scope_and_sensitive_settings(async_client, db_session, enforce_keys):
    await _set_user_auth(db_session, False)
    db_session.add(Settings(key="mqtt_password", value="private-test-value"))
    await db_session.commit()
    key = await _key(db_session)
    headers = {"X-API-Key": key}
    settings_response = await async_client.get("/api/v1/settings/", headers=headers)
    assert settings_response.status_code == 200
    assert settings_response.json()["mqtt_password"] == ""
    response = await async_client.post(
        "/api/v1/settings/electricity-price", headers=headers, json={"energy_cost_per_kwh": 0.25}
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_key_without_cloud_access_cannot_use_global_cloud_token(async_client, db_session, enforce_keys):
    await _set_user_auth(db_session, False)
    db_session.add(Settings(key="cloud_token", value="private-test-token"))
    await db_session.commit()
    key = await _key(db_session, can_access_cloud=False)
    response = await async_client.get("/api/v1/makerworld/status", headers={"X-API-Key": key})
    assert response.status_code == 200, response.text
    assert response.json()["has_cloud_token"] is False


@pytest.mark.integration
async def test_media_token_cannot_override_a_key_scope(async_client, db_session, printer_factory, enforce_keys):
    await _set_user_auth(db_session, False)
    printer = await printer_factory()
    key = await _key(db_session, can_read_status=False)
    response = await async_client.get(
        f"/api/v1/printers/{printer.id}/cover?token=invalid",
        headers={"X-API-Key": key},
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_token_only_camera_route_does_not_accept_a_key(async_client, db_session, printer_factory, enforce_keys):
    await _set_user_auth(db_session, False)
    printer = await printer_factory()
    key = await _key(db_session)
    response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/stream", headers={"X-API-Key": key})
    assert response.status_code == 403


@pytest.mark.integration
async def test_ambiguous_key_headers_are_rejected(async_client, db_session, enforce_keys):
    await _set_user_auth(db_session, False)
    key = await _key(db_session)
    for headers in (
        [("X-API-Key", key), ("X-API-Key", "bb_invalid")],
        [("Authorization", f"Bearer {key}"), ("Authorization", "Bearer bb_invalid")],
        [("Authorization", f"Bearer {key} extra")],
        [("X-API-Key", ""), ("Authorization", f"Bearer {key}")],
    ):
        response = await async_client.get("/api/v1/printers/", headers=headers)
        assert response.status_code == 401


@pytest.mark.integration
async def test_key_validation_failure_is_closed(async_client, db_session, enforce_keys):
    await _set_user_auth(db_session, False)
    with patch(
        "backend.app.core.auth._validate_api_key", new=AsyncMock(side_effect=RuntimeError("database unavailable"))
    ):
        response = await async_client.get("/api/v1/printers/", headers={"X-API-Key": "bb_test"})
    assert response.status_code == 503


@pytest.mark.integration
async def test_native_login_and_jwt_still_work(async_client, db_session, enforce_keys):
    setup = await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "owner", "admin_password": "TestPassword1!"},
    )
    assert setup.status_code == 200, setup.text
    login = await async_client.post("/api/v1/auth/login", json={"username": "owner", "password": "TestPassword1!"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    assert (
        await async_client.get("/api/v1/printers/", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    mixed = await async_client.get(
        "/api/v1/printers/", headers={"Authorization": f"Bearer {token}", "X-API-Key": "bb_invalid"}
    )
    assert mixed.status_code == 401
    key = await _key(db_session)
    mixed = await async_client.get("/api/v1/printers/", headers={"Authorization": f"Bearer {token}", "X-API-Key": key})
    assert mixed.status_code == 401


@pytest.mark.integration
async def test_key_and_keyless_requests_do_not_share_principals(
    async_client, db_session, printer_factory, enforce_keys
):
    await _set_user_auth(db_session, False)
    await printer_factory(access_code="local-secret")
    key = await _key(db_session)
    keyed, browser = await asyncio.gather(
        async_client.get("/api/v1/printers/", headers={"X-API-Key": key}),
        async_client.get("/api/v1/printers/"),
    )
    assert keyed.status_code == browser.status_code == 200
    assert "access_code" not in keyed.json()[0]
    assert browser.json()[0]["access_code"] == "local-secret"


@pytest.mark.integration
async def test_public_version_and_identity_still_work_for_a_valid_key(async_client, db_session, enforce_keys):
    await _set_user_auth(db_session, False)
    key = await _key(db_session)
    headers = {"X-API-Key": key}
    assert (await async_client.get("/api/v1/updates/version", headers=headers)).status_code == 200
    assert (await async_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    result = await db_session.execute(select(APIKey))
    assert len(result.scalars().all()) == 1
