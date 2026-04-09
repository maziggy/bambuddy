"""Tests for LDAP authentication service (#794).

Tests the pure logic functions in ldap_service.py:
- Config parsing from settings dict
- LDAP filter escaping (RFC 4515)
- Group mapping resolution
- LDAPConfig/LDAPUserInfo dataclass construction

Network-dependent functions (authenticate_ldap_user, test_ldap_connection)
are not tested here — they require a live LDAP server.
"""

import pytest

from backend.app.services.ldap_service import (
    LDAPConfig,
    LDAPUserInfo,
    _ldap_escape,
    authenticate_ldap_user,
    parse_ldap_config,
    resolve_group_mapping,
)


class TestParseConfig:
    """Verify parse_ldap_config builds LDAPConfig from settings dict."""

    def test_returns_none_when_disabled(self):
        settings = {"ldap_enabled": "false", "ldap_server_url": "ldaps://example.com"}
        assert parse_ldap_config(settings) is None

    def test_returns_none_when_missing_enabled(self):
        settings = {"ldap_server_url": "ldaps://example.com"}
        assert parse_ldap_config(settings) is None

    def test_returns_none_when_no_server_url(self):
        settings = {"ldap_enabled": "true", "ldap_server_url": ""}
        assert parse_ldap_config(settings) is None

    def test_returns_none_when_server_url_whitespace(self):
        settings = {"ldap_enabled": "true", "ldap_server_url": "   "}
        assert parse_ldap_config(settings) is None

    def test_parses_minimal_config(self):
        settings = {
            "ldap_enabled": "true",
            "ldap_server_url": "ldaps://ldap.example.com:636",
        }
        config = parse_ldap_config(settings)
        assert config is not None
        assert config.server_url == "ldaps://ldap.example.com:636"
        assert config.bind_dn == ""
        assert config.search_base == ""
        assert config.user_filter == "(sAMAccountName={username})"
        assert config.security == "starttls"
        assert config.group_mapping == {}
        assert config.auto_provision is False
        assert config.ca_cert_path == ""
        assert config.default_group == ""

    def test_parses_full_config(self):
        settings = {
            "ldap_enabled": "true",
            "ldap_server_url": "ldaps://ldap.example.com:636",
            "ldap_bind_dn": "cn=admin,dc=example,dc=com",
            "ldap_bind_password": "secret",
            "ldap_search_base": "ou=users,dc=example,dc=com",
            "ldap_user_filter": "(uid={username})",
            "ldap_security": "ldaps",
            "ldap_group_mapping": '{"cn=admins,dc=example,dc=com": "Administrators"}',
            "ldap_auto_provision": "true",
            "ldap_ca_cert_path": "/path/to/ca.pem",
            "ldap_default_group": "Viewers",
        }
        config = parse_ldap_config(settings)
        assert config is not None
        assert config.bind_dn == "cn=admin,dc=example,dc=com"
        assert config.bind_password == "secret"
        assert config.search_base == "ou=users,dc=example,dc=com"
        assert config.user_filter == "(uid={username})"
        assert config.security == "ldaps"
        assert config.group_mapping == {"cn=admins,dc=example,dc=com": "Administrators"}
        assert config.auto_provision is True
        assert config.ca_cert_path == "/path/to/ca.pem"
        assert config.default_group == "Viewers"

    def test_handles_invalid_group_mapping_json(self):
        settings = {
            "ldap_enabled": "true",
            "ldap_server_url": "ldaps://ldap.example.com",
            "ldap_group_mapping": "not valid json",
        }
        config = parse_ldap_config(settings)
        assert config is not None
        assert config.group_mapping == {}

    def test_handles_non_dict_group_mapping(self):
        settings = {
            "ldap_enabled": "true",
            "ldap_server_url": "ldaps://ldap.example.com",
            "ldap_group_mapping": '["not", "a", "dict"]',
        }
        config = parse_ldap_config(settings)
        assert config is not None
        assert config.group_mapping == {}

    def test_enabled_case_insensitive(self):
        settings = {"ldap_enabled": "True", "ldap_server_url": "ldaps://ldap.example.com"}
        assert parse_ldap_config(settings) is not None

        settings = {"ldap_enabled": "TRUE", "ldap_server_url": "ldaps://ldap.example.com"}
        assert parse_ldap_config(settings) is not None

    def test_strips_whitespace(self):
        settings = {
            "ldap_enabled": "true",
            "ldap_server_url": "  ldaps://ldap.example.com  ",
            "ldap_bind_dn": "  cn=admin,dc=example,dc=com  ",
            "ldap_search_base": "  dc=example,dc=com  ",
            "ldap_default_group": "  Viewers  ",
        }
        config = parse_ldap_config(settings)
        assert config.server_url == "ldaps://ldap.example.com"
        assert config.bind_dn == "cn=admin,dc=example,dc=com"
        assert config.search_base == "dc=example,dc=com"
        assert config.default_group == "Viewers"


class TestLDAPEscape:
    """Verify RFC 4515 escaping for LDAP search filter values."""

    def test_plain_string(self):
        assert _ldap_escape("testuser") == "testuser"

    def test_escapes_backslash(self):
        assert _ldap_escape("test\\user") == "test\\5cuser"

    def test_escapes_asterisk(self):
        assert _ldap_escape("test*user") == "test\\2auser"

    def test_escapes_open_paren(self):
        assert _ldap_escape("test(user") == "test\\28user"

    def test_escapes_close_paren(self):
        assert _ldap_escape("test)user") == "test\\29user"

    def test_escapes_null(self):
        assert _ldap_escape("test\x00user") == "test\\00user"

    def test_escapes_multiple_chars(self):
        assert _ldap_escape("a*b(c)d\\e") == "a\\2ab\\28c\\29d\\5ce"

    def test_empty_string(self):
        assert _ldap_escape("") == ""


class TestResolveGroupMapping:
    """Verify LDAP group DN to BamBuddy group name resolution."""

    def test_empty_mapping(self):
        assert resolve_group_mapping(["cn=admins,dc=example"], {}) == []

    def test_empty_groups(self):
        mapping = {"cn=admins,dc=example": "Administrators"}
        assert resolve_group_mapping([], mapping) == []

    def test_single_match(self):
        mapping = {"cn=admins,dc=example,dc=com": "Administrators"}
        groups = ["cn=admins,dc=example,dc=com"]
        assert resolve_group_mapping(groups, mapping) == ["Administrators"]

    def test_multiple_matches(self):
        mapping = {
            "cn=admins,dc=example,dc=com": "Administrators",
            "cn=ops,dc=example,dc=com": "Operators",
        }
        groups = ["cn=admins,dc=example,dc=com", "cn=ops,dc=example,dc=com"]
        result = resolve_group_mapping(groups, mapping)
        assert set(result) == {"Administrators", "Operators"}

    def test_no_match(self):
        mapping = {"cn=admins,dc=example,dc=com": "Administrators"}
        groups = ["cn=users,dc=example,dc=com"]
        assert resolve_group_mapping(groups, mapping) == []

    def test_case_insensitive_dn(self):
        mapping = {"CN=Admins,DC=Example,DC=Com": "Administrators"}
        groups = ["cn=admins,dc=example,dc=com"]
        assert resolve_group_mapping(groups, mapping) == ["Administrators"]

    def test_partial_match_not_matched(self):
        mapping = {"cn=admins,dc=example,dc=com": "Administrators"}
        groups = ["cn=admins,dc=other,dc=com"]
        assert resolve_group_mapping(groups, mapping) == []

    def test_extra_groups_ignored(self):
        mapping = {"cn=admins,dc=example,dc=com": "Administrators"}
        groups = ["cn=admins,dc=example,dc=com", "cn=users,dc=example,dc=com", "cn=devs,dc=example,dc=com"]
        assert resolve_group_mapping(groups, mapping) == ["Administrators"]


class TestDataclasses:
    """Verify dataclass construction."""

    def test_ldap_user_info(self):
        info = LDAPUserInfo(
            username="testuser",
            email="test@example.com",
            display_name="Test User",
            groups=["cn=admins,dc=example,dc=com"],
        )
        assert info.username == "testuser"
        assert info.email == "test@example.com"
        assert info.display_name == "Test User"
        assert info.groups == ["cn=admins,dc=example,dc=com"]

    def test_ldap_user_info_none_fields(self):
        info = LDAPUserInfo(username="testuser", email=None, display_name=None, groups=[])
        assert info.email is None
        assert info.display_name is None
        assert info.groups == []

    def test_ldap_config(self):
        config = LDAPConfig(
            server_url="ldaps://ldap.example.com:636",
            bind_dn="cn=admin,dc=example,dc=com",
            bind_password="secret",
            search_base="dc=example,dc=com",
            user_filter="(uid={username})",
            security="ldaps",
            group_mapping={"cn=admins": "Administrators"},
            auto_provision=True,
            ca_cert_path="",
            default_group="Viewers",
        )
        assert config.server_url == "ldaps://ldap.example.com:636"
        assert config.auto_provision is True
        assert config.default_group == "Viewers"


# ---------------------------------------------------------------------------
# Mocked authenticate_ldap_user group-discovery tests
# ---------------------------------------------------------------------------
# These tests mock ldap3.Connection to exercise the group-discovery logic in
# authenticate_ldap_user without a live LDAP server. Added after a bug where
# POSIX primary-group membership (via gidNumber) was ignored — see CHANGELOG.


class _MockAttr:
    """Minimal stand-in for ldap3 Attribute objects.

    Supports str(), bool(), .value, .values, and iteration — the operations
    used by ldap_service against user entry attributes.
    """

    def __init__(self, value):
        self._value = value

    @property
    def value(self):
        return self._value

    @property
    def values(self):
        return self._value if isinstance(self._value, list) else [self._value]

    def __str__(self):
        return str(self._value)

    def __bool__(self):
        return bool(self._value)

    def __iter__(self):
        if isinstance(self._value, list):
            return iter(self._value)
        return iter([self._value])


class _MockEntry:
    """Minimal stand-in for ldap3 Entry. Only attributes passed at construction exist."""

    def __init__(self, dn, **attrs):
        self.entry_dn = dn
        for key, val in attrs.items():
            setattr(self, key, _MockAttr(val))


class _MockConnection:
    """Mock ldap3 Connection that returns pre-configured entries based on filter substring match.

    Every Connection() instance shares a class-level fixture dict so the service-account
    connection and the user-bind connection both see the same fake directory.
    """

    _search_fixture: dict[str, list] = {}
    _instances: list["_MockConnection"] = []

    def __init__(self, *args, **kwargs):
        self.entries: list = []
        self.search_calls: list[str] = []
        _MockConnection._instances.append(self)

    def open(self):
        pass

    def start_tls(self):
        pass

    def bind(self):
        return True

    def unbind(self):
        pass

    def search(self, search_base=None, search_filter=None, search_scope=None, attributes=None):
        self.search_calls.append(search_filter or "")
        for needle, entries in _MockConnection._search_fixture.items():
            if needle in (search_filter or ""):
                self.entries = entries
                return True
        self.entries = []
        return True


@pytest.fixture
def mock_ldap(monkeypatch):
    """Patch Connection + _create_server in ldap_service so authenticate_ldap_user can run offline."""
    _MockConnection._search_fixture = {}
    _MockConnection._instances = []
    monkeypatch.setattr("backend.app.services.ldap_service.Connection", _MockConnection)
    monkeypatch.setattr("backend.app.services.ldap_service._create_server", lambda config: None)
    return _MockConnection


def _base_config(**overrides):
    """Build a minimal LDAPConfig for mocked tests."""
    defaults = {
        "server_url": "ldaps://test.example.com:636",
        "bind_dn": "cn=admin,dc=test,dc=com",
        "bind_password": "x",
        "search_base": "dc=test,dc=com",
        "user_filter": "(uid={username})",
        "security": "ldaps",
        "group_mapping": {},
        "auto_provision": False,
        "ca_cert_path": "",
        "default_group": "",
    }
    defaults.update(overrides)
    return LDAPConfig(**defaults)


class TestAuthenticateLdapUserGroups:
    """Group-discovery behaviour in authenticate_ldap_user.

    Covers the POSIX primary gidNumber lookup and case-insensitive dedupe added
    to fix a bug where users whose role came from their primary group were
    authenticated without the correct group membership.
    """

    def test_primary_gidnumber_group_found(self, mock_ldap):
        """Regression: POSIX primary group (gidNumber match) must be included in the result."""
        user_entry = _MockEntry("cn=mz,dc=test,dc=com", uid="mz", gidNumber=10002)
        operators_group = _MockEntry("cn=bambuddy-operators,ou=groups,dc=test,dc=com")

        mock_ldap._search_fixture = {
            "(uid=mz)": [user_entry],
            "memberUid=mz": [],  # no supplementary memberships
            "gidNumber=10002": [operators_group],
        }

        info = authenticate_ldap_user(_base_config(), "mz", "password")

        assert info is not None
        assert info.groups == ["cn=bambuddy-operators,ou=groups,dc=test,dc=com"]

    def test_dedupes_group_found_via_both_memberuid_and_primary_gid(self, mock_ldap):
        """A user in the same group via BOTH memberUid and primary gidNumber should appear once."""
        user_entry = _MockEntry("cn=mz,dc=test,dc=com", uid="mz", gidNumber=10002)
        group_entry = _MockEntry("cn=bambuddy-operators,ou=groups,dc=test,dc=com")

        mock_ldap._search_fixture = {
            "(uid=mz)": [user_entry],
            "memberUid=mz": [group_entry],  # supplementary membership
            "gidNumber=10002": [group_entry],  # primary group — same DN
        }

        info = authenticate_ldap_user(_base_config(), "mz", "password")

        assert info.groups == ["cn=bambuddy-operators,ou=groups,dc=test,dc=com"]

    def test_case_insensitive_dedupe(self, mock_ldap):
        """DNs differing only in case should collapse to a single entry (LDAP DNs are case-insensitive)."""
        user_entry = _MockEntry("cn=mz,dc=test,dc=com", uid="mz", gidNumber=10002)
        upper_dn = _MockEntry("CN=Bambuddy-Operators,OU=Groups,DC=Test,DC=Com")
        lower_dn = _MockEntry("cn=bambuddy-operators,ou=groups,dc=test,dc=com")

        mock_ldap._search_fixture = {
            "(uid=mz)": [user_entry],
            "memberUid=mz": [upper_dn],
            "gidNumber=10002": [lower_dn],
        }

        info = authenticate_ldap_user(_base_config(), "mz", "password")

        assert len(info.groups) == 1
        # The first-seen casing (memberUid result) is kept.
        assert info.groups[0] == "CN=Bambuddy-Operators,OU=Groups,DC=Test,DC=Com"

    def test_no_gidnumber_skips_primary_search(self, mock_ldap):
        """User entries without a gidNumber attribute should not crash and should not issue the primary-gid query."""
        user_entry = _MockEntry("cn=tester,dc=test,dc=com", uid="tester")  # no gidNumber
        viewers_group = _MockEntry("cn=bambuddy-viewers,ou=groups,dc=test,dc=com")

        mock_ldap._search_fixture = {
            "(uid=tester)": [user_entry],
            "memberUid=tester": [viewers_group],
        }

        info = authenticate_ldap_user(_base_config(), "tester", "password")

        assert info is not None
        assert info.groups == ["cn=bambuddy-viewers,ou=groups,dc=test,dc=com"]
        # Ensure the primary-gidNumber search was never issued — verifying the guard works.
        service_conn = _MockConnection._instances[0]
        gidnumber_searches = [call for call in service_conn.search_calls if "gidNumber=" in call]
        assert gidnumber_searches == []
