"""BAMBUDDY_OIDC_* reader (#2593).

The reader is deliberately dumb: it maps env vars to field names and applies
defaults. Whether the resulting provider is *valid* is decided later, by the
same OIDCProviderCreate schema the API uses, so env config cannot bypass a
check the UI enforces.
"""

from __future__ import annotations

import pytest

from backend.app.core.oidc_env import read_env_oidc_config

REQUIRED = {
    "BAMBUDDY_OIDC_NAME": "Keycloak",
    "BAMBUDDY_OIDC_ISSUER_URL": "https://sso.example.com/realms/main",
    "BAMBUDDY_OIDC_CLIENT_ID": "bambuddy",
    "BAMBUDDY_OIDC_CLIENT_SECRET": "s3cr3t",
}

OPTIONAL = (
    "BAMBUDDY_OIDC_SCOPES",
    "BAMBUDDY_OIDC_ENABLED",
    "BAMBUDDY_OIDC_AUTO_CREATE_USERS",
    "BAMBUDDY_OIDC_AUTO_LINK_EXISTING",
    "BAMBUDDY_OIDC_EMAIL_CLAIM",
    "BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED",
    "BAMBUDDY_OIDC_ICON_URL",
    "BAMBUDDY_OIDC_AUTOLOGIN",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (*REQUIRED, *OPTIONAL):
        monkeypatch.delenv(key, raising=False)


def _set_required(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)


def test_returns_none_when_nothing_is_configured():
    assert read_env_oidc_config() is None


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_returns_none_when_any_single_required_var_is_missing(monkeypatch, missing):
    """All four or nothing -- a half-configured provider must not reach the
    database, where it would fail at authorize time instead of at startup."""
    _set_required(monkeypatch)
    monkeypatch.delenv(missing)
    assert read_env_oidc_config() is None


def test_an_empty_required_var_counts_as_unset(monkeypatch):
    """`BAMBUDDY_OIDC_CLIENT_SECRET=` in a compose file is a forgotten value,
    not an intentional empty secret."""
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_SECRET", "")
    assert read_env_oidc_config() is None


def test_reads_the_required_vars(monkeypatch):
    _set_required(monkeypatch)
    cfg = read_env_oidc_config()
    assert cfg["name"] == "Keycloak"
    assert cfg["issuer_url"] == "https://sso.example.com/realms/main"
    assert cfg["client_id"] == "bambuddy"
    assert cfg["client_secret"] == "s3cr3t"


def test_applies_the_documented_defaults(monkeypatch):
    _set_required(monkeypatch)
    cfg = read_env_oidc_config()
    assert cfg["scopes"] == "openid email profile"
    assert cfg["is_enabled"] is True
    assert cfg["auto_create_users"] is False
    assert cfg["auto_link_existing_accounts"] is False
    assert cfg["email_claim"] == "email"
    assert cfg["require_email_verified"] is True
    assert cfg["icon_url"] is None
    assert cfg["is_autologin"] is False


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "yes", "YES", " yes "])
def test_booleans_accept_the_project_truthy_spellings(monkeypatch, raw):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_AUTO_CREATE_USERS", raw)
    assert read_env_oidc_config()["auto_create_users"] is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "", "off", "nonsense"])
def test_anything_else_is_false(monkeypatch, raw):
    """Only the three documented spellings enable a flag; an unrecognised value
    must not silently turn on auto-create-users."""
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_AUTO_CREATE_USERS", raw)
    assert read_env_oidc_config()["auto_create_users"] is False


def test_a_boolean_default_of_true_can_be_turned_off(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED", "false")
    assert read_env_oidc_config()["require_email_verified"] is False


def test_optional_strings_override_their_defaults(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_SCOPES", "openid profile groups")
    monkeypatch.setenv("BAMBUDDY_OIDC_EMAIL_CLAIM", "mail")
    monkeypatch.setenv("BAMBUDDY_OIDC_ICON_URL", "https://sso.example.com/logo.png")
    cfg = read_env_oidc_config()
    assert cfg["scopes"] == "openid profile groups"
    assert cfg["email_claim"] == "mail"
    assert cfg["icon_url"] == "https://sso.example.com/logo.png"
