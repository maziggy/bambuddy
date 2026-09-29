"""Web Push security and transport tests. No real push requests are sent."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException

from backend.app.services import web_push


@pytest.fixture
def subscription():
    raw = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return web_push.PushSubscription(
        endpoint="https://fcm.googleapis.com/fcm/send/private-token",
        keys={
            "p256dh": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
            "auth": base64.urlsafe_b64encode(b"a" * 16).rstrip(b"=").decode(),
        },
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://fcm.googleapis.com/push",
        "https://127.0.0.1/push",
        "https://[::1]/push",
        "https://169.254.169.254/metadata",
        "https://fcm.googleapis.com.evil.test/push",
        "https://evilpush.apple.com/push",
        "https://user:secret@fcm.googleapis.com/push",
        "https://fcm.googleapis.com:8443/push",
        "https://fcm.googleapis.com/push#fragment",
        "https://fcm.googleapis.com/",
        "https://fcm.googleapis.com\\@evil.test/push",
        "https://fcm.googleapis.com/push\ntoken",
    ],
)
def test_rejects_unsafe_endpoints(url):
    with pytest.raises(ValueError):
        web_push.validate_push_endpoint(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://fcm.googleapis.com/fcm/send/token",
        "https://web.push.apple.com/token",
        "https://updates.push.services.mozilla.com/wpush/v2/token",
        "https://wns2-test.notify.windows.com/w/?token=abc",
    ],
)
def test_supported_services(url):
    assert web_push.validate_push_endpoint(url) == url


def test_storage_encrypted_redacted_and_preserved(subscription):
    stored = web_push.store_push_config({"subscription": subscription.model_dump()})
    assert subscription.endpoint not in json.dumps(stored)
    assert subscription.keys.auth not in json.dumps(stored)
    assert stored["encrypted_subscription"].startswith("fernet:")
    assert web_push.public_push_config(stored) == {"registered": True}
    assert web_push.store_push_config({}, stored) == stored
    with pytest.raises(ValueError):
        web_push.store_push_config(stored)


def test_storage_fails_closed(monkeypatch, subscription):
    monkeypatch.setattr(web_push, "is_encryption_active", lambda: False)
    with pytest.raises(RuntimeError):
        web_push.store_push_config({"subscription": subscription.model_dump()})
    with pytest.raises(RuntimeError):
        web_push.get_vapid_private_key()


def test_vapid_survives_reload_and_does_not_replace_corrupt_key():
    first = web_push.get_vapid_private_key()
    path = web_push.resolve_data_dir() / ".web_push_vapid_key"
    assert b"PRIVATE KEY" not in path.read_bytes()
    assert web_push.get_vapid_private_key() == first
    path.write_text("corrupt")
    with pytest.raises(ValueError):
        web_push.get_vapid_private_key()
    assert path.read_text() == "corrupt"


def test_send_bounds_payload_and_sets_transport_safety(monkeypatch, subscription):
    send = Mock(return_value=SimpleNamespace(status_code=201))
    monkeypatch.setattr(web_push, "webpush", send)
    web_push.send_push(subscription, web_push.create_vapid_private_key(), "t" * 500, "😀" * 5000)
    arguments = send.call_args.kwargs
    payload = json.loads(arguments["data"])
    assert len(payload["title"]) == 100
    assert len(payload["body"]) == 500
    assert len(arguments["data"].encode()) < 3993
    assert payload["url"] == "/"
    assert arguments["timeout"] == 10
    assert isinstance(arguments["requests_session"], web_push._NoRedirectSession)


def test_backup_restore_preserves_subscription_identity(tmp_path):
    original = web_push.get_vapid_private_key()
    backup = tmp_path / "backup"
    backup.mkdir()
    web_push.backup_vapid_key(backup)
    key_path = web_push.resolve_data_dir() / ".web_push_vapid_key"
    key_path.unlink()
    assert web_push.get_vapid_private_key() != original
    web_push.restore_vapid_key(backup)
    assert web_push.get_vapid_private_key() == original


def test_legacy_backup_does_not_leave_stale_key(tmp_path):
    original = web_push.get_vapid_private_key()
    backup = tmp_path / "legacy"
    backup.mkdir()
    web_push.restore_vapid_key(backup)
    assert web_push.get_vapid_private_key() != original


def test_invalid_backup_key_does_not_overwrite_current_key(tmp_path):
    original = web_push.get_vapid_private_key()
    backup = tmp_path / "invalid"
    backup.mkdir()
    (backup / ".web_push_vapid_key").write_text("not a key")
    with pytest.raises(ValueError):
        web_push.restore_vapid_key(backup)
    assert web_push.get_vapid_private_key() == original


def test_no_redirects(monkeypatch):
    request = Mock(return_value=None)
    monkeypatch.setattr("requests.Session.request", request)
    with web_push._NoRedirectSession() as session:
        session.post("https://fcm.googleapis.com/send/token", allow_redirects=True)
    assert request.call_args.kwargs["allow_redirects"] is False


def test_backup_preflight_uses_backed_up_key_without_changing_current_identity(tmp_path):
    from cryptography.fernet import Fernet

    original = web_push.get_vapid_private_key()
    directory = web_push.resolve_data_dir()
    original_mfa = (directory / ".mfa_encryption_key").read_bytes()
    backup = tmp_path / "different-install"
    backup.mkdir()
    key = Fernet.generate_key()
    (backup / ".mfa_encryption_key").write_bytes(key)
    (backup / ".web_push_vapid_key").write_bytes(b"fernet:" + Fernet(key).encrypt(web_push.create_vapid_private_key()))
    web_push.validate_vapid_backup(backup)
    assert web_push.get_vapid_private_key() == original
    assert (directory / ".mfa_encryption_key").read_bytes() == original_mfa


def test_backup_preflight_rejects_conflicting_environment_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    web_push.get_vapid_private_key()
    backup = tmp_path / "backup"
    backup.mkdir()
    web_push.backup_vapid_key(backup)
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError, match="incompatible encryption key"):
        web_push.validate_vapid_backup(backup)


@pytest.mark.parametrize("payload", [b"fernet:invalid", b"fernet:" + b"x" * 4096])
def test_backup_preflight_rejects_corrupt_ciphertext(tmp_path, payload):
    web_push.get_vapid_private_key()
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / ".web_push_vapid_key").write_bytes(payload)
    with pytest.raises(ValueError):
        web_push.validate_vapid_backup(backup)


def test_push_write_failure_rolls_back_mfa_key(tmp_path, monkeypatch):
    original = web_push.get_vapid_private_key()
    directory = web_push.resolve_data_dir()
    mfa = directory / ".mfa_encryption_key"
    original_mfa = mfa.read_bytes()
    backup = tmp_path / "backup"
    backup.mkdir()
    web_push.backup_vapid_key(backup)
    (backup / mfa.name).write_bytes(original_mfa)
    replace = web_push.os.replace

    def fail_push_write(source, destination):
        if str(destination).endswith(".web_push_vapid_key"):
            raise OSError("simulated disk error")
        return replace(source, destination)

    monkeypatch.setattr(web_push.os, "replace", fail_push_write)
    with pytest.raises(OSError, match="simulated disk error"), web_push.preserve_encryption_keys_on_error():
        mfa.write_bytes(b"replaced MFA key")
        web_push.restore_vapid_key(backup)
    assert mfa.read_bytes() == original_mfa
    assert web_push.get_vapid_private_key() == original


def test_failed_key_install_does_not_leave_new_files(tmp_path, monkeypatch):
    monkeypatch.setattr(web_push, "resolve_data_dir", lambda: tmp_path)

    def fail_key_install():
        with web_push.preserve_encryption_keys_on_error():
            (tmp_path / ".mfa_encryption_key").write_bytes(b"new")
            raise OSError("installation failed")

    with pytest.raises(OSError, match="installation failed"):
        fail_key_install()
    assert not (tmp_path / ".mfa_encryption_key").exists()


def test_real_library_encryption_and_signing_before_network(monkeypatch, subscription):
    """Only mock the socket boundary, not pywebpush's crypto/signing."""
    import requests

    response = requests.Response()
    response.status_code = 201
    request = Mock(return_value=response)
    monkeypatch.setattr("requests.Session.request", request)
    web_push.send_push(subscription, web_push.create_vapid_private_key(), "Test", "Body")
    request.assert_called_once()
    assert request.call_args.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410, 429, 500])
async def test_expiry_cleanup_and_retryable_failures(monkeypatch, subscription, status):
    send = Mock(side_effect=WebPushException("DO NOT EXPOSE", response=SimpleNamespace(status_code=status)))
    monkeypatch.setattr(web_push, "webpush", send)
    config = web_push.store_push_config({"subscription": subscription.model_dump()})
    success, message = await web_push.deliver_push(config, "Test", "Body")
    assert not success
    assert "DO NOT EXPOSE" not in message
    assert ("encrypted_subscription" not in config) == (status in (404, 410))
    if status in (404, 410):
        await web_push.deliver_push(config, "Test", "Body")
        assert send.call_count == 1


@pytest.mark.asyncio
async def test_integrated_dispatch_honors_quiet_hours(monkeypatch, subscription):
    from backend.app.models.notification import NotificationProvider
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    send = Mock(return_value=SimpleNamespace(status_code=201))
    monkeypatch.setattr(web_push, "webpush", send)
    provider = NotificationProvider(
        provider_type="webpush",
        name="Test",
        config=json.dumps(web_push.store_push_config({"subscription": subscription.model_dump()})),
    )
    monkeypatch.setattr(service, "_is_in_quiet_hours", lambda _: True)
    assert (await service._send_to_provider(provider, "Test", "Body"))[0]
    send.assert_not_called()
    monkeypatch.setattr(service, "_is_in_quiet_hours", lambda _: False)
    assert (await service._send_to_provider(provider, "Test", "Body"))[0]
    send.assert_called_once()
