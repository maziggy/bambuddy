"""S6: warn on unknown MFA_*/BAMBUDDY_* env vars so typos like
``MFA_ENCYPTION_KEY`` are not silently swallowed by ``extra="ignore"``."""

from __future__ import annotations

import importlib
import logging

import pytest


@pytest.mark.unit
def test_unknown_mfa_env_var_logs_info(monkeypatch, caplog):
    """A typo'd MFA_* env var must be logged at INFO so operators see it."""
    monkeypatch.setenv("MFA_ENCYPTION_KEY", "typo-value")  # missing R

    import backend.app.core.config as cfg_mod

    with caplog.at_level(logging.INFO):
        importlib.reload(cfg_mod)

    assert any("MFA_ENCYPTION_KEY" in rec.message for rec in caplog.records)


@pytest.mark.unit
def test_unknown_bambuddy_env_var_logs_info(monkeypatch, caplog):
    """An unrecognised BAMBUDDY_* env var must also be logged."""
    monkeypatch.setenv("BAMBUDDY_NEW_FEATURE", "v1")

    import backend.app.core.config as cfg_mod

    with caplog.at_level(logging.INFO):
        importlib.reload(cfg_mod)

    assert any("BAMBUDDY_NEW_FEATURE" in rec.message for rec in caplog.records)


@pytest.mark.unit
def test_known_intentional_env_var_does_not_log(monkeypatch, caplog):
    """MFA_ENCRYPTION_KEY is declared in _INTENTIONAL_UNSETTINGS — must be silent."""
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "x" * 44)  # invalid but not a typo

    import backend.app.core.config as cfg_mod

    with caplog.at_level(logging.INFO):
        importlib.reload(cfg_mod)

    # The intentional var must not produce a typo warning.
    typo_warnings = [
        rec for rec in caplog.records if "MFA_ENCRYPTION_KEY" in rec.message and "typo" in rec.message.lower()
    ]
    assert typo_warnings == []
