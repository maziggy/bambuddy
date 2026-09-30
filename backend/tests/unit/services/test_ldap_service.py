"""Tests for LDAP authentication service (#794).

Tests the pure logic functions in ldap_service.py:
- Config parsing from settings dict
- LDAP filter escaping (RFC 4515)
- Group mapping resolution
- LDAPConfig/LDAPUserInfo dataclass construction

Network-dependent functions (authenticate_ldap_user, test_ldap_connection)
are not tested here — they require a live LDAP server.
"""

from types import SimpleNamespace

import pytest
from ldap3.core.exceptions import (
    LDAPObjectClassError,
    LDAPSocketOpenError,
    LDAPStartTLSError,
    LDAPUnwillingToPerformResult,
)
from ldap3.utils.ciDict import CaseInsensitiveDict

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
    test_ldap_connection as check_ldap_connection,
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


class _MockServer:
    """Stand-in for ldap3 Server: only the schema and root DSE info the service reads.

    `schema` None is a server that published no schema, where ldap3 checks no
    names client-side. Otherwise it carries the attribute types and object
    classes the server defines.
    """

    def __init__(self, attribute_types=None, object_classes=None, naming_contexts=None, active_directory=False):
        if attribute_types is None and object_classes is None:
            self.schema = None
        else:
            self.schema = SimpleNamespace(
                attribute_types=CaseInsensitiveDict(dict.fromkeys(attribute_types or ())),
                object_classes=CaseInsensitiveDict(dict.fromkeys(object_classes or ())),
            )
        features = [("1.2.840.113556.1.4.800", "FEATURE", "Active directory", "MICROSOFT")] if active_directory else []
        if naming_contexts is None and not active_directory:
            self.info = None
        else:
            self.info = SimpleNamespace(naming_contexts=naming_contexts, supported_features=features)


class _MockConnection:
    """Mock ldap3 Connection that returns pre-configured entries based on filter substring match.

    Every Connection() instance shares a class-level fixture dict so the service-account
    connection and the user-bind connection both see the same fake directory.
    """

    _search_fixture: dict[str, list] = {}
    _instances: list["_MockConnection"] = []
    # Filter substring that should raise LDAPObjectClassError instead of
    # searching, standing in for ldap3's client-side schema validation — it
    # rejects an object class the server's published schema doesn't define
    # before the request is ever built (#2769).
    _raise_object_class_error_on: str | None = None

    def __init__(self, server=None, *args, **kwargs):
        self.server = server
        self.entries: list = []
        self.search_calls: list[str] = []
        self.search_bases: list[str | None] = []
        self.search_attrs: list[list | None] = []
        self.last_attrs: list | None = None
        _MockConnection._instances.append(self)

    def open(self):
        pass

    def start_tls(self, read_server_info=True):
        self.start_tls_read_server_info = read_server_info

    def bind(self):
        return True

    def unbind(self):
        pass

    def search(self, search_base=None, search_filter=None, search_scope=None, attributes=None, **kwargs):
        # **kwargs absorbs ldap3 options like size_limit that the real client supports
        self.search_calls.append(search_filter or "")
        self.search_bases.append(search_base)
        self.last_attrs = list(attributes) if attributes is not None else None
        self.search_attrs.append(self.last_attrs)
        needle = _MockConnection._raise_object_class_error_on
        if needle and needle in (search_filter or ""):
            raise LDAPObjectClassError(f"invalid class in objectClass attribute: {needle}")
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
    _MockConnection._raise_object_class_error_on = None
    _MockConnection.server_fixture = _MockServer()
    monkeypatch.setattr("backend.app.services.ldap_service.Connection", _MockConnection)
    monkeypatch.setattr(
        "backend.app.services.ldap_service._create_server", lambda config: _MockConnection.server_fixture
    )
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


class TestDirectoryWithoutPosixGroupClass:
    """A directory whose published schema defines no posixGroup class (#2769).

    ldap3 fetches the schema at connect time (get_info=ALL) and validates object
    class names in a filter against it before building the request, so both POSIX
    group searches raise client-side and nothing reaches the server. lldap is the
    case in the wild: it puts posixAccount on every account it creates, which
    gives each user a gidNumber, but defines no group class beyond groupOfNames.
    Left uncaught the exception escaped authenticate_ldap_user and the login route
    reported it as "Incorrect username or password", so LDAP login was impossible.
    """

    def test_authenticates_and_keeps_memberof_groups(self, mock_ldap):
        """The reporter's setup: the mapped membership comes from memberOf, which
        is read off the user entry and never touches a posixGroup filter."""
        user_entry = _MockEntry(
            "uid=peter,ou=people,dc=fablab,dc=test",
            uid="peter",
            gidNumber=1001,  # lldap gives every account one
            memberOf=["cn=AAUStudents,ou=groups,dc=fablab,dc=test"],
        )
        mock_ldap._search_fixture = {"(uid=peter)": [user_entry]}
        mock_ldap._raise_object_class_error_on = "objectClass=posixGroup"

        info = authenticate_ldap_user(_base_config(), "peter", "password")

        assert info is not None
        assert info.groups == ["cn=AAUStudents,ou=groups,dc=fablab,dc=test"]

    def test_authenticates_with_no_groups_at_all(self, mock_ldap):
        """No memberOf either. The user still gets in — auto-provisioning assigns
        the configured default group, which is the whole point of that setting."""
        user_entry = _MockEntry("uid=peter,ou=people,dc=fablab,dc=test", uid="peter", gidNumber=1001)
        mock_ldap._search_fixture = {"(uid=peter)": [user_entry]}
        mock_ldap._raise_object_class_error_on = "objectClass=posixGroup"

        info = authenticate_ldap_user(_base_config(), "peter", "password")

        assert info is not None
        assert info.username == "peter"
        assert info.groups == []

    def test_abandons_the_primary_gid_search_after_the_first_rejection(self, mock_ldap):
        """Both filters name the same class, so once one is rejected the other
        cannot succeed. Attempting it would only produce a second identical
        exception to swallow."""
        user_entry = _MockEntry("uid=peter,ou=people,dc=fablab,dc=test", uid="peter", gidNumber=1001)
        mock_ldap._search_fixture = {"(uid=peter)": [user_entry]}
        mock_ldap._raise_object_class_error_on = "objectClass=posixGroup"

        authenticate_ldap_user(_base_config(), "peter", "password")

        service_conn = _MockConnection._instances[0]
        posix_searches = [call for call in service_conn.search_calls if "posixGroup" in call]
        assert len(posix_searches) == 1
        assert "memberUid=peter" in posix_searches[0]

    def test_a_directory_that_defines_the_class_is_untouched(self, mock_ldap):
        """The guard must not cost a normal directory its POSIX groups — both
        searches still run and both results still land."""
        user_entry = _MockEntry("cn=mz,dc=test,dc=com", uid="mz", gidNumber=10002)
        supplementary = _MockEntry("cn=bambuddy-viewers,ou=groups,dc=test,dc=com")
        primary = _MockEntry("cn=bambuddy-operators,ou=groups,dc=test,dc=com")

        mock_ldap._search_fixture = {
            "(uid=mz)": [user_entry],
            "memberUid=mz": [supplementary],
            "gidNumber=10002": [primary],
        }

        info = authenticate_ldap_user(_base_config(), "mz", "password")

        assert info.groups == [
            "cn=bambuddy-viewers,ou=groups,dc=test,dc=com",
            "cn=bambuddy-operators,ou=groups,dc=test,dc=com",
        ]


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


# ---------------------------------------------------------------------------
# Group membership on directories where `*` doesn't return memberOf (#3197)
# ---------------------------------------------------------------------------

_USER_DN = "uid=tofm,ou=people,dc=example,dc=com"
_ADMINS_DN = "cn=bambuddy-admins,ou=groups,dc=example,dc=com"
_PEOPLE_BASE = "ou=people,dc=example,dc=com"


def _user_search_attrs(conn: _MockConnection) -> list | None:
    """The attribute list sent with the user search (the first search on the service connection)."""
    return conn.search_attrs[0]


def _member_searches(conn: _MockConnection) -> list[tuple[str, str | None]]:
    return [
        (flt, base)
        for flt, base in zip(conn.search_calls, conn.search_bases, strict=True)
        if "(member=" in flt or "(uniqueMember=" in flt
    ]


class TestMemberOfIsRequestedByName:
    """lldap fills in memberOf only when it is asked for by name, and OpenLDAP's
    memberof overlay makes it operational, so `*` alone returns no groups. The
    reporter's lldap user was a member of a mapped group and always got the
    default group instead."""

    def test_login_asks_for_memberof_when_the_schema_has_it(self, mock_ldap):
        mock_ldap.server_fixture = _MockServer(
            attribute_types=["uid", "memberOf"], object_classes=["groupOfUniqueNames"]
        )
        user_entry = _MockEntry(_USER_DN, uid="tofm", memberOf=[_ADMINS_DN])
        mock_ldap._search_fixture = {"(uid=tofm)": [user_entry]}

        info = authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        service_conn = _MockConnection._instances[0]
        assert _user_search_attrs(service_conn) == ["*", "memberOf"]
        assert info.groups == [_ADMINS_DN]

    def test_schema_match_is_case_insensitive(self, mock_ldap):
        """Schemas spell it memberof, memberOf or MemberOf; ldap3's schema dict ignores case."""
        mock_ldap.server_fixture = _MockServer(attribute_types=["memberof"], object_classes=[])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}

        authenticate_ldap_user(_base_config(), "tofm", "password")

        assert _user_search_attrs(_MockConnection._instances[0]) == ["*", "memberOf"]

    def test_groups_are_asked_as_well_as_memberof(self, mock_ldap):
        """OpenLDAP's memberof overlay tracks only the group class it was set up
        for (osixia's image: groupOfUniqueNames), so a groupOfNames group is
        missing from memberOf even though the schema has the attribute."""
        mock_ldap.server_fixture = _MockServer(
            attribute_types=["memberOf"],
            object_classes=["groupOfNames", "groupOfUniqueNames"],
            naming_contexts=["dc=example,dc=com"],
        )
        operators_dn = "cn=bambuddy-operators,ou=groups,dc=example,dc=com"
        mock_ldap._search_fixture = {
            "(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm", memberOf=[operators_dn])],
            f"(member={_USER_DN})": [_MockEntry(_ADMINS_DN), _MockEntry(operators_dn)],
        }

        info = authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        assert info.groups == [operators_dn, _ADMINS_DN]

    def test_no_group_side_search_on_active_directory(self, mock_ldap):
        """AD keeps memberOf complete, and its groups are objectClass=group, so a
        subtree search from the domain root would find nothing."""
        mock_ldap.server_fixture = _MockServer(
            attribute_types=["memberOf", "member"],
            object_classes=["group", "groupOfNames"],
            naming_contexts=["dc=example,dc=com"],
            active_directory=True,
        )
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm", memberOf=[_ADMINS_DN])]}

        info = authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        assert info.groups == [_ADMINS_DN]
        assert _member_searches(_MockConnection._instances[0]) == []

    def test_admin_lookup_asks_for_memberof_too(self, mock_ldap):
        mock_ldap.server_fixture = _MockServer(attribute_types=["memberOf"], object_classes=[])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm", memberOf=[_ADMINS_DN])]}

        info = lookup_ldap_user(_base_config(), "tofm")

        assert _user_search_attrs(_MockConnection._instances[0]) == ["*", "memberOf"]
        assert info.groups == [_ADMINS_DN]

    def test_memberof_not_requested_when_the_schema_lacks_it(self, mock_ldap):
        """ldap3 rejects a requested attribute the schema doesn't define before
        sending anything, even with check_names off. Asking anyway would make
        every login on such a directory fail."""
        mock_ldap.server_fixture = _MockServer(attribute_types=["uid", "cn"], object_classes=[])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}

        authenticate_ldap_user(_base_config(), "tofm", "password")

        assert _user_search_attrs(_MockConnection._instances[0]) == ["*"]

    def test_memberof_requested_when_the_server_publishes_no_schema(self, mock_ldap):
        """Without a schema ldap3 checks no names, and the server ignores an
        attribute it doesn't know."""
        mock_ldap.server_fixture = _MockServer()
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}

        authenticate_ldap_user(_base_config(), "tofm", "password")

        assert _user_search_attrs(_MockConnection._instances[0]) == ["*", "memberOf"]


class TestGroupsListingTheUserByDn:
    """Group membership asked from the group side. Plain OpenLDAP without the
    memberof overlay can answer it no other way."""

    def _server(self, object_classes=("groupOfNames", "groupOfUniqueNames"), naming_contexts=("dc=example,dc=com",)):
        return _MockServer(
            attribute_types=["uid", "cn", "member", "uniqueMember"],
            object_classes=list(object_classes),
            naming_contexts=list(naming_contexts),
        )

    def test_finds_a_group_outside_the_user_search_base(self, mock_ldap):
        """The reporter's layout: users under ou=people, groups under ou=groups.
        The group search starts at the naming context, not the user search base."""
        mock_ldap.server_fixture = self._server()
        mock_ldap._search_fixture = {
            "(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")],
            f"(member={_USER_DN})": [_MockEntry(_ADMINS_DN)],
        }

        info = authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        assert info.groups == [_ADMINS_DN]
        searches = _member_searches(_MockConnection._instances[0])
        assert len(searches) == 1
        flt, base = searches[0]
        assert base == "dc=example,dc=com"
        assert flt == (
            f"(|(&(objectClass=groupOfNames)(member={_USER_DN}))"
            f"(&(objectClass=groupOfUniqueNames)(uniqueMember={_USER_DN})))"
        )

    def test_only_classes_the_schema_defines_go_into_the_filter(self, mock_ldap):
        """Naming an undefined class raises client-side, the #2769 failure."""
        mock_ldap.server_fixture = self._server(object_classes=["groupOfUniqueNames"])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}

        authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        (flt, _base) = _member_searches(_MockConnection._instances[0])[0]
        assert flt == f"(&(objectClass=groupOfUniqueNames)(uniqueMember={_USER_DN}))"

    def test_no_search_when_the_schema_has_neither_class(self, mock_ldap):
        mock_ldap.server_fixture = self._server(object_classes=["posixGroup"])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}

        info = authenticate_ldap_user(_base_config(), "tofm", "password")

        assert info.groups == []
        assert _member_searches(_MockConnection._instances[0]) == []

    def test_dn_is_escaped_in_the_filter(self, mock_ldap):
        """A DN may carry filter metacharacters (an escaped comma, a parenthesis)."""
        dn = r"cn=Doe\, John (ops),ou=people,dc=example,dc=com"
        mock_ldap.server_fixture = self._server(object_classes=["groupOfNames"])
        mock_ldap._search_fixture = {"(uid=jdoe)": [_MockEntry(dn, uid="jdoe")]}

        authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "jdoe", "password")

        (flt, _base) = _member_searches(_MockConnection._instances[0])[0]
        assert flt == r"(&(objectClass=groupOfNames)(member=cn=Doe\5c, John \28ops\29,ou=people,dc=example,dc=com))"

    def test_search_base_is_used_when_no_naming_context_contains_it(self, mock_ldap):
        mock_ldap.server_fixture = self._server(naming_contexts=["dc=other,dc=org"])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}

        authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        (_flt, base) = _member_searches(_MockConnection._instances[0])[0]
        assert base == _PEOPLE_BASE

    def test_naming_context_match_is_on_whole_components(self, mock_ldap):
        """dc=ample,dc=com is not a suffix of ou=people,dc=example,dc=com."""
        mock_ldap.server_fixture = self._server(naming_contexts=["dc=ample,dc=com", "DC=Example,DC=Com"])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}

        authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        (_flt, base) = _member_searches(_MockConnection._instances[0])[0]
        assert base == "DC=Example,DC=Com"

    def test_dedupes_against_posix_groups(self, mock_ldap):
        """A group can be both a groupOfNames and a posixGroup (OpenLDAP rfc2307bis)."""
        mock_ldap.server_fixture = self._server(object_classes=["groupOfNames", "posixGroup"])
        mock_ldap._search_fixture = {
            "(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")],
            f"(member={_USER_DN})": [_MockEntry(_ADMINS_DN)],
            "memberUid=tofm": [_MockEntry(_ADMINS_DN.upper())],
        }

        info = authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")

        assert info.groups == [_ADMINS_DN]

    def test_a_failed_group_search_still_logs_the_user_in(self, mock_ldap, caplog):
        """The user gets the groups found by other means, and the log names the
        failure without the DN it may contain (#2681)."""

        class _GroupSearchFails(_MockConnection):
            def search(self, search_base=None, search_filter=None, **kwargs):
                if "(member=" in (search_filter or ""):
                    raise LDAPObjectClassError(f"size limit on {_USER_DN}")
                return super().search(search_base=search_base, search_filter=search_filter, **kwargs)

        import backend.app.services.ldap_service as ldap_service

        mock_ldap.server_fixture = self._server(object_classes=["groupOfNames"])
        mock_ldap._search_fixture = {"(uid=tofm)": [_MockEntry(_USER_DN, uid="tofm")]}
        original = ldap_service.Connection
        ldap_service.Connection = _GroupSearchFails
        try:
            with caplog.at_level("WARNING", logger="backend.app.services.ldap_service"):
                info = authenticate_ldap_user(_base_config(search_base=_PEOPLE_BASE), "tofm", "password")
        finally:
            ldap_service.Connection = original

        assert info is not None
        assert info.groups == []
        assert "LDAP group membership lookup failed (LDAPObjectClassError)" in caplog.text
        assert _USER_DN not in caplog.text


class TestStartTlsRefused:
    """lldap offers LDAPS only. Its answer to StartTLS is "Unsupported extended
    operation" plus the StartTLS OID, which the reporter had to decode."""

    def _refusing(self, error):
        class _Conn(_MockConnection):
            def start_tls(self, read_server_info=True):
                raise error

        return _Conn

    def test_connection_test_says_what_to_change(self, mock_ldap, monkeypatch):
        refusal = LDAPUnwillingToPerformResult(
            result=53,
            description="unwillingToPerform",
            message="Unsupported extended operation: 1.3.6.1.4.1.1466.20037",
            response_type="extendedResp",
        )
        monkeypatch.setattr("backend.app.services.ldap_service.Connection", self._refusing(refusal))

        ok, message = check_ldap_connection(_base_config(server_url="ldap://lldap:3890", security="starttls"))

        assert ok is False
        assert message == (
            "LDAP connection failed: the server refused StartTLS (unwillingToPerform). "
            "If it only offers LDAPS, choose LDAPS and use its ldaps:// URL and port"
        )

    def test_login_with_refused_starttls_fails_cleanly(self, mock_ldap, monkeypatch):
        refusal = LDAPUnwillingToPerformResult(result=53, description="unwillingToPerform")
        monkeypatch.setattr("backend.app.services.ldap_service.Connection", self._refusing(refusal))

        assert authenticate_ldap_user(_base_config(server_url="ldap://x", security="starttls"), "u", "p") is None

    @pytest.mark.parametrize(
        "error",
        [LDAPStartTLSError("wrap socket error: certificate verify failed"), LDAPSocketOpenError("reset")],
    )
    def test_tls_failures_keep_their_own_message(self, mock_ldap, monkeypatch, error):
        """Only a refusal by the server is reworded; a certificate problem is not a missing feature."""
        monkeypatch.setattr("backend.app.services.ldap_service.Connection", self._refusing(error))

        ok, message = check_ldap_connection(_base_config(server_url="ldap://x", security="starttls"))

        assert ok is False
        assert message == f"LDAP connection failed: {error}"
        assert "refused StartTLS" not in message

    def test_ldaps_never_sends_starttls(self, mock_ldap, monkeypatch):
        refusal = LDAPUnwillingToPerformResult(result=53, description="unwillingToPerform")
        monkeypatch.setattr("backend.app.services.ldap_service.Connection", self._refusing(refusal))

        ok, _message = check_ldap_connection(_base_config(server_url="ldaps://x:636", security="starttls"))

        assert ok is True

    def test_server_info_is_not_read_before_the_bind(self, mock_ldap):
        """ldap3's default re-reads the schema right after StartTLS, before any
        bind; Active Directory and Samba AD refuse that anonymous read with
        operationsError, so StartTLS never worked against them. bind() reads it
        once authenticated."""
        mock_ldap._search_fixture = {"(uid=u)": [_MockEntry("uid=u,dc=test,dc=com", uid="u")]}

        authenticate_ldap_user(_base_config(server_url="ldap://ad:389", security="starttls"), "u", "p")

        service_conn, user_conn = _MockConnection._instances
        assert service_conn.start_tls_read_server_info is False
        assert user_conn.start_tls_read_server_info is False
