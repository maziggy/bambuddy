"""S6: warn on unknown MFA_*/BAMBUDDY_* env vars so typos like
``MFA_ENCYPTION_KEY`` are not silently swallowed by ``extra="ignore"``."""

from __future__ import annotations

import importlib
import logging

import pytest


@pytest.fixture(autouse=True)
def _restore_config_module():
    """Undo the ``importlib.reload`` these tests depend on.

    Reloading ``backend.app.core.config`` re-executes it, so ``settings`` becomes
    a *new* object built from the environment as it stands mid-test. Nothing put
    the old one back. ``monkeypatch`` unwinds the env vars, not the reload.

    The result is two live ``Settings`` instances in one process: every module
    that did ``from ... config import settings`` at import time keeps the
    original, while anything resolving ``config.settings`` afterwards gets the
    replacement — and under xdist that split persisted for every later test in
    the same worker. It surfaced as unrelated path assertions failing with a
    ``base_dir`` from *this* module's tmp_path (``TestLibraryPathHelpers``,
    ``TestUploadSourceThreeMF``, ``TestArchivePlatesDesignOverrides``,
    ``TestSystemHealthAPI``), which is why it looked like a random flake and
    moved between runs as the work distribution changed.

    Snapshotting the whole module dict rather than just ``settings`` restores
    object *identity*, which is what the two views have to agree on.
    """
    import backend.app.core.config as cfg_mod

    saved = dict(cfg_mod.__dict__)
    yield
    cfg_mod.__dict__.clear()
    cfg_mod.__dict__.update(saved)


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
