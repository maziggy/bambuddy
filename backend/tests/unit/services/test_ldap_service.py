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
        }
        config = parse_ldap_config(settings)
        assert config.server_url == "ldaps://ldap.example.com"
        assert config.bind_dn == "cn=admin,dc=example,dc=com"
        assert config.search_base == "dc=example,dc=com"


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
        )
        assert config.server_url == "ldaps://ldap.example.com:636"
        assert config.auto_provision is True
