"""Shared path resolution helpers.

Centralises the DATA_DIR fallback used by ``auth.py`` (``.jwt_secret``) and
``encryption.py`` (``.mfa_encryption_key``) so both modules read the
environment variable fresh on every call. Reading fresh — instead of caching
the value at module import — is required so test fixtures can override
``DATA_DIR`` per-test via ``monkeypatch.setenv`` and have the override take
effect immediately.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_data_dir() -> Path:
    """Return the data directory, reading ``DATA_DIR`` fresh from env on each call.

    Falls back to ``<project_root>/data`` when ``DATA_DIR`` is not set, matching
    the behaviour of ``backend/app/core/auth.py:_get_jwt_secret``.
    """
    data_dir_env = os.environ.get("DATA_DIR")
    if data_dir_env:
        return Path(data_dir_env)
    return Path(__file__).parent.parent.parent.parent / "data"
