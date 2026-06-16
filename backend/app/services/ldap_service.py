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
class LDAPSearchResult:
    """A directory user returned by the admin search endpoint (no auth performed)."""

    username: str
    email: str | None
    display_name: str | None
    dn: str


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
    default_group: str  # Fallback BamBuddy group assigned when user has no mapped groups (empty = no fallback)


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
        default_group=settings.get("ldap_default_group", "").strip(),
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


def _open_service_connection(config: LDAPConfig, server: Server, *, check_names: bool = True) -> Connection:
    """Open and bind a service-account LDAP connection. Raises on failure.

    `check_names` toggles ldap3's client-side attribute-name validation. The
    default keeps it on so typos in `user_filter` fail loudly. The fuzzy
    directory search disables it because its fixed OR filter spans both AD-only
    (sAMAccountName, displayName) and OpenLDAP-only attribute names — without
    this bypass ldap3 throws `LDAPAttributeError` before any request is sent
    on a directory whose schema doesn't define one of the names.
    """
    conn = Connection(
        server,
        user=config.bind_dn,
        password=config.bind_password,
        auto_bind=False,
        raise_exceptions=True,
        read_only=True,
        check_names=check_names,
    )
    conn.open()
    if config.security == "starttls" and not config.server_url.startswith("ldaps://"):
        conn.start_tls()
    conn.bind()
    return conn


def _pick_canonical_username(entry, fallback: str) -> str:
    """Prefer sAMAccountName, then uid, then the supplied fallback."""
    if hasattr(entry, "sAMAccountName") and entry.sAMAccountName:
        return str(entry.sAMAccountName)
    if hasattr(entry, "uid") and entry.uid:
        return str(entry.uid)
    return fallback


def _extract_user_info(
    service_conn: Connection, config: LDAPConfig, user_entry, fallback_username: str
) -> LDAPUserInfo:
    """Build an LDAPUserInfo from an already-fetched directory entry.

    Collects memberOf groups, POSIX memberUid groups, and the primary
    gidNumber group; dedups DNs case-insensitively. Uses the supplied
    service-bound connection to resolve POSIX groups.
    """
    email = str(user_entry.mail) if hasattr(user_entry, "mail") and user_entry.mail else None
    display_name = (
        str(user_entry.displayName) if hasattr(user_entry, "displayName") and user_entry.displayName else None
    )

    # Collect groups from memberOf attribute (Active Directory / groupOfNames)
    groups = [str(g) for g in user_entry.memberOf] if hasattr(user_entry, "memberOf") and user_entry.memberOf else []

    canonical_username = _pick_canonical_username(user_entry, fallback_username)

    # Also search for POSIX groups (memberUid-based) using the service account
    posix_filter = f"(&(objectClass=posixGroup)(memberUid={_ldap_escape(canonical_username)}))"
    service_conn.search(
        search_base=config.search_base,
        search_filter=posix_filter,
        search_scope=SUBTREE,
        attributes=["cn"],
    )
    for entry in service_conn.entries:
        groups.append(str(entry.entry_dn))

    # POSIX primary group: user's gidNumber matches a posixGroup's gidNumber.
    # Standard Unix semantics treat this as full group membership, so we need
    # to resolve it to a group DN alongside the memberUid results.
    if hasattr(user_entry, "gidNumber") and user_entry.gidNumber:
        primary_gid = str(user_entry.gidNumber)
        primary_filter = f"(&(objectClass=posixGroup)(gidNumber={_ldap_escape(primary_gid)}))"
        service_conn.search(
            search_base=config.search_base,
            search_filter=primary_filter,
            search_scope=SUBTREE,
            attributes=["cn"],
        )
        for entry in service_conn.entries:
            groups.append(str(entry.entry_dn))

    # Dedupe group DNs (user may be in a group via both memberUid and primary gidNumber).
    # Case-insensitive comparison — LDAP DNs are case-insensitive by spec.
    seen_lower: set[str] = set()
    deduped_groups: list[str] = []
    for g in groups:
        key = g.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            deduped_groups.append(g)

    return LDAPUserInfo(
        username=canonical_username,
        email=email,
        display_name=display_name,
        groups=deduped_groups,
    )


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

    try:
        service_conn = _open_service_connection(config, server)
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

        info = _extract_user_info(service_conn, config, user_entry, username)
        logger.info(
            "LDAP authentication successful for user: %s (DN: %s, groups: %d)",
            info.username,
            user_dn,
            len(info.groups),
        )
        return info
    finally:
        service_conn.unbind()


def lookup_ldap_user(config: LDAPConfig, username: str) -> LDAPUserInfo | None:
    """Look up a directory user by exact username via the service-account bind.

    Performs no password verification — intended for the admin manual-provision
    flow, where the caller has already been authenticated as a BamBuddy admin
    and now needs the directory attributes (email, display name, group DNs)
    to create the user.

    Uses the same `user_filter` template that the login path uses, so anything
    that logs in successfully via auto-provision is also resolvable here.
    """
    server = _create_server(config)

    try:
        service_conn = _open_service_connection(config, server)
    except Exception as e:
        logger.warning("LDAP service account bind failed during lookup: %s", e)
        raise

    try:
        search_filter = config.user_filter.replace("{username}", _ldap_escape(username))
        service_conn.search(
            search_base=config.search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["*"],
        )
        if not service_conn.entries:
            logger.info("LDAP lookup: user not found: %s", username)
            return None
        return _extract_user_info(service_conn, config, service_conn.entries[0], username)
    finally:
        service_conn.unbind()


def search_ldap_users(config: LDAPConfig, query: str, limit: int = 25) -> list[LDAPSearchResult]:
    """Fuzzy search the directory for users matching `query`.

    Uses a fixed OR filter across sAMAccountName, uid, mail, displayName, and
    cn — covering both Active Directory and OpenLDAP layouts. The query is
    RFC-4515 escaped so a typed `*` doesn't enumerate the whole directory.
    Returns up to `limit` results (default 25). Service-bind failures raise so
    the caller can surface a 503; "no matches" returns an empty list.

    Callers should enforce a minimum query length (≥2 chars) — short queries
    against a large directory are wasteful and effectively unbounded.
    """
    query = query.strip()
    if len(query) < 2:
        return []

    escaped = _ldap_escape(query)
    search_filter = (
        f"(|(sAMAccountName=*{escaped}*)(uid=*{escaped}*)(mail=*{escaped}*)(displayName=*{escaped}*)(cn=*{escaped}*))"
    )

    server = _create_server(config)

    try:
        # check_names=False so OpenLDAP directories (no sAMAccountName/displayName
        # in schema) don't reject the cross-schema OR filter — see helper docstring.
        service_conn = _open_service_connection(config, server, check_names=False)
    except Exception as e:
        logger.warning("LDAP service account bind failed during search: %s", e)
        raise

    try:
        # attributes=["*"] requests all user attributes. We can't enumerate the
        # AD/OpenLDAP-specific names (sAMAccountName, displayName) explicitly
        # because ldap3 validates the attribute list against the server schema
        # even with check_names=False — and OpenLDAP rejects the AD names. The
        # `*` wildcard is hardcoded in ldap3's ATTRIBUTES_EXCLUDED_FROM_CHECK so
        # it bypasses that validation, and the server returns whatever it has.
        service_conn.search(
            search_base=config.search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["*"],
            size_limit=limit,
        )
        results: list[LDAPSearchResult] = []
        for entry in service_conn.entries:
            username = _pick_canonical_username(entry, "")
            if not username and hasattr(entry, "cn") and entry.cn:
                # Last resort — some OpenLDAP layouts only have cn
                username = str(entry.cn)
            if not username:
                continue
            email = str(entry.mail) if hasattr(entry, "mail") and entry.mail else None
            display_name = str(entry.displayName) if hasattr(entry, "displayName") and entry.displayName else None
            results.append(
                LDAPSearchResult(
                    username=username,
                    email=email,
                    display_name=display_name,
                    dn=str(entry.entry_dn),
                )
            )
        logger.info("LDAP directory search for %r returned %d result(s)", query, len(results))
        return results
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
