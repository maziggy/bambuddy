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
    LDAPSearchResult,
    LDAPUserInfo,
    _ldap_escape,
    authenticate_ldap_user,
    lookup_ldap_user,
    parse_ldap_config,
    resolve_group_mapping,
    search_ldap_users,
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
        self.last_attrs: list | None = None
        _MockConnection._instances.append(self)

    def open(self):
        pass

    def start_tls(self):
        pass

    def bind(self):
        return True

    def unbind(self):
        pass

    def search(self, search_base=None, search_filter=None, search_scope=None, attributes=None, **kwargs):
        # **kwargs absorbs ldap3 options like size_limit that the real client supports
        self.search_calls.append(search_filter or "")
        self.last_attrs = list(attributes) if attributes is not None else None
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


# ---------------------------------------------------------------------------
# Manual provisioning helpers — search_ldap_users + lookup_ldap_user (#1298)
# ---------------------------------------------------------------------------


class TestSearchLdapUsers:
    """Admin directory search for the manual-provision flow."""

    def test_returns_empty_when_query_too_short(self, mock_ldap):
        """Queries under 2 chars must not hit the directory at all."""
        results = search_ldap_users(_base_config(), "a")
        assert results == []
        # No connection was opened — no Connection instance recorded.
        assert _MockConnection._instances == []

    def test_returns_empty_when_query_whitespace(self, mock_ldap):
        results = search_ldap_users(_base_config(), "   ")
        assert results == []
        assert _MockConnection._instances == []

    def test_filter_covers_all_common_attributes(self, mock_ldap):
        """The fixed OR filter must cover sAMAccountName, uid, mail, displayName, cn."""
        _MockConnection._search_fixture = {}  # any matching attr; empty result is fine
        search_ldap_users(_base_config(), "jdoe")

        assert len(_MockConnection._instances) == 1
        sent = _MockConnection._instances[0].search_calls[0]
        for attr in ("sAMAccountName=*jdoe*", "uid=*jdoe*", "mail=*jdoe*", "displayName=*jdoe*", "cn=*jdoe*"):
            assert attr in sent, f"filter missing {attr}: {sent}"

    def test_wildcard_in_query_is_escaped(self, mock_ldap):
        """A typed * in the query must not enumerate the whole directory."""
        _MockConnection._search_fixture = {}
        search_ldap_users(_base_config(), "j*")

        sent = _MockConnection._instances[0].search_calls[0]
        # _ldap_escape replaces * with \2a; the outer wildcards (from our filter)
        # must remain, but the user-supplied * must be escaped.
        assert "*j\\2a*" in sent

    def test_picks_samaccountname_first(self, mock_ldap):
        entry = _MockEntry(
            "cn=John Doe,dc=test,dc=com",
            sAMAccountName="jdoe",
            uid="jdoe-uid",
            mail="jdoe@test.com",
            displayName="John Doe",
            cn="John Doe",
        )
        _MockConnection._search_fixture = {"sAMAccountName=*jdoe*": [entry]}

        results = search_ldap_users(_base_config(), "jdoe")

        assert len(results) == 1
        assert isinstance(results[0], LDAPSearchResult)
        assert results[0].username == "jdoe"  # sAMAccountName preferred
        assert results[0].email == "jdoe@test.com"
        assert results[0].display_name == "John Doe"
        assert results[0].dn == "cn=John Doe,dc=test,dc=com"

    def test_falls_back_to_uid_when_no_samaccountname(self, mock_ldap):
        entry = _MockEntry("uid=alice,ou=people,dc=test,dc=com", uid="alice", cn="Alice")
        _MockConnection._search_fixture = {"uid=*alice*": [entry]}

        results = search_ldap_users(_base_config(), "alice")

        assert len(results) == 1
        assert results[0].username == "alice"

    def test_falls_back_to_cn_when_neither_samaccountname_nor_uid(self, mock_ldap):
        """Some OpenLDAP layouts only have cn — make sure we still surface them."""
        entry = _MockEntry("cn=Bob,ou=people,dc=test,dc=com", cn="Bob")
        _MockConnection._search_fixture = {"cn=*Bob*": [entry]}

        results = search_ldap_users(_base_config(), "Bob")

        assert len(results) == 1
        assert results[0].username == "Bob"

    def test_raises_when_service_bind_fails(self, mock_ldap, monkeypatch):
        """Bind failures must propagate so the route can return 503 instead of [] (which
        would look indistinguishable from 'no matches found' to the admin)."""

        class _BindFailConn(_MockConnection):
            def bind(self):
                raise RuntimeError("simulated bind failure")

        monkeypatch.setattr("backend.app.services.ldap_service.Connection", _BindFailConn)

        with pytest.raises(RuntimeError):
            search_ldap_users(_base_config(), "anyone")

    def test_connection_skips_client_side_attribute_validation(self, mock_ldap, monkeypatch):
        """OpenLDAP directories don't define sAMAccountName/displayName in their schema,
        so ldap3 would raise LDAPAttributeError client-side before sending the query
        — break the regression by asserting Connection is opened with check_names=False
        for directory search."""
        captured_kwargs: dict = {}

        class _CapturingConn(_MockConnection):
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr("backend.app.services.ldap_service.Connection", _CapturingConn)

        search_ldap_users(_base_config(), "anyone")

        assert captured_kwargs.get("check_names") is False, (
            "search_ldap_users must open the connection with check_names=False — "
            "otherwise ldap3 rejects sAMAccountName/displayName on OpenLDAP schemas"
        )

    def test_requests_all_user_attributes_to_bypass_schema_check(self, mock_ldap):
        """ldap3's `build_attribute_selection` validates each named attribute against
        the server schema regardless of check_names; only the `*` wildcard is in
        its hard-coded exclusion list. So search_ldap_users MUST request `["*"]`
        — not the explicit AD-flavoured names — or OpenLDAP servers raise
        `LDAPAttributeError: invalid attribute type in attribute list: sAMAccountName`."""
        _MockConnection._search_fixture = {}
        search_ldap_users(_base_config(), "anyone")

        # The mock's search() captures search_filter in search_calls but not
        # attributes — so monkeypatch its signature briefly to capture both.
        # Easier: re-grep ldap3 here. The mock's search() accepts kwargs via
        # **kwargs; we just need to verify the attributes arg was the wildcard.
        sent_attrs = _MockConnection._instances[0].last_attrs  # set by patched search
        assert sent_attrs == ["*"], (
            f"Expected attributes=['*'] to bypass ldap3 schema validation; got {sent_attrs!r}. "
            "Explicit AD attribute names (sAMAccountName, displayName) make ldap3 throw on "
            "OpenLDAP directories whose schema doesn't define them."
        )


class TestLookupLdapUser:
    """Service-bind lookup used by the manual-provision route."""

    def test_returns_none_when_user_missing(self, mock_ldap):
        _MockConnection._search_fixture = {}  # nothing matches

        result = lookup_ldap_user(_base_config(), "nobody")

        assert result is None

    def test_returns_user_info_with_groups(self, mock_ldap):
        user_entry = _MockEntry(
            "cn=John Doe,dc=test,dc=com",
            uid="jdoe",
            mail="jdoe@test.com",
            displayName="John Doe",
            memberOf=["cn=ops,ou=groups,dc=test,dc=com", "cn=qa,ou=groups,dc=test,dc=com"],
        )
        _MockConnection._search_fixture = {"(uid=jdoe)": [user_entry]}

        info = lookup_ldap_user(_base_config(), "jdoe")

        assert info is not None
        assert info.username == "jdoe"
        assert info.email == "jdoe@test.com"
        assert info.display_name == "John Doe"
        assert set(info.groups) == {"cn=ops,ou=groups,dc=test,dc=com", "cn=qa,ou=groups,dc=test,dc=com"}

    def test_does_not_attempt_password_bind(self, mock_ldap):
        """lookup_ldap_user MUST NOT call the user-DN bind that authenticate_ldap_user
        does — admins are using their own session, not the LDAP user's password."""
        user_entry = _MockEntry("cn=jdoe,dc=test,dc=com", uid="jdoe")
        _MockConnection._search_fixture = {"(uid=jdoe)": [user_entry]}

        lookup_ldap_user(_base_config(), "jdoe")

        # authenticate_ldap_user creates TWO Connection objects (service + user-bind).
        # lookup_ldap_user must create only ONE.
        assert len(_MockConnection._instances) == 1

    def test_raises_when_service_bind_fails(self, mock_ldap, monkeypatch):
        class _BindFailConn(_MockConnection):
            def bind(self):
                raise RuntimeError("simulated bind failure")

        monkeypatch.setattr("backend.app.services.ldap_service.Connection", _BindFailConn)

        with pytest.raises(RuntimeError):
            lookup_ldap_user(_base_config(), "anyone")
