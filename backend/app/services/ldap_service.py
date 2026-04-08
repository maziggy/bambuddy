"""LDAP authentication service for BamBuddy (#794).

Supports:
- LDAP bind authentication (simple bind with user's credentials)
- StartTLS, LDAPS, and plaintext connections
- User search with configurable filter
- Group membership resolution for role mapping
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ldap3 import ALL, SUBTREE, Connection, Server, Tls

logger = logging.getLogger(__name__)


@dataclass
class LDAPUserInfo:
    """User information retrieved from LDAP after successful authentication."""

    username: str
    email: str | None
    display_name: str | None
    groups: list[str]  # List of group DNs the user belongs to


@dataclass
class LDAPConfig:
    """LDAP configuration parsed from settings."""

    server_url: str
    bind_dn: str
    bind_password: str
    search_base: str
    user_filter: str  # e.g. "(sAMAccountName={username})"
    security: str  # "none", "starttls", "ldaps"
    group_mapping: dict[str, str]  # LDAP group DN -> BamBuddy group name
    auto_provision: bool
    ca_cert_path: str  # Path to CA certificate file (empty = skip verification)


def parse_ldap_config(settings: dict[str, str]) -> LDAPConfig | None:
    """Parse LDAP config from settings key-value pairs. Returns None if LDAP not enabled."""
    if settings.get("ldap_enabled", "false").lower() != "true":
        return None

    server_url = settings.get("ldap_server_url", "").strip()
    if not server_url:
        return None

    group_mapping_raw = settings.get("ldap_group_mapping", "")
    try:
        group_mapping = json.loads(group_mapping_raw) if group_mapping_raw else {}
    except json.JSONDecodeError:
        group_mapping = {}

    return LDAPConfig(
        server_url=server_url,
        bind_dn=settings.get("ldap_bind_dn", "").strip(),
        bind_password=settings.get("ldap_bind_password", ""),
        search_base=settings.get("ldap_search_base", "").strip(),
        user_filter=settings.get("ldap_user_filter", "(sAMAccountName={username})").strip(),
        security=settings.get("ldap_security", "starttls").strip(),
        group_mapping=group_mapping if isinstance(group_mapping, dict) else {},
        auto_provision=settings.get("ldap_auto_provision", "false").lower() == "true",
        ca_cert_path=settings.get("ldap_ca_cert_path", "").strip(),
    )


def _create_server(config: LDAPConfig) -> Server:
    """Create an ldap3 Server instance from config.

    Always uses TLS — either LDAPS (TLS from start) or StartTLS (upgrade after connect).
    Plaintext LDAP is not supported.
    """
    import ssl

    use_ssl = config.security == "ldaps" or config.server_url.startswith("ldaps://")

    if config.ca_cert_path:
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=config.ca_cert_path)
    else:
        tls = Tls(validate=ssl.CERT_NONE)

    return Server(config.server_url, use_ssl=use_ssl, tls=tls, get_info=ALL, connect_timeout=10)


def authenticate_ldap_user(config: LDAPConfig, username: str, password: str) -> LDAPUserInfo | None:
    """Authenticate a user via LDAP bind.

    1. Bind with service account to search for the user DN
    2. Attempt bind with the user's DN and provided password
    3. On success, retrieve user attributes and group memberships

    Returns LDAPUserInfo on success, None on failure.
    """
    if not password:
        return None

    server = _create_server(config)

    # Step 1: Service account bind + user search
    try:
        service_conn = Connection(
            server,
            user=config.bind_dn,
            password=config.bind_password,
            auto_bind=False,
            raise_exceptions=True,
            read_only=True,
        )
        service_conn.open()
        if config.security == "starttls" and not config.server_url.startswith("ldaps://"):
            service_conn.start_tls()
        service_conn.bind()
    except Exception as e:
        logger.warning("LDAP service account bind failed: %s", e)
        return None

    try:
        # Search for the user
        search_filter = config.user_filter.replace("{username}", _ldap_escape(username))
        service_conn.search(
            search_base=config.search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["*"],
        )

        if not service_conn.entries:
            logger.info("LDAP user not found: %s", username)
            return None

        user_entry = service_conn.entries[0]
        user_dn = str(user_entry.entry_dn)

        # Step 2: Bind as the user to verify password
        try:
            user_conn = Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=False,
                raise_exceptions=True,
                read_only=True,
            )
            user_conn.open()
            if config.security == "starttls" and not config.server_url.startswith("ldaps://"):
                user_conn.start_tls()
            user_conn.bind()
            user_conn.unbind()
        except Exception as e:
            logger.info("LDAP bind failed for user %s: %s", username, e)
            return None

        # Step 3: Extract user info
        email = str(user_entry.mail) if hasattr(user_entry, "mail") and user_entry.mail else None
        display_name = (
            str(user_entry.displayName) if hasattr(user_entry, "displayName") and user_entry.displayName else None
        )

        # Collect groups from memberOf attribute (Active Directory / groupOfNames)
        groups = (
            [str(g) for g in user_entry.memberOf] if hasattr(user_entry, "memberOf") and user_entry.memberOf else []
        )

        # Also search for Posix groups (memberUid-based) using the service account
        canonical_username = username
        if hasattr(user_entry, "sAMAccountName") and user_entry.sAMAccountName:
            canonical_username = str(user_entry.sAMAccountName)
        elif hasattr(user_entry, "uid") and user_entry.uid:
            canonical_username = str(user_entry.uid)

        posix_filter = f"(&(objectClass=posixGroup)(memberUid={_ldap_escape(canonical_username)}))"
        service_conn.search(
            search_base=config.search_base,
            search_filter=posix_filter,
            search_scope=SUBTREE,
            attributes=["cn"],
        )
        for entry in service_conn.entries:
            groups.append(str(entry.entry_dn))

        logger.info(
            "LDAP authentication successful for user: %s (DN: %s, groups: %d)", canonical_username, user_dn, len(groups)
        )

        return LDAPUserInfo(
            username=canonical_username,
            email=email,
            display_name=display_name,
            groups=groups,
        )
    finally:
        service_conn.unbind()


def resolve_group_mapping(ldap_groups: list[str], group_mapping: dict[str, str]) -> list[str]:
    """Map LDAP group DNs to BamBuddy group names.

    Returns list of BamBuddy group names that the user should be added to.
    Comparison is case-insensitive on the LDAP group DN.
    """
    if not group_mapping:
        return []

    # Build case-insensitive lookup
    mapping_lower = {k.lower(): v for k, v in group_mapping.items()}
    result = []
    for ldap_group in ldap_groups:
        bambuddy_group = mapping_lower.get(ldap_group.lower())
        if bambuddy_group:
            result.append(bambuddy_group)
    return result


def test_ldap_connection(config: LDAPConfig) -> tuple[bool, str]:
    """Test LDAP connection and service account bind.

    Returns (success, message).
    """
    try:
        server = _create_server(config)
        conn = Connection(
            server,
            user=config.bind_dn,
            password=config.bind_password,
            auto_bind=False,
            raise_exceptions=True,
            read_only=True,
        )
        conn.open()
        if config.security == "starttls" and not config.server_url.startswith("ldaps://"):
            conn.start_tls()
        conn.bind()

        # Try a search to verify search base
        conn.search(
            search_base=config.search_base,
            search_filter="(objectClass=*)",
            search_scope=SUBTREE,
            size_limit=1,
        )
        conn.unbind()
        return True, "LDAP connection successful"
    except Exception as e:
        return False, f"LDAP connection failed: {e}"


def _ldap_escape(value: str) -> str:
    """Escape special characters in LDAP search filter values (RFC 4515)."""
    replacements = {
        "\\": "\\5c",
        "*": "\\2a",
        "(": "\\28",
        ")": "\\29",
        "\x00": "\\00",
    }
    for char, escaped in replacements.items():
        value = value.replace(char, escaped)
    return value
