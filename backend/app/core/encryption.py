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
# Public source values exposed via get_key_source(). Internal failure causes
# (none_write_failed, none_corrupted) are mapped to "none" before exposure
# so the public API stays stable for the EncryptionStatusResponse schema.
_PublicSource = Literal["env", "file", "generated", "none"]
# Internal source carries the specific failure cause for accurate logging.
# "none" remains valid for legacy test stubs (lambda: (None, "none")).
_InternalSource = Literal[
    "env",
    "file",
    "generated",
    "none",
    "none_write_failed",
    "none_corrupted",
]
_key_source: _PublicSource | None = None

_KEY_FILE_NAME = ".mfa_encryption_key"


def _validate_fernet_key(key: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(key.encode())
    except (binascii.Error, ValueError):
        return False
    return len(decoded) == 32


def _load_or_generate_key() -> tuple[str | None, _InternalSource]:
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
            return None, "none_corrupted"
        if _validate_fernet_key(file_key):
            return file_key, "file"
        logger.error(
            "%s is present but is not a valid Fernet key. "
            "Refusing to overwrite — fix the file or set MFA_ENCRYPTION_KEY. "
            "Falling back to plaintext storage.",
            key_file,
        )
        return None, "none_corrupted"

    # 3. Generate a new key and persist it.
    # S1: Use os.open(O_WRONLY|O_CREAT|O_EXCL, 0o600) to avoid the TOCTOU
    # window between write_text() (umask-respecting) and chmod() — the key
    # is created with 0o600 from the start, never world-readable.
    new_key = Fernet.generate_key().decode()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, new_key.encode())
        finally:
            os.close(fd)
        # S9: Some filesystems (Windows, SMB, FUSE without uid mapping) silently
        # ignore mode bits — verify and warn so operators know the key is not
        # protected at the FS level.
        actual_mode = key_file.stat().st_mode & 0o777
        if actual_mode != 0o600:
            logger.warning(
                "MFA key file %s: filesystem did not enforce 0o600 (actual: 0o%o). "
                "Key may be world-readable on Windows / SMB / FUSE mounts.",
                key_file,
                actual_mode,
            )
        logger.info("Generated new MFA encryption key and saved to %s", key_file)
        return new_key, "generated"
    except FileExistsError:
        # Race between key_file.exists() check above and O_EXCL — another
        # process created the file. Treat as corrupted (do NOT regenerate).
        logger.error(
            "Race detected creating %s (file appeared between check and create). "
            "Refusing to overwrite — set MFA_ENCRYPTION_KEY explicitly to recover.",
            key_file,
        )
        return None, "none_corrupted"
    except OSError as exc:
        logger.error(
            "Could not save MFA encryption key to %s (%s). "
            "Falling back to plaintext storage. Set MFA_ENCRYPTION_KEY in the "
            "environment or fix the data-dir permissions to enable encryption.",
            key_file,
            exc,
        )
        return None, "none_write_failed"


def get_key_source() -> _PublicSource | None:
    return _key_source


def is_encryption_active() -> bool:
    return _get_fernet() is not None


def _get_fernet():
    global _fernet_instance, _warn_shown, _key_source

    if _fernet_instance is not None:
        return _fernet_instance

    key, internal_source = _load_or_generate_key()
    # S8: collapse internal failure causes to public "none" while keeping
    # the differentiated source for the warning path below.
    _key_source = "none" if internal_source.startswith("none") else internal_source

    if key is None:
        if not _warn_shown:
            # S8: only emit the "DATA_DIR not writable" warning when that's
            # actually the cause. The corrupted-file path already error-logged
            # in _load_or_generate_key with a more specific message.
            if internal_source == "none_write_failed":
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
        # S7: Warn when a key IS configured but the stored value is plaintext.
        # This surfaces rows that were written before encryption was enabled so
        # operators know they need a migration / re-enroll cycle. WARNING level
        # so it shows up in normal operator log review.
        if _get_fernet() is not None:
            logger.warning(
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
