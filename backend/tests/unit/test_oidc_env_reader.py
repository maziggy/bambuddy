"""BAMBUDDY_OIDC_* reader (#2593).

The reader is deliberately dumb: it maps env vars to field names and applies
defaults. Whether the resulting provider is *valid* is decided later, by the
same OIDCProviderCreate schema the API uses, so env config cannot bypass a
check the UI enforces.
"""

from __future__ import annotations

import pytest

from backend.app.core.oidc_env import EnvOIDCConfigError, env_bool, read_env_oidc_config

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
    "BAMBUDDY_OIDC_DEFAULT_GROUP",
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


@pytest.mark.parametrize("raw", ["", "   ", "\n", " \t\n "])
@pytest.mark.parametrize("key", sorted(REQUIRED))
def test_an_empty_required_var_counts_as_unset(monkeypatch, key, raw):
    """`BAMBUDDY_OIDC_CLIENT_SECRET=` in a compose file is a forgotten value,
    not an intentional empty secret -- and neither is one holding only
    whitespace, which the optional vars have always treated as unset."""
    _set_required(monkeypatch)
    monkeypatch.setenv(key, raw)
    assert read_env_oidc_config() is None


@pytest.mark.parametrize("key", sorted(REQUIRED))
def test_a_required_var_is_stripped(monkeypatch, key):
    """A Kubernetes Secret written as a block scalar carries a trailing
    newline, and the schema bounds these four by max_length only -- so an
    unstripped issuer_url reaches the database, enables the SSO button and
    then raises httpx.InvalidURL on the first click, long after startup could
    have refused it."""
    _set_required(monkeypatch)
    monkeypatch.setenv(key, f"  {REQUIRED[key]}\n")

    cfg = read_env_oidc_config()
    field = {
        "BAMBUDDY_OIDC_NAME": "name",
        "BAMBUDDY_OIDC_ISSUER_URL": "issuer_url",
        "BAMBUDDY_OIDC_CLIENT_ID": "client_id",
        "BAMBUDDY_OIDC_CLIENT_SECRET": "client_secret",
    }[key]
    assert cfg[field] == REQUIRED[key]


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


@pytest.mark.parametrize("raw", ["false", "FALSE", "False", "0", "no", "NO"])
def test_falsy_values_are_false(monkeypatch, raw):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_AUTO_CREATE_USERS", raw)
    assert read_env_oidc_config()["auto_create_users"] is False


@pytest.mark.parametrize("raw", ["on", "enabled", "y", "nonsense"])
def test_an_unrecognized_boolean_is_rejected(monkeypatch, raw):
    """Only the documented spellings are accepted; an unrecognised value must
    not silently turn a flag on or off -- it must refuse the whole config
    instead of guessing (M-R4 strict boolean parsing)."""
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_AUTO_CREATE_USERS", raw)
    with pytest.raises(EnvOIDCConfigError, match="BAMBUDDY_OIDC_AUTO_CREATE_USERS"):
        read_env_oidc_config()


def test_a_boolean_default_of_true_can_be_turned_off(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_REQUIRE_EMAIL_VERIFIED", "false")
    assert read_env_oidc_config()["require_email_verified"] is False


# --- env_bool, tested directly ------------------------------------------------
# The reader-level tests above pin the contract through read_env_oidc_config;
# these exercise the helper itself so its default/blank/reject behavior is
# proven independently of any particular BAMBUDDY_OIDC_* field.


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "NO"])
def test_env_bool_falsy_values_are_false(monkeypatch, raw):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert env_bool("SOME_FLAG", True) is False


@pytest.mark.parametrize("default", [True, False])
def test_env_bool_absent_is_the_given_default(monkeypatch, default):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert env_bool("SOME_FLAG", default) is default


@pytest.mark.parametrize("raw", ["", "   "])
@pytest.mark.parametrize("default", [True, False])
def test_env_bool_blank_is_the_given_default(monkeypatch, raw, default):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert env_bool("SOME_FLAG", default) is default


@pytest.mark.parametrize("raw", ["on", "enabled", "y", "nonsense"])
def test_env_bool_rejects_an_unrecognized_value(monkeypatch, raw):
    monkeypatch.setenv("SOME_FLAG", raw)
    with pytest.raises(EnvOIDCConfigError, match="SOME_FLAG"):
        env_bool("SOME_FLAG", True)


@pytest.mark.parametrize("raw", ["on", "enabled", "y", "nonsense"])
@pytest.mark.parametrize("default", [True, False])
def test_env_bool_lenient_falls_back_to_default_on_unrecognized(monkeypatch, raw, default):
    """strict=False (the request-path callers like BAMBUDDY_LOCAL_LOGIN): an
    unrecognized value must return the default, never raise -- a raise there
    would 500 a live endpoint rather than skip a startup config."""
    monkeypatch.setenv("SOME_FLAG", raw)
    assert env_bool("SOME_FLAG", default, strict=False) is default


def test_optional_strings_override_their_defaults(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_SCOPES", "openid profile groups")
    monkeypatch.setenv("BAMBUDDY_OIDC_EMAIL_CLAIM", "mail")
    monkeypatch.setenv("BAMBUDDY_OIDC_ICON_URL", "https://sso.example.com/logo.png")
    cfg = read_env_oidc_config()
    assert cfg["scopes"] == "openid profile groups"
    assert cfg["email_claim"] == "mail"
    assert cfg["icon_url"] == "https://sso.example.com/logo.png"


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_scopes_is_unset(monkeypatch, raw):
    """`BAMBUDDY_OIDC_SCOPES=` in a compose file is a forgotten value, not a
    request for a provider with no scopes -- same rule as default_group."""
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_SCOPES", raw)
    assert read_env_oidc_config()["scopes"] == "openid email profile"


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_email_claim_is_unset(monkeypatch, raw):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_EMAIL_CLAIM", raw)
    assert read_env_oidc_config()["email_claim"] == "email"


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_icon_url_is_unset(monkeypatch, raw):
    """Uncommenting `# BAMBUDDY_OIDC_ICON_URL=` in .env.example must not take
    the provider down -- the reader must still return a config, not refuse it."""
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_ICON_URL", raw)
    cfg = read_env_oidc_config()
    assert cfg is not None, "a blank optional var must not refuse the whole provider"
    assert cfg["icon_url"] is None


def test_the_default_group_is_read_as_a_name(monkeypatch):
    """A name, not an id: group ids differ per install, so an id in a compose
    file would point at whatever group happened to be created third."""
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_DEFAULT_GROUP", "Operators")
    cfg = read_env_oidc_config()
    assert cfg["default_group"] == "Operators"
    assert "default_group_id" not in cfg, "resolution needs the database, not the reader"


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_default_group_is_unset(monkeypatch, raw):
    _set_required(monkeypatch)
    monkeypatch.setenv("BAMBUDDY_OIDC_DEFAULT_GROUP", raw)
    assert read_env_oidc_config()["default_group"] is None


def test_every_var_the_reader_knows_is_registered_in_the_typo_guard():
    """An unregistered BAMBUDDY_* var logs "possible typo" at every boot, which
    would tell operators their correct config is wrong. Asserted against the
    reader's own vars rather than a copied list, so a var added later is caught
    here instead of in someone's logs."""
    from backend.app.core.config import _INTENTIONAL_UNSETTINGS

    unregistered = {v for v in (*REQUIRED, *OPTIONAL) if v not in _INTENTIONAL_UNSETTINGS}
    assert not unregistered
