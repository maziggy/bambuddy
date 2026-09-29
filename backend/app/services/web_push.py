"""Small Web Push transport boundary; no printer access or application startup.

Subscriptions are untrusted input. Only established browser push services are
accepted, and redirects are disabled so they cannot turn delivery into SSRF.
Do not log endpoints, subscription keys, payloads, or provider response bodies.
"""

import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pywebpush import WebPushException, webpush

from backend.app.core.encryption import is_encryption_active, mfa_decrypt, mfa_encrypt
from backend.app.core.paths import resolve_data_dir


def validate_push_endpoint(value: str) -> str:
    if len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("Invalid push service endpoint")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        valid_host = (
            host == "fcm.googleapis.com"
            or host == "web.push.apple.com"
            or host.endswith(".push.apple.com")
            or host == "updates.push.services.mozilla.com"
            or host.endswith(".push.services.mozilla.com")
            or host.endswith(".notify.windows.com")
        )
        valid = (
            url.scheme == "https"
            and valid_host
            and url.port in (None, 443)
            and not url.username
            and not url.password
            and not url.fragment
            and bool(url.path.strip("/"))
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use an HTTPS subscription from a supported browser push service")
    return value


def decode_key(value: str, length: int) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("Invalid subscription key")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise ValueError("Invalid subscription key") from exc
    if len(decoded) != length:
        raise ValueError("Invalid subscription key length")
    return decoded


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(max_length=90)
    auth: str = Field(max_length=24)

    @field_validator("p256dh")
    @classmethod
    def check_public_key(cls, value: str) -> str:
        raw = decode_key(value, 65)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        return value

    @field_validator("auth")
    @classmethod
    def check_auth(cls, value: str) -> str:
        decode_key(value, 16)
        return value


class PushSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(max_length=2048)
    keys: PushKeys
    expirationTime: float | None = None  # Browser PushSubscription.toJSON().

    @field_validator("endpoint")
    @classmethod
    def check_endpoint(cls, value: str) -> str:
        return validate_push_endpoint(value)


class PushDeliveryError(Exception):
    """Sanitized failure; never exposes a provider's body or subscription."""

    def __init__(self, status_code: int | None = None):
        self.status_code = status_code
        self.expired = status_code in (404, 410)
        super().__init__("Push subscription expired" if self.expired else "Push delivery failed")


class _NoRedirectSession(requests.Session):
    def request(self, method, url, **kwargs):
        validate_push_endpoint(url)
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def create_vapid_private_key() -> bytes:
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def vapid_public_key(private_pem: bytes) -> str:
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(private_key.curve, ec.SECP256R1):
        raise ValueError("Web Push requires a P-256 private key")
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def send_push(subscription: PushSubscription, private_pem: bytes, title: str, body: str) -> None:
    """Send a bounded, visible notification; invoke off the event loop."""
    subscription = PushSubscription.model_validate(subscription.model_dump())
    payload = json.dumps(
        {
            "title": title[:100],
            "body": body[:500],
            "url": "/",
        },
        ensure_ascii=False,
    )
    try:
        with _NoRedirectSession() as session:
            response = webpush(
                subscription_info=subscription.model_dump(exclude={"expirationTime"}),
                data=payload,
                vapid_private_key=Vapid.from_pem(private_pem),
                # py-vapid's strict HTTPS subject validator accepts origins,
                # not URLs with paths. Use the project's contact site.
                vapid_claims={"sub": "https://bambuddy.cool"},
                ttl=3600,
                timeout=10,
                requests_session=session,
            )
        if not 200 <= response.status_code < 300:
            raise PushDeliveryError(response.status_code)
    except WebPushException as exc:
        response = exc.response
        raise PushDeliveryError(response.status_code if response is not None else None) from None
    except requests.RequestException:
        raise PushDeliveryError() from None


def get_vapid_private_key() -> bytes:
    """Persist one encrypted application key, never replace an existing key.

    A hard link publishes the completely written file atomically without
    overwriting a key created by another worker. Back up this file together
    with the database and .mfa_encryption_key.
    """
    if not is_encryption_active():
        raise RuntimeError("Web Push requires working secret encryption")
    directory = resolve_data_dir()
    path = directory / ".web_push_vapid_key"
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".web_push_", dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(mfa_encrypt(create_vapid_private_key().decode("ascii")))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                # Another worker published the key first; read and validate it below.
                pass
        finally:
            os.unlink(temporary)
    private_pem = mfa_decrypt(path.read_text()).encode("ascii")
    vapid_public_key(private_pem)  # Fail closed if corrupt; do not regenerate.
    return private_pem


def backup_vapid_key(destination: Path) -> None:
    """Keep subscription identity with a full backup, never in diagnostics."""
    source = resolve_data_dir() / ".web_push_vapid_key"
    if source.exists():
        shutil.copy2(source, destination / source.name)


def validate_vapid_backup(source_directory: Path) -> None:
    """Validate against the post-restore encryption key before changing any files.

    An explicit environment key takes precedence, just as it does at startup.
    This deliberately does not initialize the current encryption singleton.
    """
    source = source_directory / ".web_push_vapid_key"
    if not source.exists():
        return
    try:
        if source.stat().st_size > 4096:
            raise ValueError()
        encrypted = source.read_bytes()
        if not encrypted.startswith(b"fernet:"):
            raise ValueError()
        key = os.environ.get("MFA_ENCRYPTION_KEY")
        if key:
            cipher = Fernet(key.encode())
        else:
            key_file = source_directory / ".mfa_encryption_key"
            if not key_file.exists():
                key_file = resolve_data_dir() / ".mfa_encryption_key"
            cipher = Fernet(key_file.read_bytes().strip())
        vapid_public_key(cipher.decrypt(encrypted[len(b"fernet:") :]))
    except Exception:
        raise ValueError("Invalid Web Push key backup or incompatible encryption key") from None


def _write_secret_file(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".web_push_restore_", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def preserve_encryption_keys_on_error():
    """Undo partial MFA/Push key writes if key installation fails before DB restore."""
    directory = resolve_data_dir()
    originals = {}
    for name in (".mfa_encryption_key", ".web_push_vapid_key"):
        path = directory / name
        originals[path] = path.read_bytes() if path.exists() else None
    try:
        yield
    except Exception:
        for path, original in originals.items():
            current = path.read_bytes() if path.exists() else None
            if current != original:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _write_secret_file(path, original)
        raise


def restore_vapid_key(source_directory: Path) -> None:
    """Restore the backed-up identity alongside its encryption key.

    Older backups have no Push identity. Remove the previous installation's
    key in that case: retaining it could leave a key encrypted with the wrong
    MFA key after restore. Devices from the replaced installation must enroll
    again; a full restore replaces their subscription records as well.
    """
    directory = resolve_data_dir()
    destination = directory / ".web_push_vapid_key"
    source = source_directory / destination.name
    if not source.exists():
        destination.unlink(missing_ok=True)
        return
    validate_vapid_backup(source_directory)
    _write_secret_file(destination, source.read_bytes())


def store_push_config(config: dict, existing: dict | None = None) -> dict:
    """Only accept browser subscriptions, never client-supplied ciphertext."""
    if set(config) - {"subscription", "registered"}:
        raise ValueError("Invalid Push configuration")
    if "subscription" not in config:
        if existing and existing.get("encrypted_subscription") and not existing.get("expired"):
            return existing
        raise ValueError("Enable notifications on this device before saving")
    try:
        subscription = PushSubscription.model_validate(config["subscription"])
    except ValueError:
        raise ValueError("Invalid browser push subscription") from None
    if not is_encryption_active():
        raise RuntimeError("Web Push requires working secret encryption")
    return {"encrypted_subscription": mfa_encrypt(subscription.model_dump_json())}


def public_push_config(config: dict) -> dict:
    return {"registered": bool(config.get("encrypted_subscription")) and not config.get("expired", False)}


async def deliver_push(config: dict, title: str, body: str) -> tuple[bool, str]:
    """Never return/log an endpoint, key, or push-service response body."""
    if config.get("expired"):
        return False, "Push subscription expired; enable notifications on the device again"
    try:
        subscription = PushSubscription.model_validate_json(mfa_decrypt(config["encrypted_subscription"]))
        await asyncio.to_thread(send_push, subscription, get_vapid_private_key(), title, body)
        return True, "Push service accepted the notification; check the device for delivery"
    except PushDeliveryError as exc:
        if exc.expired:
            config["expired"] = True
            # Discard the unusable capability; do not retry it indefinitely.
            config.pop("encrypted_subscription", None)
            return False, "Push subscription expired; enable notifications on the device again"
        return False, "Push delivery failed; try again later"
    except Exception:
        return False, "Push configuration unavailable; enable notifications on the device again"
