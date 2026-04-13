"""At-rest encryption for high-value secrets (TOTP keys, OIDC client_secret).

Set the ``MFA_ENCRYPTION_KEY`` environment variable to a URL-safe base64-encoded
32-byte key (generate with ``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``)
to enable Fernet symmetric encryption.

When the key is not set, values are stored as plaintext and a warning is emitted.
Existing plaintext values are read back correctly even after the key is added
(values without the ``fernet:`` prefix are treated as legacy plaintext).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FERNET_PREFIX = "fernet:"
_fernet_instance = None
_warn_shown = False


def _get_fernet():
    global _fernet_instance, _warn_shown

    if _fernet_instance is not None:
        return _fernet_instance

    key = os.environ.get("MFA_ENCRYPTION_KEY")
    if key:
        from cryptography.fernet import Fernet

        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
        return _fernet_instance

    if not _warn_shown:
        logger.warning(
            "MFA_ENCRYPTION_KEY is not set — TOTP secrets and OIDC client_secrets are "
            "stored in plaintext. Generate a key with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
        _warn_shown = True
    return None


def mfa_encrypt(plaintext: str) -> str:
    """Encrypt a secret value. Returns the ciphertext with a ``fernet:`` prefix,
    or the original plaintext if ``MFA_ENCRYPTION_KEY`` is not configured."""
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
            logger.warning(
                "mfa_decrypt: MFA_ENCRYPTION_KEY is set but the stored value has no "
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
    except InvalidToken:
        raise RuntimeError(
            "MFA secret was encrypted under a different MFA_ENCRYPTION_KEY. "
            "Key rotation is not currently supported — restore the previous key "
            "or have users re-enroll."
        )
