"""At-rest encryption for high-value secrets (TOTP keys, OIDC client_secret).

The encryption key is resolved on first use in this priority order:

1. ``MFA_ENCRYPTION_KEY`` environment variable (must be a URL-safe base64
   string that decodes to exactly 32 bytes — the Fernet key format).
2. ``DATA_DIR/.mfa_encryption_key`` file (read if present and valid). A
   corrupted or unreadable file falls back to plaintext (step 4) without
   overwriting — to protect previously encrypted rows.
3. Auto-generate a new Fernet key, write to ``DATA_DIR/.mfa_encryption_key``
   with mode ``0o600`` (only when neither env var nor key file exists).
   Falls back to plaintext (step 4) on OSError.
4. ``None`` (legacy plaintext fallback) — unreadable or corrupted key file,
   or read-only filesystem.

Existing plaintext values are read back correctly even after a key is
configured — values without the ``fernet:`` prefix are returned as-is. This
keeps the auto-bootstrap non-breaking for installs that already wrote
plaintext rows before the key existed.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

_FERNET_PREFIX = "fernet:"
_fernet_instance = None
_warn_shown = False
_key_source: Literal["env", "file", "generated", "none"] | None = None

_KEY_FILE_NAME = ".mfa_encryption_key"


def _validate_fernet_key(key: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(key.encode())
    except (binascii.Error, ValueError):
        return False
    return len(decoded) == 32


def _load_or_generate_key() -> tuple[str | None, Literal["env", "file", "generated", "none"]]:
    # Lazy import: keeps cryptography out of import-time even when the helper
    # is patched in tests that never invoke encryption.
    from cryptography.fernet import Fernet

    from backend.app.core.paths import resolve_data_dir

    # 1. Environment variable
    env_key = os.environ.get("MFA_ENCRYPTION_KEY")
    if env_key:
        if _validate_fernet_key(env_key):
            return env_key, "env"
        logger.error(
            "MFA_ENCRYPTION_KEY is set but is not a valid Fernet key "
            "(must decode to exactly 32 bytes). Falling back to file-based key."
        )

    data_dir = resolve_data_dir()
    key_file = data_dir / _KEY_FILE_NAME

    # 2. Existing file in DATA_DIR
    if key_file.exists():
        try:
            file_key = key_file.read_text().strip()
        except OSError as exc:
            # Refusing to fall through to regeneration — overwriting the file
            # would destroy access to every row already encrypted under the
            # current key. Operator must fix permissions or pin the key
            # explicitly via MFA_ENCRYPTION_KEY.
            logger.error(
                "Failed to read existing MFA key file %s (%s). "
                "Refusing to regenerate — this would destroy all previously encrypted secrets. "
                "Fix the file permissions or set MFA_ENCRYPTION_KEY explicitly.",
                key_file,
                exc,
            )
            return None, "none"
        if _validate_fernet_key(file_key):
            return file_key, "file"
        logger.error(
            "%s is present but is not a valid Fernet key. "
            "Refusing to overwrite — fix the file or set MFA_ENCRYPTION_KEY. "
            "Falling back to plaintext storage.",
            key_file,
        )
        return None, "none"

    # 3. Generate a new key and persist it
    new_key = Fernet.generate_key().decode()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key)
        key_file.chmod(0o600)
        logger.info("Generated new MFA encryption key and saved to %s", key_file)
        return new_key, "generated"
    except OSError as exc:
        logger.error(
            "Could not save MFA encryption key to %s (%s). "
            "Falling back to plaintext storage. Set MFA_ENCRYPTION_KEY in the "
            "environment or fix the data-dir permissions to enable encryption.",
            key_file,
            exc,
        )
        return None, "none"


def get_key_source() -> Literal["env", "file", "generated", "none"] | None:
    return _key_source


def is_encryption_active() -> bool:
    return _get_fernet() is not None


def _get_fernet():
    global _fernet_instance, _warn_shown, _key_source

    if _fernet_instance is not None:
        return _fernet_instance

    key, source = _load_or_generate_key()
    _key_source = source

    if key is None:
        if not _warn_shown:
            logger.warning(
                "MFA_ENCRYPTION_KEY is not set and DATA_DIR is not writable — "
                "TOTP secrets and OIDC client_secrets are stored in plaintext. "
                "Generate a key with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
            # Suppresses repetitive warnings across calls; reset together
            # with _fernet_instance when re-initializing (e.g. in tests).
            _warn_shown = True
        return None

    from cryptography.fernet import Fernet

    _fernet_instance = Fernet(key.encode())
    return _fernet_instance


def mfa_encrypt(plaintext: str) -> str:
    """Encrypt a secret value. Returns the ciphertext with a ``fernet:`` prefix,
    or the original plaintext if no encryption key is available."""
    f = _get_fernet()
    if f is None:
        return plaintext
    return _FERNET_PREFIX + f.encrypt(plaintext.encode()).decode()


def mfa_decrypt(value: str) -> str:
    """Decrypt a value previously encrypted with ``mfa_encrypt``.

    Values without the ``fernet:`` prefix are returned as-is (legacy plaintext).
    Raises ``RuntimeError`` if the prefix is present but no key is configured.
    """
    if not value.startswith(_FERNET_PREFIX):
        # Nit6: Warn when a key IS configured but the stored value is plaintext.
        # This surfaces rows that were written before encryption was enabled so
        # operators know they need a migration / re-enroll cycle.
        if _get_fernet() is not None:
            logger.debug(
                "mfa_decrypt: encryption key is active but the stored value has no "
                "'fernet:' prefix — returning legacy plaintext. Consider re-enrolling "
                "this secret to store it encrypted."
            )
        return value  # Legacy plaintext — backward compatible

    f = _get_fernet()
    if f is None:
        raise RuntimeError(
            "MFA_ENCRYPTION_KEY must be set to decrypt MFA secrets that were stored with encryption enabled."
        )
    from cryptography.fernet import InvalidToken

    try:
        return f.decrypt(value[len(_FERNET_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "MFA secret was encrypted under a different MFA_ENCRYPTION_KEY. "
            "Key rotation is not currently supported — restore the previous key "
            "or have users re-enroll."
        ) from exc
