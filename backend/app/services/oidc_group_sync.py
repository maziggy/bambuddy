"""OIDC group sync (#3107).

Mirrors the LDAP group sync semantics from api/routes/auth.py
(`_sync_ldap_user`) for OIDC logins: the provider's ``group_mapping``
configures which Bambuddy groups the IdP is allowed to manage, and every
login replaces only that managed slice — manual assignments to any other
group survive (#1292, same fix the LDAP path needed).

Differences from LDAP worth stating:

- LDAP reads group DNs from the directory entry. OIDC reads group values
  from a JWT claim, and providers disagree on the shape: Keycloak ships a
  JSON array, Authentik ships an array, Logto and some legacy setups ship a
  space- or comma-separated string. ``extract_idp_groups`` accepts both.
- LDAP has a ``default_group`` fallback when no mapped group matches. The
  OIDC path already has ``default_group_id`` applied at account creation,
  and re-asserting it on every login would fight manual upgrades: an admin
  who promotes an auto-created user out of Viewers would see the promotion
  reverted at the next SSO login. So the OIDC sync has no fallback — an
  empty resolved set simply means "the IdP grants none of the mapped
  groups", which removes exactly the mapped groups and nothing else.
"""

from __future__ import annotations

import contextlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.group import Group
from backend.app.models.user import User

logger = logging.getLogger(__name__)

# Bound on the claim parsing below. A legitimate groups claim holds tens of
# entries; anything past this is a malformed or hostile token, and iterating
# it would just burn cycles before the mapping lookup ignores the extras.
_MAX_CLAIM_ITEMS = 500


def extract_idp_groups(claim_value: object) -> list[str]:
    """Normalise a raw JWT claim value into a list of IdP group strings.

    Accepts the shapes seen in the wild:
    - list of strings (Keycloak, Authentik, most modern providers)
    - single string, space- or comma-separated (Logto, some legacy setups)
    - a single group name as a bare string

    Non-string entries, empty fragments, and obvious non-group payloads
    (dicts, numbers) are dropped rather than rejected: a provider adding an
    unexpected claim shape must not lock users out of their mapped groups.
    Duplicates are removed while preserving order (first occurrence wins).
    The result is bounded by _MAX_CLAIM_ITEMS for both shapes — a string
    claim splits into arbitrarily many fragments, so the slice applies after
    splitting, not only on the list path.
    """
    if claim_value is None:
        return []
    if isinstance(claim_value, list):
        raw_items = [item for item in claim_value[:_MAX_CLAIM_ITEMS] if isinstance(item, str)]
    elif isinstance(claim_value, str):
        # Space-separated is the OIDC convention (scope-style); commas are a
        # pragmatic extra since some IdPs stringify arrays that way.
        raw_items = claim_value.replace(",", " ").split(" ")[:_MAX_CLAIM_ITEMS]
    else:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def resolve_oidc_group_mapping(idp_groups: list[str], group_mapping: dict[str, str]) -> list[str]:
    """Map IdP group values to Bambuddy group names (case-insensitive on the key).

    Same contract as ldap_service.resolve_group_mapping: returns the Bambuddy
    group names the user should hold among the mapped set. Values are compared
    case-insensitively because IdP group casing is not stable across providers
    (Keycloak preserves case; some LDAP-backed OIDC deployments downcase),
    and a case mismatch silently dropping a group is the failure mode an
    admin can least diagnose from the UI.
    """
    if not group_mapping:
        return []
    mapping_lower = {k.lower(): v for k, v in group_mapping.items()}
    result: list[str] = []
    for idp_group in idp_groups:
        mapped = mapping_lower.get(idp_group.lower())
        if mapped and mapped not in result:
            result.append(mapped)
    return result


async def sync_oidc_user_groups(
    db: AsyncSession,
    user: User,
    *,
    group_claim: str,
    group_mapping: dict[str, str],
    claims: dict,
) -> None:
    """Apply the provider's group mapping to ``user`` after a successful login.

    Only Bambuddy groups named in ``group_mapping`` values are managed; every
    other group on the user is a manual assignment and is preserved. Commits
    only when something actually changed (the LDAP sync logs on change; same
    here). Never raises: a group-sync failure must not abort the login the
    token exchange already authenticated — the exception is logged and the
    user keeps the groups they had.
    """
    if not group_mapping:
        # No mapping configured: nothing is managed, so nothing may change.
        # This is the default state and must remain a no-op for upgrades.
        return

    try:
        mapped_names = resolve_oidc_group_mapping(extract_idp_groups(claims.get(group_claim)), group_mapping)

        # Only groups that exist locally can be granted; a mapping entry
        # pointing at a deleted group is skipped (the same dangling-FK
        # tolerance default_group_id documents for SQLite).
        if mapped_names:
            groups_result = await db.execute(select(Group).where(Group.name.in_(mapped_names)))
            target_groups = list(groups_result.scalars().all())
        else:
            target_groups = []

        managed_names = set(group_mapping.values())
        preserved = [g for g in user.groups if g.name not in managed_names]
        new_groups = preserved + target_groups

        current_ids = {g.id for g in user.groups}
        new_ids = {g.id for g in new_groups}
        if current_ids == new_ids:
            return

        user.groups = new_groups
        await db.commit()
        logger.info(
            "OIDC group sync: user %s groups -> %s",
            user.username,
            sorted(g.name for g in new_groups),
        )
    except Exception:  # noqa: BLE001 -- login must survive a sync failure
        logger.exception("OIDC group sync failed for user %s; groups left unchanged", user.username)
        with contextlib.suppress(Exception):
            await db.rollback()
