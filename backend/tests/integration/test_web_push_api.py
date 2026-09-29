"""Exercise Push through the existing notification provider API."""

import base64
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_push_provider_crud_and_redaction(async_client, db_session, monkeypatch):
    raw = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    subscription = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test-secret",
        "keys": {
            "p256dh": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
            "auth": base64.urlsafe_b64encode(b"a" * 16).rstrip(b"=").decode(),
        },
    }
    base = "/api/v1/notifications"
    key = await async_client.get(base + "/webpush/public-key")
    assert key.status_code == 200
    assert set(key.json()) == {"public_key"}
    data = {"name": "Phone", "provider_type": "webpush", "config": {"subscription": subscription}}
    response = await async_client.post(base + "/", json=data)
    assert response.status_code == 200, response.text
    provider = response.json()
    assert provider["config"] == {"registered": True}
    identifier = provider["id"]
    row = (
        await db_session.execute(text("SELECT config FROM notification_providers WHERE id=:id"), {"id": identifier})
    ).scalar()
    assert subscription["endpoint"] not in row
    assert json.loads(row)["encrypted_subscription"].startswith("fernet:")
    response = await async_client.patch(
        f"{base}/{identifier}", json={"config": {}, "name": "Edited", "on_print_start": True}
    )
    assert response.status_code == 200
    assert response.json()["config"] == {"registered": True}
    assert response.json()["on_print_start"] is True
    response = await async_client.get(base + "/")
    assert subscription["endpoint"] not in response.text
    assert "fernet:" not in response.text
    response = await async_client.patch(f"{base}/{identifier}", json={"config": {"encrypted_subscription": "fake"}})
    assert response.status_code == 422
    send = AsyncMock(return_value=(True, "Accepted"))
    monkeypatch.setattr("backend.app.services.notification_service.deliver_push", send)
    response = await async_client.post(f"{base}/{identifier}/test")
    assert response.json()["success"] is True
    assert "encrypted_subscription" in send.call_args.args[0]
    response = await async_client.patch(f"{base}/{identifier}", json={"provider_type": "ntfy"})
    assert response.status_code == 200
    assert response.json()["config"] == {}
    response = await async_client.delete(f"{base}/{identifier}")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_invalid_subscription_is_rejected_without_echoing_secrets(async_client):
    for path in ("/", "/test-config"):
        response = await async_client.post(
            "/api/v1/notifications" + path,
            json={
                "name": "Phone",
                "provider_type": "webpush",
                "config": {
                    "subscription": {
                        "endpoint": "https://127.0.0.1/private-token",
                        "keys": {"auth": "secret", "p256dh": "secret"},
                    }
                },
            },
        )
        assert response.status_code == 422
        assert "private-token" not in response.text
        assert "secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_normal_event_filters_and_expiry_are_persisted(
    db_session, printer_factory, notification_provider_factory, monkeypatch
):
    from sqlalchemy import select

    from backend.app.models.notification import NotificationLog
    from backend.app.services.notification_service import NotificationService

    printer = await printer_factory(is_active=False)
    other = await printer_factory(is_active=False)
    selected = await notification_provider_factory(provider_type="webpush", config={"encrypted_subscription": "opaque"})
    await notification_provider_factory(provider_type="webpush", enabled=False)
    await notification_provider_factory(provider_type="webpush", on_print_complete=False)
    await notification_provider_factory(provider_type="webpush", printer_id=other.id)

    async def expired(config, title, body):
        config.pop("encrypted_subscription")
        config["expired"] = True
        return False, "Push subscription expired"

    send = AsyncMock(side_effect=expired)
    monkeypatch.setattr("backend.app.services.notification_service.deliver_push", send)
    await NotificationService().on_print_complete(
        printer.id, "Simulated", "completed", {"filename": "test"}, db_session
    )
    send.assert_awaited_once()
    await db_session.refresh(selected)
    assert json.loads(selected.config) == {"expired": True}
    logs = list((await db_session.execute(select(NotificationLog))).scalars())
    assert len(logs) == 1
    assert logs[0].provider_id == selected.id
    assert logs[0].event_type == "print_complete"
    assert not logs[0].success


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_backup_includes_encrypted_push_identity(async_client, tmp_path, monkeypatch):
    import zipfile

    from backend.app.api.routes.settings import create_backup_zip
    from backend.app.core.config import settings
    from backend.app.services.web_push import get_vapid_private_key

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    original = get_vapid_private_key()
    archive, _ = await create_backup_zip(output_path=tmp_path)
    try:
        with zipfile.ZipFile(archive) as backup:
            data = backup.read(".web_push_vapid_key")
            assert data.startswith(b"fernet:")
            assert original not in data
            assert ".mfa_encryption_key" in backup.namelist()
    finally:
        archive.unlink()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_restore_rejects_bad_push_key_before_stopping_services(async_client, monkeypatch):
    import io
    import zipfile

    from backend.app.services.web_push import get_vapid_private_key, resolve_data_dir

    original = get_vapid_private_key()
    original_mfa = (resolve_data_dir() / ".mfa_encryption_key").read_bytes()
    stop = AsyncMock()
    monkeypatch.setattr("backend.app.core.database.close_all_connections", stop)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as backup:
        backup.writestr("bambuddy.db", b"not used: key validation must happen first")
        backup.writestr(".web_push_vapid_key", b"fernet:corrupt")
        backup.writestr(".mfa_encryption_key", b"invalid")
    response = await async_client.post(
        "/api/v1/settings/restore", files={"file": ("backup.zip", archive.getvalue(), "application/zip")}
    )
    assert response.status_code == 400, response.text
    stop.assert_not_called()
    assert get_vapid_private_key() == original
    assert (resolve_data_dir() / ".mfa_encryption_key").read_bytes() == original_mfa


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("credentials", ["missing", "invalid", "wrong_scope", "expired", "revoked"])
async def test_push_requires_existing_notification_permissions(async_client, db_session, credentials):
    from datetime import timedelta

    from backend.app.core.auth import create_access_token, get_password_hash
    from backend.app.models.settings import Settings
    from backend.app.models.user import User

    db_session.add_all(
        [
            Settings(key="auth_enabled", value="true"),
            Settings(key="advanced_auth_enabled", value="true"),
            User(
                username="push-no-permissions",
                password_hash=get_password_hash("OnlyForTests123!"),
                role="user",
                is_active=True,
            ),
        ]
    )
    await db_session.commit()
    headers = {}
    if credentials in ("wrong_scope", "revoked"):
        login = await async_client.post(
            "/api/v1/auth/login",
            json={
                "username": "push-no-permissions",
                "password": "OnlyForTests123!",
            },
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    elif credentials == "invalid":
        headers = {"Authorization": "Bearer invalid-token"}
    elif credentials == "expired":
        token = create_access_token({"sub": "push-no-permissions"}, expires_delta=timedelta(seconds=-1))
        headers = {"Authorization": f"Bearer {token}"}
    if credentials == "revoked":
        logout = await async_client.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code == 200
    expected = 403 if credentials == "wrong_scope" else 401
    response = await async_client.get("/api/v1/notifications/webpush/public-key", headers=headers)
    assert response.status_code == expected
    for path in ("/", "/test-config"):
        response = await async_client.post(
            "/api/v1/notifications" + path,
            headers=headers,
            json={
                "name": "No permission",
                "provider_type": "webpush",
                "config": {},
            },
        )
        assert response.status_code == expected
