from __future__ import annotations

import logging
import os
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import PyJWTError as JWTError
from passlib.context import CryptContext
from passlib.exc import PasswordSizeError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.database import async_session, get_db
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import (
    ALL_PRINTERS,
    PrinterScope,
    api_key_own_scope,
    resolve_user_id_printer_scope,
    resolve_user_printer_scope,
)
from backend.app.models.api_key import APIKey
from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.last_used import LastUsedThrottle
from backend.app.utils.local_time import utcnow_naive

logger = logging.getLogger(__name__)

# GHSA-r2qv-8222-hqg3 (CVSS 9.9) — API key permission enforcement is allowlist-based.
#
# Until 0.2.4.x, ``_check_apikey_permissions`` only consulted the admin denylist
# below. The three documented scope flags on ``APIKey``
# (``can_read_status`` / ``can_queue`` / ``can_control_printer`` / ``can_manage_library``)
# were enforced only by ``check_permission()`` inside ``routes/webhook.py``;
# every other route used ``require_permission_if_auth_enabled`` which fell
# through to the denylist-only path, so an API key with all flags unchecked
# could still stop prints, edit queue items, and read every endpoint not in
# this set. ``require_any_permission_if_auth_enabled`` and
# ``require_ownership_permission`` did not call this helper at all, so admin
# "any-of" routes and ownership-modify routes were entirely ungated for API keys.
#
# Fix: ``_check_apikey_permissions`` now requires every requested permission to
# be present in ``_APIKEY_SCOPE_BY_PERMISSION`` (allowlist), and gates on the
# corresponding scope flag on the API key. Unmapped permissions = 403. This
# means a Permission added to ``core/permissions.py`` without a matching entry
# in ``_APIKEY_SCOPE_BY_PERMISSION`` is automatically denied for API keys —
# the previous denylist shape allowed every new Permission to silently widen
# the API-key surface.
#
# The denylist is retained for documentation / drift-detection only — its
# entries also satisfy "not in the allowlist", so they fail closed regardless.
#
# #1894 follow-on: the allowlist is a ceiling, not a grant. A key is also
# narrowed to what its owner may do, so a user who can create keys cannot mint
# themselves authority they do not have, and deactivating a user disables their
# keys. Legacy ownerless keys (``user_id IS NULL``) have no owner to narrow
# against and remain governed by the scope flags alone.
#
# Mapping rationale (see wiki/features/api-keys.md):
#   can_read_status       → every ``*_READ`` + camera + stats + system + websocket
#                           + the slim id/username user listing (NOT ``users:read``)
#   can_queue             → queue write ops + archive reprint
#   can_control_printer   → physical printer + smart-plug control
#   can_manage_library    → library upload/own + MakerWorld import (separate
#                           trust level from queue management, hence its own flag)
#   can_manage_inventory  → spool/catalog/forecast writes + SpoolBuddy kiosk writes
#   can_manage_maintenance→ per-printer maintenance log/reset + type-catalog CRUD
#   admin-only            → unmapped (default-deny); covers all create/update/
#                           delete of admin resources, settings writes, user/
#                           group/api-key/backup admin ops, discovery scan,
#                           cloud auth, library ALL-ownership perms, purges
#
# A value may be a tuple of scope flags, in which case ALL of them must be True
# on the key. That is for the rare permission whose route spans two trust
# dimensions the operator toggles separately — see ``PIPELINES_RUN`` below.
# Prefer a single flag; a tuple is a statement that neither flag alone
# authorises what the route does.
_APIKEY_SCOPE_BY_PERMISSION: dict[Permission, str | tuple[str, ...]] = {
    # can_read_status — read-only access to status, history, and configuration
    Permission.PRINTERS_READ: "can_read_status",
    # Legacy flat permissions retained for back-compat with custom API keys —
    # the role bootstraps no longer use these, but custom keys may still
    # carry can_read_status scope mapping. New endpoints gate on the
    # ARCHIVES_READ_OWN / _ALL split (maziggy/bambuddy-security #2).
    Permission.ARCHIVES_READ: "can_read_status",
    Permission.ARCHIVES_READ_OWN: "can_read_status",
    Permission.ARCHIVES_READ_ALL: "can_read_status",
    Permission.QUEUE_READ: "can_read_status",
    Permission.QUEUE_READ_OWN: "can_read_status",
    Permission.QUEUE_READ_ALL: "can_read_status",
    Permission.LIBRARY_READ: "can_read_status",
    Permission.LIBRARY_READ_OWN: "can_read_status",
    Permission.LIBRARY_READ_ALL: "can_read_status",
    Permission.PROJECTS_READ: "can_read_status",
    Permission.FILAMENTS_READ: "can_read_status",
    Permission.INVENTORY_READ: "can_read_status",
    Permission.INVENTORY_VIEW_ASSIGNMENTS: "can_read_status",
    Permission.INVENTORY_FORECAST_READ: "can_read_status",
    Permission.SMART_PLUGS_READ: "can_read_status",
    Permission.CAMERA_VIEW: "can_read_status",
    Permission.MAINTENANCE_READ: "can_read_status",
    Permission.KPROFILES_READ: "can_read_status",
    Permission.NOTIFICATIONS_READ: "can_read_status",
    Permission.NOTIFICATION_TEMPLATES_READ: "can_read_status",
    Permission.EXTERNAL_LINKS_READ: "can_read_status",
    Permission.FIRMWARE_READ: "can_read_status",
    Permission.AMS_HISTORY_READ: "can_read_status",
    Permission.PRINTER_SENSOR_HISTORY_READ: "can_read_status",
    Permission.STATS_READ: "can_read_status",
    Permission.STATS_FILTER_BY_USER: "can_read_status",
    # USERS_READ_SLIM is ids and usernames only (#1894). It lets a key whose
    # owner holds stats:filter_by_user address ``?created_by_id=N`` without
    # guessing ids. The full USERS_READ listing (emails, roles, group
    # membership, permission sets) stays unmapped = admin-only.
    Permission.USERS_READ_SLIM: "can_read_status",
    Permission.SYSTEM_READ: "can_read_status",
    # SETTINGS_READ stays allowed via read-status so SpoolBuddy kiosks keep
    # working (they need the UI-language setting via API key).
    Permission.SETTINGS_READ: "can_read_status",
    Permission.MAKERWORLD_VIEW: "can_read_status",
    Permission.MANYFOLD_VIEW: "can_read_status",
    # Pipeline definitions and run history are configuration + status: listing
    # pipelines, reading a run, and the (write-free) POST check-eligibility
    # pre-flight. Authoring stays admin-only under PIPELINES_WRITE.
    Permission.PIPELINES_READ: "can_read_status",
    Permission.WEBSOCKET_CONNECT: "can_read_status",
    # can_queue — queue write ops + reprint (which enqueues an existing archive)
    Permission.QUEUE_CREATE: "can_queue",
    Permission.QUEUE_UPDATE_OWN: "can_queue",
    Permission.QUEUE_UPDATE_ALL: "can_queue",
    Permission.QUEUE_DELETE_OWN: "can_queue",
    Permission.QUEUE_DELETE_ALL: "can_queue",
    Permission.QUEUE_REORDER: "can_queue",
    Permission.QUEUE_START_UNREVIEWED: "can_queue",
    Permission.ARCHIVES_REPRINT_OWN: "can_queue",
    Permission.ARCHIVES_REPRINT_ALL: "can_queue",
    # can_control_printer — physical-world side effects on hardware
    Permission.PRINTERS_CONTROL: "can_control_printer",
    Permission.PRINTERS_FILES: "can_control_printer",
    Permission.PRINTERS_AMS_RFID: "can_control_printer",
    Permission.PRINTERS_CLEAR_PLATE: "can_control_printer",
    Permission.SMART_PLUGS_CONTROL: "can_control_printer",
    # can_manage_library — file-manager scope (upload/rename/delete library
    # entries + MakerWorld import which downloads files into the library).
    # OWN and ALL ownership variants map to the same scope so the
    # `require_ownership_permission` checker passes the API key through and
    # lets its owner's OWN/ALL permission decide the rows. This matches
    # `can_queue` and the archives/inventory scopes; splitting OWN/ALL across
    # allowlist/denylist made the whole library curation surface unreachable
    # for API keys (#1832).
    # LIBRARY_PURGE stays admin-only as a genuinely destructive op that
    # bypasses the soft-delete window.
    Permission.LIBRARY_UPLOAD: "can_manage_library",
    Permission.LIBRARY_UPDATE_OWN: "can_manage_library",
    Permission.LIBRARY_UPDATE_ALL: "can_manage_library",
    Permission.LIBRARY_DELETE_OWN: "can_manage_library",
    Permission.LIBRARY_DELETE_ALL: "can_manage_library",
    Permission.MAKERWORLD_IMPORT: "can_manage_library",
    Permission.MANYFOLD_IMPORT: "can_manage_library",
    # can_manage_inventory — inventory write scope. Covers the documented
    # spool/catalog/forecast write surface AND the SpoolBuddy kiosk endpoints
    # (NFC scan, scale reading, system command/update) which used
    # INVENTORY_UPDATE as a stand-in for "kiosk write" under the prior
    # denylist model. Read-only inventory (INVENTORY_READ etc.) stays under
    # can_read_status.
    Permission.INVENTORY_CREATE: "can_manage_inventory",
    Permission.INVENTORY_UPDATE: "can_manage_inventory",
    Permission.INVENTORY_DELETE: "can_manage_inventory",
    Permission.INVENTORY_FORECAST_WRITE: "can_manage_inventory",
    # can_manage_maintenance — carved out of the admin denylist so HA-style
    # automations can log "cleaned nozzle" / reset a maintenance counter via
    # `POST /maintenance/items/{item_id}/perform` without granting broader
    # printer control or settings write (#1832 follow-up). Also covers the
    # per-printer maintenance CRUD (assign/remove items, edit intervals) and
    # the type-catalog CRUD — the type catalog is a config surface (system
    # types are auto-seeded, custom types are user-defined), so grouping it
    # with the item writes matches the operator mental model of "keys that
    # log maintenance can also manage what gets tracked." MAINTENANCE_READ
    # stays under can_read_status.
    Permission.MAINTENANCE_CREATE: "can_manage_maintenance",
    Permission.MAINTENANCE_UPDATE: "can_manage_maintenance",
    Permission.MAINTENANCE_DELETE: "can_manage_maintenance",
    # can_manage_archives — print-history curation. Carved out of the admin
    # denylist so automations can prune old prints via API key (#1888): the
    # archive delete/update routes gate on
    # ``require_ownership_permission(ARCHIVES_*_ALL, ARCHIVES_*_OWN)``, where
    # the key's owner's OWN/ALL permission decides the rows (same as can_queue
    # / can_manage_library), so OWN and ALL map to the same scope. ARCHIVES_PURGE stays admin-only (see denylist) as a genuinely
    # destructive op that drops the stats contribution, mirroring LIBRARY_PURGE.
    # ARCHIVES_REPRINT_* stays under can_queue (it enqueues a print).
    Permission.ARCHIVES_CREATE: "can_manage_archives",
    Permission.ARCHIVES_UPDATE_OWN: "can_manage_archives",
    Permission.ARCHIVES_UPDATE_ALL: "can_manage_archives",
    Permission.ARCHIVES_DELETE_OWN: "can_manage_archives",
    Permission.ARCHIVES_DELETE_ALL: "can_manage_archives",
    # can_manage_projects — project curation. Carved out of the admin denylist
    # so automations can create projects and batch-add archives via API key
    # (#1893). The project mutation routes gate on plain
    # ``RequirePermissionIfAuthEnabled(Permission.PROJECTS_*)`` (no OWN/ALL
    # ownership split — projects have no per-row ownership permission), so the
    # three CRUD permissions map directly to the one scope. Membership edits
    # (e.g. add-archives-to-project) gate on PROJECTS_UPDATE, so they're covered.
    # PROJECTS_READ stays under can_read_status (unchanged).
    Permission.PROJECTS_CREATE: "can_manage_projects",
    Permission.PROJECTS_UPDATE: "can_manage_projects",
    Permission.PROJECTS_DELETE: "can_manage_projects",
    # can_queue AND can_manage_library — running a pipeline does two things a
    # key is separately trusted with. It slices the source into a new library
    # file (``slice_and_persist``, the same write the direct
    # ``POST /library/files/{id}/slice`` route gates on LIBRARY_UPLOAD →
    # can_manage_library), then creates one PrintQueueItem per copy for the
    # scheduler to dispatch (can_queue). Mapping it to either flag alone would
    # hand that flag the other one's authority, so both are required. Cancelling
    # a run is the same permission — whoever may start one may stop it. PR A
    # parked all three pipeline permissions on the denylist "until the run
    # dispatch lands"; it landed in PR C (#1425) and this is that follow-up.
    Permission.PIPELINES_RUN: ("can_queue", "can_manage_library"),
    # can_access_cloud — narrow opt-in scope, gated by the router-level
    # ``_cloud_api_key_gate`` and additionally enforced here so the route-
    # level ``cloud_caller(Permission.CLOUD_AUTH)`` dep also fails closed
    # when the flag is off (defence-in-depth).
    Permission.CLOUD_AUTH: "can_access_cloud",
    # ORCA_CLOUD_AUTH folds into the same ``can_access_cloud`` scope: same
    # trust dimension (third-party cloud access for profile sync), so an
    # operator who already accepted "this key can talk to clouds for the
    # owner" doesn't need a second toggle for Orca. Splitting later requires
    # a new column + migration — easy to add if the trust dimensions diverge.
    Permission.ORCA_CLOUD_AUTH: "can_access_cloud",
}

# Retained for documentation, drift-detection, and the prior "administrative
# operations" error string. Entries here are also absent from
# ``_APIKEY_SCOPE_BY_PERMISSION``, so they fail closed via the allowlist; the
# denylist is a redundant explicit "these are admin" marker, not the load-
# bearing security check.
_APIKEY_DENIED_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        # Settings administration (cred storage; rewriting these reaches SMTP/LDAP/MQTT).
        Permission.SETTINGS_UPDATE,
        Permission.SETTINGS_BACKUP,
        Permission.SETTINGS_RESTORE,
        # User / group / API-key administration.
        Permission.USERS_READ,
        Permission.USERS_CREATE,
        Permission.USERS_UPDATE,
        Permission.USERS_DELETE,
        Permission.GROUPS_READ,
        Permission.GROUPS_CREATE,
        Permission.GROUPS_UPDATE,
        Permission.GROUPS_DELETE,
        Permission.API_KEYS_CREATE,
        Permission.API_KEYS_UPDATE,
        Permission.API_KEYS_DELETE,
        Permission.API_KEYS_READ,
        # Finance / cost-center data has no dedicated API-key scope.
        Permission.COST_CENTERS_READ_OWN,
        Permission.COST_CENTERS_READ_ALL,
        Permission.COST_CENTERS_MODIFY,
        Permission.COST_CENTERS_CREATE,
        # GitHub backup admin + firmware OTA.
        Permission.GITHUB_BACKUP,
        Permission.GITHUB_RESTORE,
        Permission.FIRMWARE_UPDATE,
        # Resource administration (printer/project/filament/maintenance/k-profile/etc CRUD).
        # API keys with the operational scopes can read these resources via
        # *_READ permissions but cannot mutate the catalog/registry itself.
        Permission.PRINTERS_CREATE,
        Permission.PRINTERS_UPDATE,
        Permission.PRINTERS_DELETE,
        # ARCHIVES_CREATE / _UPDATE_OWN / _UPDATE_ALL / _DELETE_OWN /
        # _DELETE_ALL moved to the allowlist under `can_manage_archives`
        # (#1888) — split between allow/deny made the whole archive-management
        # surface unreachable for API keys via `require_ownership_permission`
        # (same regression class as the library/maintenance carve-outs in
        # #1832). ARCHIVES_PURGE stays denied as a genuinely destructive op
        # that drops the print's stats contribution.
        Permission.ARCHIVES_PURGE,
        # LIBRARY_UPDATE_ALL / LIBRARY_DELETE_ALL moved to the allowlist
        # under `can_manage_library` (#1832) — split between allow/deny made
        # the whole library curation surface unreachable for API keys via
        # `require_ownership_permission`. Purge stays denied as a genuinely
        # destructive op.
        Permission.LIBRARY_PURGE,
        # PROJECTS_CREATE / _UPDATE / _DELETE moved to the allowlist under
        # `can_manage_projects` (#1893) — they were denied for every API key,
        # making the project-management surface (create, add-archives, delete)
        # unreachable, same regression class as the archives/library carve-outs.
        Permission.FILAMENTS_CREATE,
        Permission.FILAMENTS_UPDATE,
        Permission.FILAMENTS_DELETE,
        # MAINTENANCE_CREATE / MAINTENANCE_UPDATE / MAINTENANCE_DELETE moved
        # to the allowlist under `can_manage_maintenance` (#1832 follow-up).
        Permission.KPROFILES_CREATE,
        Permission.KPROFILES_UPDATE,
        Permission.KPROFILES_DELETE,
        Permission.NOTIFICATIONS_CREATE,
        Permission.NOTIFICATIONS_UPDATE,
        Permission.NOTIFICATIONS_DELETE,
        Permission.NOTIFICATIONS_USER_EMAIL,
        Permission.NOTIFICATION_TEMPLATES_UPDATE,
        Permission.EXTERNAL_LINKS_CREATE,
        Permission.EXTERNAL_LINKS_UPDATE,
        Permission.EXTERNAL_LINKS_DELETE,
        Permission.SMART_PLUGS_CREATE,
        Permission.SMART_PLUGS_UPDATE,
        Permission.SMART_PLUGS_DELETE,
        # Network scanning — operator only (no API-key scope for this).
        Permission.DISCOVERY_SCAN,
        # Slicer Pipelines (#1425) — authoring only. PIPELINES_READ and
        # PIPELINES_RUN moved to the allowlist once PR C landed the run
        # dispatch; PIPELINES_WRITE stays denied because it creates/edits/
        # deletes the pipeline definition (slicer settings, target printer,
        # fanout strategy) and, via `POST /pipeline-runs/clear`, drops run
        # history. That is admin authoring, matching the other resource-CRUD
        # entries here — a key that may run a pipeline cannot rewrite what it
        # does.
        Permission.PIPELINES_WRITE,
    }
)


def _required_apikey_scopes(perm_string: str) -> tuple[str, ...] | None:
    """Return every scope flag a key must hold to exercise ``perm_string``.

    None when the permission is unmapped (= admin-only / not API-key-usable),
    which is distinct from an empty tuple — the latter would read as "no flags
    needed" and must never be produced.
    """
    try:
        perm = Permission(perm_string)
    except ValueError:
        return None
    scopes = _APIKEY_SCOPE_BY_PERMISSION.get(perm)
    if scopes is None:
        return None
    return (scopes,) if isinstance(scopes, str) else tuple(scopes)


def apikey_effective_permissions(api_key: APIKey, owner: User | None = None) -> list[str]:
    """Return the permissions ``api_key`` can actually exercise, sorted.

    This is the exact set ``_check_apikey_permissions`` will let through: every
    mapped permission whose scope flag is True on the key, further narrowed to
    what ``owner`` may do. Unmapped permissions are administrative and never
    resolve for a key, so they are absent.

    ``owner=None`` means a legacy ownerless key, where the scope flags are the
    whole of the key's authority -- not "skip the owner check". Callers holding
    an owned key must pass the owner, or ``/auth/me`` will over-report and drift
    from the gate, which is the defect #1894 was about.
    """

    def _granted(perm: Permission) -> bool:
        scopes = _required_apikey_scopes(perm.value)
        # An unmapped permission cannot occur here (we iterate the mapping
        # itself), but treat it as denied rather than as "no flags to satisfy",
        # which ``all(())`` would otherwise report as granted.
        if not scopes:
            return False
        return all(getattr(api_key, flag, False) for flag in scopes)

    return sorted(
        perm.value
        for perm in _APIKEY_SCOPE_BY_PERMISSION
        if _granted(perm) and (owner is None or owner.has_permission(perm.value))
    )


async def resolve_apikey_owner(db: AsyncSession, api_key: APIKey) -> User | None:
    """Load the owner of ``api_key`` for an authorization decision.

    Distinct from ``_user_from_api_key``, which answers "who is this, if
    anyone" and returns None for both the legacy and the broken case. Here
    those two must not be conflated:

    - ``user_id IS NULL`` -- a key predating per-user ownership. There is no
      owner to narrow against, so the scope flags stand alone. Returns None.
    - ``user_id`` set but the row is missing or deactivated -- the key's
      authority came from a user who no longer has any. Raises 403 rather than
      returning None, because returning None here would fail open: deactivating
      a user would leave their keys working with full scope authority.

    Groups are eager-loaded because ``has_permission`` walks them, and a lazy
    load inside the permission check would raise MissingGreenlet.
    """
    if api_key.user_id is None:
        return None
    result = await db.execute(select(User).where(User.id == api_key.user_id).options(selectinload(User.groups)))
    owner = result.scalar_one_or_none()
    if owner is None or not owner.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key owner is deactivated or no longer exists",
        )
    return owner


async def authorize_api_key(
    db: AsyncSession,
    api_key: APIKey,
    perm_strings: list[str],
    *,
    require_any: bool = False,
) -> None:
    """Resolve the key's owner and run the full permission gate. Raises 403."""
    owner = await resolve_apikey_owner(db, api_key)
    _check_apikey_permissions(api_key, perm_strings, owner=owner, require_any=require_any)


def _check_apikey_permissions(
    api_key: APIKey,
    perm_strings: list[str],
    *,
    owner: User | None = None,
    require_any: bool = False,
) -> None:
    """Raise 403 unless ``api_key`` is allowed to use ``perm_strings``.

    Allowlist semantics: every requested permission MUST be present in
    ``_APIKEY_SCOPE_BY_PERMISSION`` AND every scope flag it maps to must be
    True on ``api_key`` (most map to one; a few require several). Unmapped
    permissions = administrative = 403.

    A key must not out-rank the user it belongs to, so when ``owner`` is given
    the permission must additionally be one the owner holds. Scope flags are
    chosen at creation time by whoever holds ``api_keys:create``; that is
    admin-only in the default groups, but a custom group can grant it, and
    without this check such a user could mint themselves a key with
    ``can_control_printer`` and act through it beyond their own permissions.
    ``owner=None`` is only correct for legacy ownerless keys -- see
    ``resolve_apikey_owner``.

    By default ALL requested permissions must pass (mirrors
    ``require_permission_if_auth_enabled``).
    When ``require_any=True``, only one needs to pass (mirrors
    ``require_any_permission_if_auth_enabled``).
    """
    if not perm_strings:
        # Defensive: empty perm list means the dep is auth-only, not perm-gated.
        # Routes never call us with [] today, but if they did, returning here
        # would silently allow — instead, fail closed.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys cannot be used for unspecified permissions",
        )

    last_failure: HTTPException | None = None
    for perm_str in perm_strings:
        scopes = _required_apikey_scopes(perm_str)
        missing = [flag for flag in scopes or () if not getattr(api_key, flag, False)]
        if not scopes:
            failure = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API keys cannot be used for administrative operations",
            )
        elif missing:
            # Name every flag the key is short of, not just the first: a
            # permission requiring two scopes would otherwise send the operator
            # round the loop twice, ticking one box per 403.
            failure = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key does not have {' and '.join(repr(flag) for flag in missing)} permission",
            )
        elif owner is not None and not owner.has_permission(perm_str):
            failure = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key owner does not have '{perm_str}' permission",
            )
        else:
            failure = None

        if failure is None and require_any:
            return  # at least one passed
        if failure is not None and not require_any:
            raise failure
        last_failure = failure

    if require_any and last_failure is not None:
        raise last_failure


class ApiKeyActor:
    """An API key standing in for its owner in a route's own per-row checks.

    Permission dependencies answer a key request with no user, and a route's
    own checks read no user as "auth is off": they skip the archive, library
    and cost-center ownership tests a signed-in session faces. Routes that
    make those tests take this from ``RequestActor`` instead. It holds only
    the permissions the key may exercise (``apikey_effective_permissions``),
    so it never exceeds the owner nor the key's scope flags, and it is an
    administrator, for checks such as printing with any cost center, only when
    the owner is one. A legacy key without an owner has no ``id``, so it owns
    nothing and is a member of no cost center.
    """

    def __init__(self, api_key: APIKey, owner: User | None):
        self.api_key = api_key
        self.owner = owner
        self.is_admin: bool = owner is not None and owner.is_admin
        self.id: int | None = owner.id if owner is not None else None
        self.username: str | None = owner.username if owner is not None else None
        self._permissions = frozenset(apikey_effective_permissions(api_key, owner))

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions

    def has_all_permissions(self, *permissions: str) -> bool:
        return all(p in self._permissions for p in permissions)

    def has_any_permission(self, *permissions: str) -> bool:
        return any(p in self._permissions for p in permissions)


@dataclass(frozen=True)
class ScopedCaller:
    """Who passed a scoped door: an API key, a user, or nobody (auth disabled)."""

    api_key: APIKey | None = None
    user: User | None = None


def require_api_key_scope(
    scope_attr: str, scope_name: str, user_permission: Permission, *, owner_needs_permission: bool = False
):
    """A narrow door for API keys that carry one explicit scope flag.

    For routes an API key may call only when its ``scope_attr`` flag is set,
    where the matching user permission stays out of the general API-key
    mapping (``_APIKEY_DENIED_PERMISSIONS`` / the allowlist).

    Accepts:
      * Auth disabled  → always allowed (matches the other routes)
      * JWT user with ``user_permission``
      * API key with ``scope_attr`` set; fails closed when its owner was deactivated,
        and with ``owner_needs_permission`` also when its owner lacks
        ``user_permission`` (so a key can't do what its owner may not)
    """

    async def permission_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> ScopedCaller:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return ScopedCaller()

            credentials_exception = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

            # API key path — X-API-Key header or Bearer bb_xxx
            api_key_value: str | None = None
            if x_api_key:
                api_key_value = x_api_key
            elif credentials is not None and credentials.credentials.startswith("bb_"):
                api_key_value = credentials.credentials

            if api_key_value is not None:
                api_key = await _validate_api_key(db, api_key_value)
                if api_key is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                # Fails closed if the owner has been deactivated. For the
                # energy-cost door the scope flag is deliberately not narrowed
                # against the owner's permissions: no user permission maps to it
                # (SETTINGS_UPDATE stays denied for keys even when the owner is
                # an administrator). Doors that do have a matching user
                # permission pass ``owner_needs_permission``.
                owner = await resolve_apikey_owner(db, api_key)
                if (
                    owner_needs_permission
                    and owner is not None
                    and not owner.has_all_permissions(user_permission.value)
                ):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"The API key's owner lacks the permission: {user_permission.value}",
                    )
                if not getattr(api_key, scope_attr):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"API key does not have '{scope_name}' permission",
                    )
                return ScopedCaller(api_key=api_key)

            # JWT path
            if credentials is None:
                raise credentials_exception

            try:
                payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
                username: str = payload.get("sub")
                if username is None:
                    raise credentials_exception
                jti: str | None = payload.get("jti")
                if not jti or await is_jti_revoked(jti, db):
                    raise credentials_exception
                iat: int | float | None = payload.get("iat")
            except JWTError:
                raise credentials_exception

            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                raise credentials_exception
            if not _is_token_fresh(iat, user):
                raise credentials_exception
            if not user.has_all_permissions(user_permission.value):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permissions: {user_permission.value}",
                )
            return ScopedCaller(user=user)

    return permission_checker


def require_energy_cost_update():
    """Dependency for ``POST /settings/electricity-price`` (#1356).

    Bypasses the ``_APIKEY_DENIED_PERMISSIONS`` ``SETTINGS_UPDATE`` block for
    API keys that explicitly opt into ``can_update_energy_cost``. Full
    ``SETTINGS_UPDATE`` for API keys stays denied — this is a narrowly-scoped
    door for the Home Assistant dynamic-tariff use case documented in
    ``wiki/features/energy.md``, not a general settings-write capability.
    """
    return require_api_key_scope("can_update_energy_cost", "update_energy_cost", Permission.SETTINGS_UPDATE)


def require_notification_send():
    """Dependency for ``POST /notifications/app-message`` and ``PUT
    /external-links/badge``: another application sending a message through the
    channels that accept app messages, or setting the count on its sidebar
    link. API keys need ``can_send_notifications``; users need
    ``NOTIFICATIONS_UPDATE``."""
    return require_api_key_scope(
        "can_send_notifications", "send_notifications", Permission.NOTIFICATIONS_UPDATE, owner_needs_permission=True
    )


# Password hashing
# Use pbkdf2_sha256 instead of bcrypt to avoid 72-byte limit and passlib initialization issues
# pbkdf2_sha256 is a secure password hashing algorithm without bcrypt's limitations
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _get_jwt_secret() -> str:
    """Get the JWT secret key from environment, file, or generate a new one.

    Priority:
    1. JWT_SECRET_KEY environment variable
    2. .jwt_secret file in data directory
    3. Generate new random secret and save to file

    Returns:
        The JWT secret key
    """
    # 1. Check environment variable first
    env_secret = os.environ.get("JWT_SECRET_KEY")
    if env_secret:
        logger.info("Using JWT secret from JWT_SECRET_KEY environment variable")
        return env_secret

    # 2. Check for secret file in data directory
    from backend.app.core.paths import resolve_data_dir

    data_dir = resolve_data_dir()
    secret_file = data_dir / ".jwt_secret"

    if secret_file.exists():
        try:
            secret = secret_file.read_text().strip()
            if secret and len(secret) >= 32:
                logger.info("Using JWT secret from %s", secret_file)
                return secret
        except OSError as e:
            logger.warning("Failed to read JWT secret file: %s", e)

    # 3. Generate new random secret
    new_secret = secrets.token_urlsafe(64)

    # Try to save it
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Note: CodeQL flags this as "clear-text storage of sensitive information" but this is
        # intentional and secure - JWT secrets must be readable by the app, we set 0600 permissions,
        # and this is standard practice for self-hosted applications (same as .env files).
        secret_file.write_text(new_secret)  # nosec B105
        # Restrict permissions (owner read/write only)
        secret_file.chmod(0o600)
        logger.info("Generated new JWT secret and saved to %s", secret_file)
    except OSError as e:
        logger.warning(
            "Could not save JWT secret to file (%s). "
            "Secret will be regenerated on restart, invalidating existing tokens. "
            "Set JWT_SECRET_KEY environment variable for persistence.",
            e,
        )

    return new_secret


# JWT settings
SECRET_KEY = _get_jwt_secret()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours (M-2: reduced from 7 days)
# Hard ceiling for the admin-configurable session policy (#1706). 30 days
# matches the Pydantic le=720 on AppSettings.session_max_hours; defense in
# depth so a tampered settings row can't request an absurd lifetime.
SESSION_MAX_HOURS_HARD_CEILING = 720

# HTTP Bearer token
security = HTTPBearer(auto_error=False)


async def resolve_session_max_minutes(db: AsyncSession) -> int:
    """Return the session-lifetime ceiling (minutes) honoured by login routes.

    Reads ``session_max_hours`` from the settings table (#1706), clamps to
    [1h, 720h], and falls back to the audit-default 24h if the row is
    missing, blank, or unparseable.

    DB errors are NOT caught here — login is already in a DB transaction and
    a broken DB must abort the login rather than silently extend or shrink
    the session lifetime.
    """
    default_minutes = ACCESS_TOKEN_EXPIRE_MINUTES
    result = await db.execute(select(Settings).where(Settings.key == "session_max_hours"))
    row = result.scalar_one_or_none()
    if row is None or not row.value:
        return default_minutes
    try:
        hours = int(row.value)
    except (TypeError, ValueError):
        return default_minutes
    if hours < 1:
        return default_minutes
    if hours > SESSION_MAX_HOURS_HARD_CEILING:
        hours = SESSION_MAX_HOURS_HARD_CEILING
    return hours * 60


# --- Slicer download tokens ---
# Short-lived, resource-bound tokens for slicer protocol handlers and browser
# downloads that can't send auth headers.  Stored in AuthEphemeralToken
# (token_type=TokenType.SLICER_DOWNLOAD) so they survive server restarts and
# work in multi-worker deployments (M-3).
#
# Whether redemption consumes the token is the *caller's* choice, made at
# verify time -- see ``verify_slicer_download_token``.  The row is identical
# either way, so a token is never "the reusable kind"; the endpoint it is
# presented to decides.
SLICER_TOKEN_EXPIRE_MINUTES = 5


async def create_slicer_download_token(resource_type: str, resource_id: int) -> str:
    """Create a short-lived download token for slicer protocol handlers."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SLICER_TOKEN_EXPIRE_MINUTES)
    token = secrets.token_urlsafe(24)
    resource_key = f"{resource_type}:{resource_id}"
    async with async_session() as db:
        # Prune expired tokens opportunistically
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == TokenType.SLICER_DOWNLOAD,
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type=TokenType.SLICER_DOWNLOAD,
                nonce=resource_key,
                expires_at=expires_at,
            )
        )
        await db.commit()
    return token


async def verify_slicer_download_token(
    token: str,
    resource_type: str,
    resource_id: int,
    *,
    single_use: bool = True,
) -> bool:
    """Verify a slicer download token, consuming it unless ``single_use`` is False.

    Returns True only if the token is valid, unexpired, and bound to the given resource.

    With ``single_use=True`` (the default) redemption is a DELETE...RETURNING, which
    keeps the token one-shot even under concurrent requests.  Use it wherever the
    thing being downloaded is itself consumed -- the prepared printer bundle is
    deleted once streamed, so a second redemption could only ever 404.

    With ``single_use=False`` the token stays valid for the rest of its five-minute
    TTL.  Use it for the URLs handed to an external slicer over a protocol handler:
    we do not control that process, and one-shot redemption breaks the moment
    anything fetches the URL twice -- a retry after a transient failure (Bambu
    Studio retries three times), a resumed transfer, a redirect follow, an
    on-access scanner.  The first fetch would win and the slicer would be left
    with a 403 (#3029).  Resource binding and expiry are unchanged; only the
    number of redemptions inside the TTL differs.

    M-NEW-1 fix: nonce (resource key) is included in the WHERE clause so redemption
    only succeeds when the token is presented to the *correct* resource endpoint.
    Previously the token was consumed (committed) even when stored_key != expected_key,
    permanently invalidating it while returning False to the caller.
    """
    expected_key = f"{resource_type}:{resource_id}"
    now = datetime.now(timezone.utc)
    bound = (
        AuthEphemeralToken.token == token,
        AuthEphemeralToken.token_type == TokenType.SLICER_DOWNLOAD,
        AuthEphemeralToken.nonce == expected_key,
        AuthEphemeralToken.expires_at > now,
    )
    async with async_session() as db:
        if not single_use:
            result = await db.execute(select(AuthEphemeralToken.id).where(*bound))
            return result.scalar_one_or_none() is not None
        result = await db.execute(delete(AuthEphemeralToken).where(*bound).returning(AuthEphemeralToken.id))
        if result.one_or_none() is None:
            return False
        await db.commit()
        return True


async def token_outlived_its_session(db: AsyncSession, username: str | None, minted_at: datetime | None) -> bool:
    """Whether a token named after a user was minted before that user's session began.

    password_changed_at marks it: set for a new user, and on a rename, a
    password change or reset, a 2FA removal or a restore. A token from before
    belongs to an earlier holder of the name or an ended session; deletes and
    renames drop those, but one minted while they commit would survive.

    A token naming a user that does not exist counts as outlived: its callers
    look the user up again on their own, and a user created with the name in
    between would be served. Compared to the microsecond (both are written by
    Python): every user is committed before any token is minted for it, so
    there is nothing to round for, unlike a JWT's whole-second iat.
    """
    if not username:
        return False
    if minted_at is None:
        return True
    changed = (
        await db.execute(select(User.password_changed_at).where(func.lower(User.username) == func.lower(username)))
    ).first()
    if changed is None:
        return True  # no such user
    session_start = changed[0]
    if session_start is None:
        return False  # no session start recorded (an old row the start-up backfill has not reached)
    return minted_at.replace(tzinfo=None) < session_start.replace(tzinfo=None)


def _minting_session_start() -> datetime | None:
    """When the session of the request minting a token began, as a created_at.

    A token minted by a signed-in user counts from their JWT's issue time, not
    from when its row is written: a password change, reset or 2FA removal that
    commits in between (a stolen JWT minting in a loop) would otherwise leave a
    token dated after it. The end of the iat second, as _is_token_fresh treats
    a JWT from the second the session began as fresh. None outside a request
    signed in with a JWT (API keys, auth off, sign-in steps).
    """
    cached = _authenticated_user.get()
    if cached is None:
        return None
    try:
        iat = jwt.decode(cached[0], SECRET_KEY, algorithms=[ALGORITHM]).get("iat")
    except JWTError:
        return None
    if iat is None:
        return None
    return datetime.fromtimestamp(int(iat), tz=timezone.utc).replace(tzinfo=None) + timedelta(microseconds=999999)


def session_dated(username: str | None) -> dict:
    """created_at for a token a signed-in user mints for themself (see
    _minting_session_start); nothing when the request's session is someone
    else's (an admin minting for a user) or there is none."""
    cached = _authenticated_user.get()
    if not username or cached is None or cached[1].username.lower() != username.lower():
        return {}
    start = _minting_session_start()
    return {"created_at": start} if start is not None else {}


def _api_key_still_the_minter(current_prefix: str | None, minted_prefix: str | None) -> bool:
    """Whether a key-minted token's key is still the one that minted it.

    The prefix is recorded at mint (in ``nonce``): SQLite gives a deleted key's
    id to the next key created, and a token minted while the key was deleted
    would otherwise work with the new key's printers. Tokens from before the
    prefix was recorded have none and pass (they expire within the hour).
    """
    return minted_prefix is None or current_prefix == minted_prefix


# --- Camera stream tokens ---
# Reusable tokens for camera stream/snapshot endpoints loaded via <img>/<video>
# tags (these cannot send Authorization headers).  Unlike slicer tokens they are
# NOT single-use — streams reconnect on errors.  Stored in AuthEphemeralToken
# (token_type="camera_stream") for multi-worker compatibility (M-3).
#
# Anonymous by design: the row records no username, so a route guarded by this
# token knows only "some camera viewer", never which one.  That is fine for a
# live stream, which is per-printer and not per-user, and is precisely why
# non-camera media moved to the identified media token in #3025.
CAMERA_STREAM_TOKEN_EXPIRE_MINUTES = 60


async def create_camera_stream_token(
    username: str | None = None, api_key_id: int | None = None, api_key_prefix: str | None = None
) -> str:
    """Create a reusable token for camera stream/snapshot access.

    Records who minted it -- ``username`` for a user, ``api_key_id`` for an
    API key -- so the streams it opens stay within that caller's printer scope
    (#1727). Neither is set when auth is off.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=CAMERA_STREAM_TOKEN_EXPIRE_MINUTES)
    token = secrets.token_urlsafe(24)
    async with async_session() as db:
        # Prune expired tokens opportunistically
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == "camera_stream",
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type="camera_stream",
                username=username or "",
                api_key_id=api_key_id,
                nonce=api_key_prefix if api_key_id is not None else None,
                expires_at=expires_at,
                **session_dated(username),
            )
        )
        await db.commit()
    return token


WEBSOCKET_TOKEN_EXPIRE_MINUTES = 60


async def create_websocket_token(
    username: str | None, api_key_id: int | None = None, api_key_prefix: str | None = None
) -> str:
    """Create a short-lived token for ``/api/v1/ws`` connections.

    Mirrors the camera-stream-token pattern: opaque random string stored
    in ``auth_ephemeral_tokens`` with type ``"websocket"`` so the WS
    endpoint can verify it *before* calling ``websocket.accept()``.

    Records the issuing principal in the ``username`` field — for JWT
    callers this is the actual username, for API-keyed callers this is
    the empty string (handled in the route layer; we accept None at this
    interface so the auth-disabled path doesn't have to fabricate one).

    The 60-minute expiry matches camera tokens: long enough to survive
    page reloads / brief disconnects, short enough that a leaked token
    is not a credential.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=WEBSOCKET_TOKEN_EXPIRE_MINUTES)
    token = secrets.token_urlsafe(24)
    async with async_session() as db:
        # Prune expired tokens opportunistically (same shape as camera).
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == "websocket",
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type="websocket",
                username=username or "",
                api_key_id=api_key_id,
                nonce=api_key_prefix if api_key_id is not None else None,
                expires_at=expires_at,
                **session_dated(username),
            )
        )
        await db.commit()
    return token


async def verify_websocket_token_principal(token: str) -> tuple[str, int | None] | None:
    """Verify a WebSocket connect token and return who minted it.

    ``(username, api_key_id)``: the username is ``""`` for API-key callers
    and with auth off; ``api_key_id`` is set only for API keys. ``None`` when
    the token is missing / expired / unknown. The token is NOT consumed -- a
    single page reload should not need a new round trip to mint a
    replacement.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == token,
                AuthEphemeralToken.token_type == "websocket",
                AuthEphemeralToken.expires_at > now,
            )
        )
        row = result.scalar_one_or_none()
        if row is None or await token_outlived_its_session(db, row.username, row.created_at):
            return None
        if row.api_key_id is not None and not _api_key_still_the_minter(
            await usable_api_key_prefix(db, row.api_key_id), row.nonce
        ):
            return None
        return row.username or "", row.api_key_id


_NO_PRINTERS = PrinterScope(frozenset())


async def usable_api_key_prefix(db: AsyncSession, api_key_id: int) -> str | None:
    """The prefix of an API key that may still be used, else None.

    None for a key that is gone, disabled or expired. The prefix tells a key
    from a later one given its id (SQLite reuses ids), so an open WebSocket can
    tell whether its key is still the one it connected with.
    """
    api_key = (await db.execute(select(APIKey).where(APIKey.id == api_key_id))).scalar_one_or_none()
    if api_key is None or not api_key.enabled:
        return None
    if api_key.expires_at is not None:
        expires = api_key.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return None
    return api_key.key_prefix


async def principal_holds(
    db: AsyncSession, username: str | None, api_key_id: int | None, permission: Permission
) -> bool:
    """Whether a token's principal still holds the permission minting it needed.

    A group change can take it away while the token is still valid (an hour,
    or months for a long-lived one) and a connection or stream is open. A key
    is checked as at the mint (its scope flags and its owner's permissions); a
    user must exist, be active and hold it.
    """
    if api_key_id is not None:
        api_key = (await db.execute(select(APIKey).where(APIKey.id == api_key_id))).scalar_one_or_none()
        if api_key is None:
            return False
        try:
            await authorize_api_key(db, api_key, [permission.value])
        except HTTPException:
            return False
        return True
    if username:
        user = await get_user_by_username(db, username)
        return user is not None and user.is_active and user.has_permission(permission.value)
    return False


async def user_id_holds(
    db: AsyncSession, user_id: int, permission: Permission, minted_at: datetime | None = None
) -> bool:
    """principal_holds for the owner of a long-lived token, named by id.

    With ``minted_at`` (the token's created_at), a user created after the token
    is not its owner: SQLite gives a deleted user's id to the next user, and a
    token minted while the user was deleted would stream with the new user's
    printers for up to a year. Both created_at are set by the database, so they
    share one clock.
    """
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not user.has_permission(permission.value):
        return False
    return minted_at is None or user.created_at is None or minted_at >= user.created_at


async def principal_may_connect(db: AsyncSession, username: str | None, api_key_id: int | None) -> bool:
    """Whether a WebSocket token's principal still holds websocket:connect."""
    return await principal_holds(db, username, api_key_id, Permission.WEBSOCKET_CONNECT)


async def principal_printer_scope(db: AsyncSession, username: str | None, api_key_id: int | None) -> PrinterScope:
    """Printer scope of the principal a token was minted for (#1727).

    Fail-closed: a key that is gone, disabled, expired or whose owner was
    deactivated, a user who is gone or deactivated, and a token naming no
    principal all get no printers. Callers skip this when auth is off.
    """
    if api_key_id is not None:
        api_key = (await db.execute(select(APIKey).where(APIKey.id == api_key_id))).scalar_one_or_none()
        if api_key is None or not api_key.enabled:
            return _NO_PRINTERS
        if api_key.expires_at is not None:
            expires = api_key.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                return _NO_PRINTERS
        try:
            return await api_key_printer_scope(db, api_key)
        except HTTPException:
            return _NO_PRINTERS
    if username:
        user = await get_user_by_username(db, username)
        if user is None or not user.is_active:
            return _NO_PRINTERS
        return await resolve_user_printer_scope(db, user)
    return _NO_PRINTERS


@dataclass(frozen=True)
class StreamOpener:
    """Who opened a camera stream, and on what authority; re-checked while it runs.

    A live view runs for hours on a token checked once. Its opener is a user
    (a 60-minute token minted with their session), an API key, a long-lived
    token (Cam Wall, overlay, Home Assistant), or nobody while auth was off.
    ``started_at`` is the token's created_at -- when the session that minted it
    began -- not when the stream opened: a session that ended in between must
    still end the stream.
    """

    auth_on: bool
    username: str | None = None
    api_key_id: int | None = None
    api_key_prefix: str | None = None
    long_lived_id: int | None = None
    long_lived_user_id: int | None = None
    long_lived_prefix: str | None = None
    started_at: datetime = field(default_factory=utcnow_naive)


async def camera_stream_access(token: str) -> tuple[PrinterScope, StreamOpener] | None:
    """Verify a camera stream token: the minter's printer scope and who it is.

    Tries the ephemeral 60-minute token first (the common, browser-bound case)
    and falls through to long-lived tokens (#1108) for HA / kiosk integrations
    that paste a token once and expect it to keep working for days; those
    carry their owner's scope. None when the token is invalid.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == token,
                AuthEphemeralToken.token_type == "camera_stream",
                AuthEphemeralToken.expires_at > now,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None and await token_outlived_its_session(db, row.username, row.created_at):
            return None
        if (
            row is not None
            and row.api_key_id is not None
            and not _api_key_still_the_minter(await usable_api_key_prefix(db, row.api_key_id), row.nonce)
        ):
            return None
        if row is not None:
            # Minting it needed camera:view; a group or key change can take it away.
            if not await principal_holds(db, row.username, row.api_key_id, Permission.CAMERA_VIEW):
                return None
            scope = await principal_printer_scope(db, row.username, row.api_key_id)
            prefix = await usable_api_key_prefix(db, row.api_key_id) if row.api_key_id is not None else None
            # When the minting session began (see _minting_session_start), or
            # when the token was minted for one that is not a user's session.
            minted = row.created_at.replace(tzinfo=None)
            return scope, StreamOpener(
                auth_on=True,
                username=row.username or None,
                api_key_id=row.api_key_id,
                api_key_prefix=prefix,
                started_at=minted,
            )

        # Long-lived path. Imported lazily so the auth module stays importable
        # at startup before the long_lived_tokens model is registered.
        from backend.app.services.long_lived_tokens import STREAM_SCOPES, verify_token as verify_long_lived

        record = await verify_long_lived(db, token, scope=STREAM_SCOPES)
        if record is None or not await user_id_holds(db, record.user_id, Permission.CAMERA_VIEW, record.created_at):
            return None
        scope = await resolve_user_id_printer_scope(db, record.user_id)
        return scope, StreamOpener(
            auth_on=True,
            long_lived_id=record.id,
            long_lived_user_id=record.user_id,
            long_lived_prefix=record.lookup_prefix,
        )


async def verify_camera_stream_token(token: str) -> PrinterScope | None:
    """Verify a camera stream token (reusable -- does not consume it).

    Returns the printer scope of whoever minted it (#1727), or None when the
    token is invalid. See camera_stream_access.
    """
    access = await camera_stream_access(token)
    return access[0] if access is not None else None


async def stream_opener_still_allowed(opener: StreamOpener, printer_id: int) -> bool:
    """Whether a stream's opener may still watch this printer. Raises when it
    can't be read: the stream decides how long it may run unchecked.

    A user's sessions must not have ended since the stream started (password
    change or reset, 2FA removal, restore), and they must still exist and be
    active; a key must still be usable and the same key; a long-lived token
    must not be revoked or expired; a stream opened while auth was off ends
    once it is on. In every case camera:view must still be held (by the
    token's owner for a long-lived one) and the printer must still be in scope.
    """
    from backend.app.models.long_lived_token import LongLivedToken
    from backend.app.services.long_lived_tokens import _is_expired

    try:
        async with async_session() as db:
            auth_on = await is_auth_enabled(db)
            if not opener.auth_on:
                return not auth_on
            if not auth_on:
                return True
            if opener.long_lived_id is not None:
                record = await db.get(LongLivedToken, opener.long_lived_id)
                if (
                    record is None
                    or record.revoked_at is not None
                    or record.user_id != opener.long_lived_user_id
                    or record.lookup_prefix != opener.long_lived_prefix
                    or _is_expired(record, datetime.now(timezone.utc))
                    or not await user_id_holds(db, record.user_id, Permission.CAMERA_VIEW, record.created_at)
                ):
                    return False
                scope = await resolve_user_id_printer_scope(db, record.user_id)
            elif opener.api_key_id is not None:
                if await usable_api_key_prefix(db, opener.api_key_id) != opener.api_key_prefix:
                    return False
                if not await principal_holds(db, None, opener.api_key_id, Permission.CAMERA_VIEW):
                    return False
                scope = await principal_printer_scope(db, None, opener.api_key_id)
            elif opener.username:
                user = await get_user_by_username(db, opener.username)
                if user is None or not user.is_active or not user.has_permission(Permission.CAMERA_VIEW.value):
                    return False
                changed = user.password_changed_at
                if changed is not None and changed.replace(tzinfo=None) > opener.started_at:
                    return False
                scope = await principal_printer_scope(db, opener.username, None)
            else:
                return False
            return scope.allows(printer_id)
    except Exception:  # SEC-AUTH-EXC: logged and re-raised, never an answer
        logger.warning("Camera stream re-check failed", exc_info=True)
        raise


async def verify_camwall_token(token: str) -> PrinterScope | None:
    """Verify a Cam Wall token (#2531). Reusable -- does not consume it.

    Returns the token owner's printer scope (#1727), or None when invalid.

    Deliberately narrower than :func:`verify_camera_stream_token`: only the
    long-lived ``camwall`` scope passes. The 60-minute ephemeral token belongs
    to a logged-in browser, which already reaches the wall's metadata through
    the ordinary printers API and has no need of this endpoint; and a
    ``camera_stream`` token was handed out for video alone, so it must not
    acquire the ability to enumerate printers by name just because a new
    feature shipped.
    """
    async with async_session() as db:
        from backend.app.services.long_lived_tokens import verify_token as verify_long_lived

        record = await verify_long_lived(db, token, scope="camwall")
        # Minting it needed camera:view; a group change can take it away.
        if record is None or not await user_id_holds(db, record.user_id, Permission.CAMERA_VIEW, record.created_at):
            return None
        return await resolve_user_id_printer_scope(db, record.user_id)


async def verify_overlay_token(token: str) -> PrinterScope | None:
    """Verify a streaming-overlay token (#2613). Reusable -- does not consume it.

    Returns the token owner's printer scope (#1727), or None when invalid.

    Like :func:`verify_camwall_token`, only the matching long-lived scope passes:
    the overlay status feed names the file being printed, so it must not be
    reachable by a ``camwall`` token (which is trusted to hide the part name) or
    a bare ``camera_stream`` token (handed out for video alone). The 60-minute
    ephemeral token belongs to a logged-in browser, which reaches the same data
    through the ordinary printers API and has no need of this endpoint.
    """
    async with async_session() as db:
        from backend.app.services.long_lived_tokens import verify_token as verify_long_lived

        record = await verify_long_lived(db, token, scope="overlay")
        # Minting it needed camera:view; a group change can take it away.
        if record is None or not await user_id_holds(db, record.user_id, Permission.CAMERA_VIEW, record.created_at):
            return None
        return await resolve_user_id_printer_scope(db, record.user_id)


# --- Media tokens (#3025) ---
# Browsers cannot attach ``Authorization`` headers to ``<img src>`` / ``<video
# src>``, so image routes need a credential that fits in a query parameter.
# Until #3025 they borrowed the *camera stream* token for that, which had two
# costs: minting one requires ``camera:view``, so a user could not see a
# library thumbnail without also being handed the live camera pointed at the
# operator's room; and a camera-stream token records no principal at all, so
# the thirteen non-camera routes had no identity to check ownership against
# and returned any row to any holder.
#
# A media token fixes both by following the *websocket* token instead: it
# stores the username, so ``require_media_token_*`` can resolve the real user
# and apply the same per-row visibility gate the header-authenticated sibling
# routes already use. Like the websocket token it is not consumed (a page of
# thumbnails is many requests) and it outlives a password change by up to its
# TTL -- acceptable for read-only media at 60 minutes, and identical to the
# guarantee ``/api/v1/ws`` has made since GHSA-r2qv.
MEDIA_TOKEN_EXPIRE_MINUTES = 60


async def create_media_token(username: str | None) -> str:
    """Create a reusable token for media (thumbnail / preview / icon) routes.

    Records the issuing principal in ``username`` exactly as
    :func:`create_websocket_token` does. API-keyed callers reach this with
    ``None`` and get the empty string, which :func:`verify_media_token`
    reports back and the dependencies then reject while auth is enabled --
    an API key has no per-row ownership identity, and it does not need one
    here because the media routes accept ``X-API-Key`` directly.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=MEDIA_TOKEN_EXPIRE_MINUTES)
    token = secrets.token_urlsafe(24)
    async with async_session() as db:
        # Prune expired tokens opportunistically (same shape as camera/websocket).
        await db.execute(
            delete(AuthEphemeralToken).where(
                AuthEphemeralToken.token_type == "media",
                AuthEphemeralToken.expires_at < now,
            )
        )
        db.add(
            AuthEphemeralToken(
                token=token,
                token_type="media",
                username=username or "",
                expires_at=expires_at,
                **session_dated(username),
            )
        )
        await db.commit()
    return token


async def verify_media_token(token: str) -> str | None:
    """Verify a media token, returning the username it was minted for.

    Returns ``""`` for a token minted by an API key (no per-row identity) and
    ``None`` when the token is missing / expired / unknown. Not consumed --
    one token serves every image on a page.

    Deliberately narrower than :func:`verify_camera_stream_token`: no
    long-lived scope passes here. ``camera_stream`` / ``camwall`` / ``overlay``
    tokens are handed to kiosks, walls and Home Assistant to display *video*,
    and are anonymous by construction, so accepting one would reinstate the
    unowned read this token type exists to close (#3025). The inverse also
    holds -- see :func:`verify_camwall_token`, which refuses a camera-stream
    token for the same reason in the other direction.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == token,
                AuthEphemeralToken.token_type == "media",
                AuthEphemeralToken.expires_at > now,
            )
        )
        row = result.scalar_one_or_none()
        if row is None or await token_outlived_its_session(db, row.username, row.created_at):
            return None
        return row.username or ""


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash.

    Uses pbkdf2_sha256 which handles long passwords automatically.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password.

    Uses pbkdf2_sha256 which is secure and has no password length limit.
    """
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token with jti (revocation) and iat (freshness) claims."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = secrets.token_hex(16)
    to_encode.update({"exp": expire, "jti": jti, "iat": now})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def _is_token_fresh(iat: int | float | None, user: User) -> bool:
    """Return False if the token was issued before the user's last password change.

    Used to invalidate all sessions after a password reset/change (M-R7-B).
    All tokens without an iat claim are unconditionally rejected — every token
    issued by this server carries iat, so absence means the token is forged or
    from a pre-iat code path whose max TTL at the time (24 h) has long since
    expired. The post-#1706 admin-set ceiling does not relax this — an iat-less
    token still cannot have been issued by current code.
    """
    if iat is None:
        return False
    if not hasattr(user, "password_changed_at") or user.password_changed_at is None:
        return True  # No password change recorded yet (I2 migration handles this)
    token_issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)
    pca = user.password_changed_at
    if pca.tzinfo is None:
        pca = pca.replace(tzinfo=timezone.utc)
    # JWT iat is whole seconds; truncate pca so tokens issued in the same second pass.
    pca = pca.replace(microsecond=0)
    return token_issued_at >= pca


async def revoke_jti(jti: str, expires_at: datetime, username: str | None = None) -> None:
    """Store a revoked JWT jti so it is rejected on future requests.

    Silently ignores duplicate inserts (e.g. double-logout with the same token).
    """
    from sqlalchemy.exc import IntegrityError

    async with async_session() as db:
        revoked = AuthEphemeralToken(
            token=jti,
            token_type="revoked_jti",
            username=username,
            expires_at=expires_at,
        )
        db.add(revoked)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()  # jti already revoked — desired state, ignore


async def is_jti_revoked(jti: str, db: AsyncSession | None = None) -> bool:
    """Return True if the given jti has been revoked.

    Pass ``db`` to reuse the caller's session instead of opening a new one
    (issue #2572): the permission dependencies already hold a session, and a
    second checkout per request doubled pool pressure — a login burst then
    exhausted the pool. With ``db`` omitted a short session is opened as before,
    for callers that check the jti before they have a session open.
    """

    async def _query(session: AsyncSession) -> bool:
        result = await session.execute(
            select(AuthEphemeralToken).where(
                AuthEphemeralToken.token == jti,
                AuthEphemeralToken.token_type == "revoked_jti",
            )
        )
        return result.scalar_one_or_none() is not None

    if db is not None:
        return await _query(db)
    async with async_session() as own_db:
        return await _query(own_db)


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Get a user by username (case-insensitive) with groups loaded for permission checks."""
    result = await db.execute(
        select(User).where(func.lower(User.username) == func.lower(username)).options(selectinload(User.groups))
    )
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Get a user by email (case-insensitive) with groups loaded for permission checks."""
    result = await db.execute(
        select(User).where(func.lower(User.email) == func.lower(email)).options(selectinload(User.groups))
    )
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """Authenticate a user by username and password.

    Username lookup is case-insensitive. Password is case-sensitive.
    LDAP and OIDC users must authenticate via their respective providers.
    """
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if getattr(user, "auth_source", "local") in ("ldap", "oidc"):
        return None  # LDAP/OIDC users must authenticate via their provider
    if not user.password_hash or not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


async def authenticate_user_by_email(db: AsyncSession, email: str, password: str) -> User | None:
    """Authenticate a user by email and password.

    Email lookup is case-insensitive. Password is case-sensitive.
    LDAP and OIDC users must authenticate via their respective providers.
    """
    user = await get_user_by_email(db, email)
    if not user:
        return None
    if getattr(user, "auth_source", "local") in ("ldap", "oidc"):
        return None  # LDAP/OIDC users must authenticate via their provider
    if not user.password_hash or not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


# Short-lived cache for the auth-enabled flag (issue #2572). The middleware
# and every ownership/permission dependency probe this once (or more) per
# request; on a large farm that DB round-trip is pure overhead because the
# value changes only when an admin toggles auth.
#
# SECURITY: only a ``True`` (auth-enabled) result is EVER cached. A disabled /
# unconfigured result is never cached, so a stale cache can only ever cause a
# request to REQUIRE auth that a moment ago wasn't required — it can never skip
# an auth check that is now required. Staleness fails CLOSED, never open (cf.
# GHSA-6mf4-q26m-47pv). ``set_auth_enabled`` invalidates explicitly on any
# toggle; the TTL is only a backstop for out-of-band changes (a direct DB edit,
# or another worker process in a multi-worker deployment).
_AUTH_ENABLED_CACHE_TTL_SECONDS = 30.0
_auth_enabled_cached_value: bool = False
_auth_enabled_cached_until: float = 0.0


def invalidate_auth_enabled_cache() -> None:
    """Drop the cached auth-enabled flag so the next probe re-reads the DB.

    Call after any write that toggles the ``auth_enabled`` setting.
    """
    global _auth_enabled_cached_value, _auth_enabled_cached_until
    _auth_enabled_cached_value = False
    _auth_enabled_cached_until = 0.0


async def is_auth_enabled(db: AsyncSession) -> bool:
    """Check if authentication is enabled.

    Fails CLOSED on database errors. A previous version of this function
    caught every exception and returned False — silently treating an
    unavailable database as "auth is disabled" and granting unauthenticated
    access to every endpoint that called it (GHSA-6mf4-q26m-47pv, CVSS 9.8).
    An attacker could trigger that fail-open by flooding /api/v1/auth/login
    to exhaust the process's file-descriptor budget, then hit a protected
    endpoint during the window where the next DB op raised.

    Legitimate "auth was never configured" still returns False — the
    settings row is simply absent, ``scalar_one_or_none`` returns None,
    no exception. Any OTHER failure (connection error, fd exhaustion,
    schema mismatch, …) propagates so the caller can deny the request
    (503 / 500). Fail-closed is the only safe default for an auth probe.

    Result is cached briefly to cut per-request DB load on large farms; only
    the enabled=True result is cached, so a stale read can only fail closed.
    See the module-level cache comment above.
    """
    global _auth_enabled_cached_value, _auth_enabled_cached_until
    if _auth_enabled_cached_value and time.monotonic() < _auth_enabled_cached_until:
        return True

    result = await db.execute(select(Settings).where(Settings.key == "auth_enabled"))
    setting = result.scalar_one_or_none()
    enabled = setting is not None and setting.value.lower() == "true"
    if enabled:
        _auth_enabled_cached_value = True
        _auth_enabled_cached_until = time.monotonic() + _AUTH_ENABLED_CACHE_TTL_SECONDS
    else:
        # Never cache "disabled" — keep failing closed on any future staleness.
        _auth_enabled_cached_value = False
    return enabled


async def _user_from_api_key(db: AsyncSession, api_key: APIKey) -> User | None:
    """Resolve the owner of a validated API key, or None for legacy ownerless keys.

    Cloud routes (and any route that needs caller identity) read the returned
    User to look up per-user state like ``cloud_token``. Legacy keys created
    before #1182 have ``user_id IS NULL`` and stay anonymous — they keep working
    against non-cloud routes for backward compatibility, but cloud routes will
    surface a "recreate this key" error rather than 200 with empty results.
    """
    if api_key.user_id is None:
        return None
    result = await db.execute(select(User).where(User.id == api_key.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        # CASCADE on user delete should prevent a dangling user_id, but if
        # someone manually deactivates the owner the key shouldn't suddenly
        # gain an "anonymous" identity — drop the request to None so cloud
        # access fails closed.
        return None
    return user


# The row a successful validation produced for the request in flight. Printer-
# scoped routes validate the same credential twice -- once in the permission
# gate, once for the key's printer allowlist -- and a validation is a pbkdf2
# verify plus a ``last_used`` write, so the second one is pure cost. Keyed by
# the raw credential so a request carrying two of them can never cross their
# rows, and held in a ContextVar so it cannot outlive the task that set it.
_validated_api_key: ContextVar[tuple[str, APIKey] | None] = ContextVar("_validated_api_key", default=None)

# Same idea for a JWT: the user the permission gate resolved, so the printer
# scope lookup (#1727) needn't decode, revocation-check and load it again.
# Keyed by the raw token for the same reason as above.
_authenticated_user: ContextVar[tuple[str, User] | None] = ContextVar("_authenticated_user", default=None)


# A key's last-used time is written at most once a minute (#3360).
_api_key_last_used = LastUsedThrottle(timedelta(minutes=1))


def _api_key_expired(api_key: APIKey) -> bool:
    if not api_key.expires_at:
        return False
    expires = api_key.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires < datetime.now(timezone.utc)


def _api_key_check_unavailable(error: Exception) -> HTTPException:
    """The 503 for a key that couldn't be checked (database, pool), logged.

    Not 401: the key may well be valid, and clients treat 401 "Invalid API key"
    as final (the web interface clears its sign-in on it). Still fail-closed:
    the request is refused either way.
    """
    logger.error("API key check failed: %s", error, exc_info=error)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication service temporarily unavailable",
    )


async def _validate_api_key(db: AsyncSession, api_key_value: str) -> APIKey | None:
    """Validate an API key and return the APIKey object if valid, None otherwise.

    Raises a 503 HTTPException when the key can't be checked at all (database
    or pool error): that is not an invalid key, and must not read as one. A
    failed ``last_used`` write is not such a case: the key has been checked by
    then, so it is logged and the key is accepted (#3360).

    L-1: Pre-filter by key_prefix (first 8 chars) before running pbkdf2 so only
    O(1) candidate rows are hashed instead of the full key table.  The prefix is
    not secret (it is shown in the admin UI), so this does not reduce security.
    """
    # key_prefix is stored as "<first-8-chars>..." (e.g. "bb_Abc12...").
    # Matching on the first 8 chars of the submitted key reduces the scan to
    # at most one row in practice (2^40 collision space for 5 base64 chars).
    key_lookup = api_key_value[:8] if len(api_key_value) >= 8 else api_key_value
    try:
        result = await db.execute(
            select(APIKey).where(
                APIKey.enabled.is_(True),
                APIKey.key_prefix.like(
                    key_lookup.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
                ),
            )
        )
        api_keys = result.scalars().all()
    except Exception as e:  # SEC-AUTH-EXC: key lookup failed -> raises 503, request refused (no key returned)
        raise _api_key_check_unavailable(e) from e

    for api_key in api_keys:
        try:
            matches = verify_password(api_key_value, api_key.key_hash)
        except PasswordSizeError:
            # The submitted value is too long to hash (passlib's limit): the
            # client's input, not this row, and certainly not a match.
            continue
        except Exception as e:  # SEC-AUTH-EXC: unverifiable hash counts as no match -> None -> 401
            # An unreadable stored hash can't vouch for anything: not a match.
            logger.warning("API key %s has an unreadable hash: %s", api_key.id, e)
            continue
        if not matches:
            continue
        if _api_key_expired(api_key):
            return None
        now = datetime.now(timezone.utc)
        if _api_key_last_used.due(api_key.key_hash, api_key.last_used, now):
            key_id = api_key.id
            api_key.last_used = now
            try:
                await db.commit()
            except Exception as e:  # SEC-AUTH-EXC: last_used write failed, key already matched -> re-read below
                # A read-only replica after a failover, a locked database: the
                # key matched, only the bookkeeping failed.
                await db.rollback()
                logger.warning("Could not record API key %s as used: %s", key_id, e)
                try:
                    await db.refresh(api_key)
                except Exception as e:  # SEC-AUTH-EXC: re-reading the key failed -> raise 503, key not returned
                    raise _api_key_check_unavailable(e) from e
                # Re-read, so it is checked again: it may have been turned off
                # or set to expire in the meantime.
                if not api_key.enabled or _api_key_expired(api_key):
                    return None
        _validated_api_key.set((api_key_value, api_key))
        return api_key
    return None


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User | None:
    """Get the current authenticated user from JWT token, or None if not authenticated.

    Returns None only when NO credentials are supplied.  If a token is supplied
    but invalid/revoked, raises 401 — a revoked token must not grant anonymous
    access (I6).
    """
    if credentials is None:
        return None

    _unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise _unauthorized
        jti: str | None = payload.get("jti")
        iat: int | float | None = payload.get("iat")
    except JWTError:
        raise _unauthorized

    if not jti:
        raise _unauthorized  # I6: revoked token → 401, not anonymous

    async with async_session() as db:
        if await is_jti_revoked(jti, db):
            raise _unauthorized  # I6: revoked token → 401, not anonymous
        user = await get_user_by_username(db, username)
        if user is None or not user.is_active:
            raise _unauthorized
        if not _is_token_fresh(iat, user):
            raise _unauthorized
        return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User:
    """Get the current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise credentials_exception
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        jti: str | None = payload.get("jti")
        iat: int | float | None = payload.get("iat")
    except JWTError:
        raise credentials_exception

    if not jti:
        raise credentials_exception

    async with async_session() as db:
        if await is_jti_revoked(jti, db):
            raise credentials_exception
        user = await get_user_by_username(db, username)
        if user is None:
            raise credentials_exception
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled",
            )
        if not _is_token_fresh(iat, user):
            raise credentials_exception
        # What this request mints counts from this session (_minting_session_start)
        _authenticated_user.set((token, user))
        return user


async def get_current_active_user(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Get the current active user (alias for clarity)."""
    return current_user


async def require_auth_if_enabled(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> User | None:
    """Require authentication if auth is enabled, otherwise return None.

    Accepts both JWT tokens (via Authorization: Bearer header) and API keys
    (via X-API-Key header or Authorization: Bearer bb_xxx). API keys return
    None for backward compatibility — routes that need the API-key owner (i.e.
    cloud routes for #1182) resolve it via their own router-level dependency
    that stashes ``request.state.api_key_owner``. Returning the owner here
    instead would silently grant API-keyed callers access to every route that
    fences via ``if current_user is None``, which is a wider surface than
    #1182 was designed to expose.
    """
    async with async_session() as db:
        auth_enabled = await is_auth_enabled(db)
        if not auth_enabled:
            return None

        # Check for API key first (X-API-Key header). The owner is resolved
        # purely for its side effect: a key whose owner has been deactivated
        # must be dead everywhere, not just on the permission-gated routes.
        # There is no permission to check here -- this dep is auth-only.
        if x_api_key:
            api_key = await _validate_api_key(db, x_api_key)
            if api_key:
                await resolve_apikey_owner(db, api_key)
                return None  # API key valid, allow access

        # Check for Bearer token (could be JWT or API key)
        if credentials is not None:
            token = credentials.credentials
            # Check if it's an API key (starts with bb_)
            if token.startswith("bb_"):
                api_key = await _validate_api_key(db, token)
                if api_key:
                    await resolve_apikey_owner(db, api_key)
                    return None  # API key valid, allow access
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Otherwise treat as JWT
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                username: str = payload.get("sub")
                if username is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                jti: str | None = payload.get("jti")
                if not jti or await is_jti_revoked(jti, db):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                iat: int | float | None = payload.get("iat")
            except JWTError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not _is_token_fresh(iat, user):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            # What this request mints counts from this session (_minting_session_start)
            _authenticated_user.set((credentials.credentials, user))
            return user

        # No credentials provided
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_admin_if_auth_enabled():
    """Dependency factory that requires admin role if auth is enabled.

    GHSA-r2qv follow-up (audit pattern P3): explicitly fail-closed for API
    keys. The previous implementation chained on ``require_auth_if_enabled``
    which returns ``None`` for *both* "auth disabled" *and* "valid API
    key" — the inner ``admin_checker`` then treated ``None`` as auth-
    disabled and admitted the caller. If any route had ever adopted this
    dep, any API key with no scope flags set would have satisfied an
    admin requirement. The dep distinguishes the two cases by consulting
    ``is_auth_enabled`` directly and rejecting API-keyed requests with
    403. "Admin" requires a user-identity role, which API keys do not
    carry.

    Admin semantics: uses ``User.is_admin`` (``role == "admin"`` OR
    Administrators-group membership) so a default-install operator who
    was made admin by being added to Administrators rather than by
    flipping the legacy role column passes. Earlier this check looked
    only at ``role`` and would have locked group-only admins out of the
    user-management routes once those routes started requiring it.
    """

    async def admin_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return None  # Auth disabled — no role to check.

            # Reject API-keyed requests up front: admin is a user-role
            # concept, not a key-scope concept. The right path for
            # admin-equivalent API-key access is a specific Permission
            # (e.g. SETTINGS_UPDATE) gated by the allowlist, not the
            # admin role.
            if x_api_key or (credentials and credentials.credentials.startswith("bb_")):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin operations require a user role; API keys cannot be admins",
                )

            # Standard JWT path: validate and require admin role.
            if credentials is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            try:
                payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
                username: str = payload.get("sub")
                if username is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                jti: str | None = payload.get("jti")
                if not jti or await is_jti_revoked(jti, db):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                iat: int | float | None = payload.get("iat")
            except JWTError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not _is_token_fresh(iat, user):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not user.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Requires admin role",
                )
            return user

    return admin_checker


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        tuple: (full_key, key_hash, key_prefix)
            - full_key: The complete API key (only shown once on creation)
            - key_hash: Hashed version for storage and verification
            - key_prefix: First 8 characters for display purposes
    """
    # Generate a secure random API key (32 bytes = 64 hex characters)
    full_key = f"bb_{secrets.token_urlsafe(32)}"
    key_hash = get_password_hash(full_key)
    key_prefix = full_key[:8] + "..." if len(full_key) > 8 else full_key
    return full_key, key_hash, key_prefix


async def get_api_key(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> APIKey:
    """Get and validate API key from request headers (the webhook routes).

    Checks both 'Authorization: Bearer <key>' and 'X-API-Key: <key>' headers;
    the key itself is checked by _validate_api_key. An expired key is an
    invalid key, as on every other route.
    """
    api_key_value = None
    if x_api_key:
        api_key_value = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        api_key_value = authorization.replace("Bearer ", "")

    if not api_key_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Provide 'X-API-Key' header or 'Authorization: Bearer <key>'",
        )

    # The same check as every other API-key route: a key that can't be checked
    # (database error) is a 503 raised in there, not an invalid key.
    api_key = await _validate_api_key(db, api_key_value)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return api_key


async def caller_is_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> bool:
    """Return True when the request is authenticated via API key (X-API-Key or Bearer bb_xxx)."""
    if x_api_key:
        return True
    return credentials is not None and credentials.credentials.startswith("bb_")


def check_permission(api_key: APIKey, permission: str) -> None:
    """Check if API key has the required permission.

    Args:
        api_key: The API key object
        permission: One of 'queue', 'control_printer', 'read_status'

    Raises:
        HTTPException: If permission is not granted
    """
    permission_map = {
        "queue": "can_queue",
        "control_printer": "can_control_printer",
        "read_status": "can_read_status",
    }

    if permission not in permission_map:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unknown permission: {permission}",
        )

    attr_name = permission_map[permission]
    if not getattr(api_key, attr_name, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not have '{permission}' permission",
        )


# The coarse webhook permission names predate the Permission enum. Each maps to
# the enum member that best represents it, so the owner can be held to the same
# standard here as on the modern routes.
_WEBHOOK_PERMISSION_EQUIVALENT: dict[str, Permission] = {
    "queue": Permission.QUEUE_CREATE,
    "control_printer": Permission.PRINTERS_CONTROL,
    "read_status": Permission.PRINTERS_READ,
}


async def check_webhook_permission(db: AsyncSession, api_key: APIKey, permission: str) -> None:
    """``check_permission`` plus the owner checks the modern routes apply.

    ``/webhook/*`` reaches its scope flags through ``check_permission`` rather
    than ``_check_apikey_permissions``, so it does not pick up the owner
    narrowing automatically. Without this it would be the way around the gate:
    the same key that is refused printer control on ``/printers/{id}/print/stop``
    could stop the print through ``/webhook/printer/{id}/stop``.
    """
    check_permission(api_key, permission)
    owner = await resolve_apikey_owner(db, api_key)
    equivalent = _WEBHOOK_PERMISSION_EQUIVALENT.get(permission)
    if owner is not None and equivalent is not None and not owner.has_permission(equivalent.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key owner does not have '{equivalent.value}' permission",
        )


async def api_key_printer_scope(db: AsyncSession, api_key: APIKey) -> PrinterScope:
    """The printers ``api_key`` may reach: its own allowlist within its owner's scope.

    Raises 403 when the owner was deactivated or deleted, like every other
    owner check (see ``resolve_apikey_owner``).
    """
    scope = api_key_own_scope(api_key)
    owner = await resolve_apikey_owner(db, api_key)
    if owner is not None:
        scope = scope.intersect(await resolve_user_printer_scope(db, owner))
    return scope


async def ensure_api_key_printer_access(db: AsyncSession, api_key: APIKey, printer_id: int) -> None:
    """Raise 404 unless ``api_key`` may reach ``printer_id`` (see ``api_key_printer_scope``)."""
    (await api_key_printer_scope(db, api_key)).ensure(printer_id)


async def validated_api_key_from_request(
    credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> APIKey | None:
    """Return the validated API key carried by a request, if any.

    Permission dependencies intentionally return ``None`` for API-key callers so
    routes do not mistake a key for a user identity (the ownership dependency
    returns an ``ApiKeyActor`` for a key whose owner may act on own rows only).
    Printer-bound routes still need the key row to enforce ``printer_ids`` after
    the normal scope/owner permission gate has run. This helper recognizes both
    supported transports.
    """

    candidate = x_api_key
    if candidate is None and credentials is not None and credentials.credentials.startswith("bb_"):
        candidate = credentials.credentials
    if candidate is None:
        return None
    cached = _validated_api_key.get()
    if cached is not None and cached[0] == candidate:
        return cached[1]
    async with async_session() as db:
        api_key = await _validate_api_key(db, candidate)
        if api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Touch the JSON-backed value before detaching the row from the session.
        _ = api_key.printer_ids
        return api_key


async def current_api_key_if_present(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> APIKey | None:
    """FastAPI dependency exposing only an authenticated API-key principal."""

    return await validated_api_key_from_request(credentials, x_api_key)


async def resolve_request_printer_scope(
    credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> PrinterScope:
    """The printer scope of whoever sent this request (#1727).

    Meant to run after the route's permission gate, which has already turned
    away bad credentials; it reuses what that gate resolved where it can. With
    auth on and no usable principal it returns an empty scope, never every
    printer.
    """
    async with async_session() as db:
        if not await is_auth_enabled(db):
            return ALL_PRINTERS
        api_key = await validated_api_key_from_request(credentials, x_api_key)
        if api_key is not None:
            return await api_key_printer_scope(db, api_key)
        if credentials is None:
            return PrinterScope(frozenset())
        cached = _authenticated_user.get()
        if cached is not None and cached[0] == credentials.credentials:
            user = cached[1]
        else:
            user = await get_current_user_optional(credentials)
            if user is None:
                return PrinterScope(frozenset())
        return await resolve_user_printer_scope(db, user)


async def get_printer_scope_if_auth_enabled(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> PrinterScope:
    """FastAPI dependency for ``resolve_request_printer_scope``.

    Declare it after the permission dependency so the gate runs first.
    """
    return await resolve_request_printer_scope(credentials, x_api_key)


RequestPrinterScope = Depends(get_printer_scope_if_auth_enabled)


def queue_review_required_for(user: User | ApiKeyActor | None) -> bool:
    """Whether jobs ``user`` queues must wait for someone to start them (#1620).

    None is auth-off or a legacy ownerless API key, neither of which has a
    reviewer above it. Whoever may start every job (queue:update_all) is the
    reviewer, so their own jobs don't wait either.
    """
    return (
        user is not None
        and not user.has_permission(Permission.QUEUE_START_UNREVIEWED.value)
        and not user.has_permission(Permission.QUEUE_UPDATE_ALL.value)
    )


def may_start_queue_item(user: User | ApiKeyActor | None, can_modify_all: bool, created_by_id: int | None) -> bool:
    """Whether the caller may start a waiting queue item (#1620).

    ``can_modify_all`` is what ``require_ownership_permission`` answered for
    queue:update_all; an API key only gets it when its owner holds that. Anyone
    else needs to be allowed to print without review, and the item must be
    theirs or have no owner yet (a virtual-printer upload, claimed by starting
    it, #1670).
    """
    if can_modify_all or user is None:
        return True
    if queue_review_required_for(user):
        return False
    return created_by_id is None or created_by_id == user.id


async def get_queue_review_required(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> bool:
    """FastAPI dependency: must the caller's new queue items wait for review (#1620)?

    Permission dependencies answer API-key requests with no user, so the key's
    owner decides here. Declare it after the permission dependency. With auth
    on and no usable principal it answers True.
    """
    async with async_session() as db:
        if not await is_auth_enabled(db):
            return False
        api_key = await validated_api_key_from_request(credentials, x_api_key)
        if api_key is not None:
            return queue_review_required_for(await resolve_apikey_owner(db, api_key))
        if credentials is None:
            return True
        cached = _authenticated_user.get()
        if cached is not None and cached[0] == credentials.credentials:
            user = cached[1]
        else:
            user = await get_current_user_optional(credentials)
            if user is None:
                return True
        return queue_review_required_for(user)


QueueReviewRequired = Depends(get_queue_review_required)


async def get_request_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> User | ApiKeyActor | None:
    """FastAPI dependency: who a route's own ownership checks run against.

    The signed-in user, or for an API key an ``ApiKeyActor`` for its owner.
    None only when auth is off. Declare it after the permission dependency,
    which has already turned away bad credentials.
    """
    async with async_session() as db:
        if not await is_auth_enabled(db):
            return None
        api_key = await validated_api_key_from_request(credentials, x_api_key)
        if api_key is not None:
            return ApiKeyActor(api_key, await resolve_apikey_owner(db, api_key))
    user = None
    if credentials is not None:
        cached = _authenticated_user.get()
        if cached is not None and cached[0] == credentials.credentials:
            user = cached[1]
        else:
            user = await get_current_user_optional(credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


RequestActor = Depends(get_request_actor)


async def actor_still_exists(db: AsyncSession, actor: User | ApiKeyActor | None) -> bool:
    """Whether the user a request started as still exists.

    A request that downloads or extracts for seconds to minutes writes the id
    it started with. A user deleted meanwhile leaves it pointing at nobody, or
    on SQLite, which gives a freed id to the next user created, at someone
    else; the name tells them apart. No actor (auth off) or a key without an
    owner has no user to lose.
    """
    if actor is None or actor.id is None:
        return True
    found = await db.scalar(select(User.id).where(User.id == actor.id, User.username == actor.username))
    return found is not None


async def require_actor_still_exists(db: AsyncSession, actor: User | ApiKeyActor | None) -> None:
    """Stop a request whose user was deleted while it ran, before it saves anything as theirs."""
    if not await actor_still_exists(db, actor):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This account was deleted")


async def get_media_or_request_printer_scope(
    token: str | None = None,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> PrinterScope:
    """``RequestPrinterScope`` for routes an ``<img>`` may load with ``?token=``.

    Such a request carries a media token and no headers, so the header-based
    lookup would find nobody and answer with no printers. A media token wins
    over any headers sent with it, exactly as in ``require_media_token_*``:
    the printers are always those of the caller the permission check named.
    """
    if token:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return ALL_PRINTERS
            username = await verify_media_token(token)
            if not username:
                return _NO_PRINTERS
            user = await get_user_by_username(db, username)
            if user is None or not user.is_active:
                return _NO_PRINTERS
            return await resolve_user_printer_scope(db, user)
    return await resolve_request_printer_scope(credentials, x_api_key)


MediaOrRequestPrinterScope = Depends(get_media_or_request_printer_scope)


def require_printer_permission_if_auth_enabled(*permissions: str | Permission):
    """Require permissions and that the path's ``printer_id`` is in the caller's scope.

    For routes with ``{printer_id}`` in the path. A printer outside the scope
    gets 404, the same as one that doesn't exist.
    """

    permission_checker = require_permission_if_auth_enabled(*permissions)

    async def checker(
        printer_id: int,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        user = await permission_checker(credentials=credentials, x_api_key=x_api_key)
        scope = await resolve_request_printer_scope(credentials, x_api_key)
        scope.ensure(printer_id)
        return user

    return checker


# Convenience dependencies - these are functions that return Depends objects


def RequireAdminIfAuthEnabled():
    """Dependency that requires admin role if auth is enabled."""
    return Depends(require_admin_if_auth_enabled())


def require_permission_if_auth_enabled(*permissions: str | Permission):
    """Dependency factory that checks permissions only if auth is enabled.

    This provides backward compatibility - when auth is disabled, all access is allowed.
    Accepts both JWT tokens (via Authorization: Bearer header) and API keys
    (via X-API-Key header or Authorization: Bearer bb_xxx).

    Args:
        *permissions: Permission strings or Permission enum values to require

    Returns:
        A dependency function that validates permissions if auth is enabled
    """
    # Convert Permission enums to strings
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]

    async def permission_checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None  # Auth disabled, allow access

            # Check for API key first (X-API-Key header). API-keyed requests
            # bypass the JWT permission check entirely — their scopes live on
            # the APIKey row (can_queue / can_control_printer / can_read_status
            # / can_access_cloud / printer_ids), and the dep returns None so
            # routes don't gain a synthetic User identity that would grant
            # access to fenced surfaces like long-lived-token management.
            # Cloud routes (#1182) resolve the API-key owner separately via
            # their own router-level dependency; see ``cloud.py``.
            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    await authorize_api_key(db, api_key, perm_strings)
                    return None  # API key valid, allow access

            # Check for Bearer token (could be JWT or API key)
            if credentials is not None:
                token = credentials.credentials
                # Check if it's an API key (starts with bb_)
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        await authorize_api_key(db, api_key, perm_strings)
                        return None  # API key valid, allow access
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                # Otherwise treat as JWT
                try:
                    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                    username: str = payload.get("sub")
                    if username is None:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    jti: str | None = payload.get("jti")
                    if not jti or await is_jti_revoked(jti, db):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    iat: int | float | None = payload.get("iat")
                except JWTError:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                user = await get_user_by_username(db, username)
                if user is None or not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                if not _is_token_fresh(iat, user):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                if not user.has_all_permissions(*perm_strings):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Missing required permissions: {', '.join(perm_strings)}",
                    )
                _authenticated_user.set((token, user))
                return user

            # No credentials provided
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return permission_checker


def RequirePermissionIfAuthEnabled(*permissions: str | Permission):
    """Convenience dependency that requires permissions if auth is enabled."""
    return Depends(require_permission_if_auth_enabled(*permissions))


def RequirePrinterPermissionIfAuthEnabled(*permissions: str | Permission):
    """Require permissions plus the caller's printer scope for the path's ``printer_id``."""

    return Depends(require_printer_permission_if_auth_enabled(*permissions))


def probe_permissions_if_auth_enabled(*permissions: str | Permission):
    """Return permission availability while preserving authentication errors.

    This is for endpoints that can return a useful permission-independent
    subset. Missing permissions become ``False``; invalid or absent credentials
    still retain the normal 401 response from the shared permission checker.
    """

    permission_checker = require_permission_if_auth_enabled(*permissions)

    async def checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> bool:
        try:
            await permission_checker(credentials, x_api_key)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_403_FORBIDDEN:
                return False
            raise
        return True

    return checker


def require_any_permission_if_auth_enabled(*permissions: str | Permission):
    """Dependency factory that requires AT LEAST ONE of the given permissions when auth is enabled."""
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]

    async def checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None

            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    # GHSA-r2qv-8222-hqg3: previously returned None unconditionally,
                    # letting any valid API key satisfy admin "any-of" route
                    # dependencies. require_any → at-least-one must pass the scope check.
                    await authorize_api_key(db, api_key, perm_strings, require_any=True)
                    return None

            if credentials is not None:
                token = credentials.credentials
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        await authorize_api_key(db, api_key, perm_strings, require_any=True)
                        return None
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                try:
                    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                    username: str = payload.get("sub")
                    if username is None:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    jti: str | None = payload.get("jti")
                    if not jti or await is_jti_revoked(jti, db):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    iat: int | float | None = payload.get("iat")
                except JWTError:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                user = await get_user_by_username(db, username)
                if user is None or not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                if not _is_token_fresh(iat, user):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                if not user.has_any_permission(*perm_strings):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Missing required permissions: {', '.join(perm_strings)}",
                    )
                _authenticated_user.set((token, user))
                return user

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return checker


def RequireAnyPermissionIfAuthEnabled(*permissions: str | Permission):
    """Convenience dependency that requires AT LEAST ONE of the given permissions when auth is enabled."""
    return Depends(require_any_permission_if_auth_enabled(*permissions))


def require_camera_stream_token_if_auth_enabled():
    """Dependency that validates a camera stream token query param when auth is enabled.

    Used for camera stream/snapshot endpoints that are loaded via <img> tags
    which cannot send Authorization headers. The frontend obtains a token from
    POST /printers/camera/stream-token and appends it as ?token=xxx.

    Camera routes only. Non-camera media (thumbnails, plate previews,
    timelapses, cover images, icons) takes ``require_media_token_*``: minting a
    camera-stream token costs ``camera:view``, which no thumbnail should
    require, and the token names no principal, so a route guarded by it cannot
    tell one user's rows from another's (#3025).
    """

    async def checker(printer_id: int, token: str | None = None) -> StreamOpener:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return StreamOpener(auth_on=False)  # Auth disabled, allow access
        access = await camera_stream_access(token) if token else None
        if access is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid camera stream token required. Obtain one from POST /api/v1/printers/camera/stream-token",
            )
        scope, opener = access
        # The token's minter may not see this printer (#1727)
        scope.ensure(printer_id)
        return opener

    return checker


RequireCameraStreamTokenIfAuthEnabled = Depends(require_camera_stream_token_if_auth_enabled())


def require_camwall_token_if_auth_enabled():
    """Dependency that validates a Cam Wall token query param when auth is enabled.

    Used by the read-only Cam Wall feed (#2531), which a kiosk browser loads
    with the token in the URL because it has no login session to carry a JWT.
    Returns the token owner's printer scope, which the feed filters by (#1727).
    """

    async def checker(token: str | None = None) -> PrinterScope:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return ALL_PRINTERS  # Auth disabled, allow access
        scope = await verify_camwall_token(token) if token else None
        if scope is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid Cam Wall token required. Create one under Settings > API Keys with the 'Cam Wall' scope.",
            )
        return scope

    return checker


RequireCamWallTokenIfAuthEnabled = Depends(require_camwall_token_if_auth_enabled())


def require_overlay_token_if_auth_enabled():
    """Dependency that validates a streaming-overlay token query param when auth
    is enabled.

    Used by the read-only overlay status feed (#2613), which OBS (or any
    embed with no login session) loads with the token in the URL because it
    has no JWT to carry.
    """

    async def checker(printer_id: int, token: str | None = None) -> None:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return  # Auth disabled, allow access
        scope = await verify_overlay_token(token) if token else None
        if scope is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid overlay token required. Create one under Settings > API Keys with the 'Streaming Overlay' scope.",
            )
        # The token owner may not see this printer (#1727)
        scope.ensure(printer_id)

    return checker


RequireOverlayTokenIfAuthEnabled = Depends(require_overlay_token_if_auth_enabled())


def require_overlay_token_any_printer_if_auth_enabled():
    """The overlay-token check for overlay assets that belong to no printer.

    The overlay logo (#3104) is one per installation, so there is no printer
    for the token owner's scope (#1727) to be checked against; a valid overlay
    token is all it takes, as before.
    """

    async def checker(token: str | None = None) -> None:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return  # Auth disabled, allow access
        if token is None or await verify_overlay_token(token) is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid overlay token required. Create one under Settings > API Keys with the 'Streaming Overlay' scope.",
            )

    return checker


RequireOverlayTokenAnyPrinterIfAuthEnabled = Depends(require_overlay_token_any_printer_if_auth_enabled())


def require_ownership_permission(
    all_permission: str | Permission,
    own_permission: str | Permission,
):
    """Dependency factory for ownership-based permission checks.

    - User with ``all_permission`` can modify any item
    - User with ``own_permission`` can only modify items where created_by_id == user.id
    - Ownerless items (created_by_id = null) require ``all_permission``
    - API keys (via X-API-Key header or Bearer bb_xxx) act as their owner:
      a key whose owner holds ``all_permission`` gets ``(None, True)``; a key
      whose owner holds only ``own_permission`` gets ``(ApiKeyActor, False)``,
      so the route's per-row check compares rows against the owner's id.
      Either way the key needs the permission's scope flag (OWN and ALL map
      to the same one). A legacy key without an owner owns nothing and still
      needs ``all_permission``. Pre-GHSA-r2qv-8222-hqg3 this returned
      ``(None, True)`` for any valid key with no scope check — see
      ``core/auth.py`` allowlist commentary.

    Returns:
        A dependency function that returns (user, can_modify_all).
        - can_modify_all=True: user can modify any item
        - can_modify_all=False: user can only modify their own items
    """
    all_perm = all_permission.value if isinstance(all_permission, Permission) else all_permission
    own_perm = own_permission.value if isinstance(own_permission, Permission) else own_permission

    async def _api_key_ownership(db: AsyncSession, api_key: APIKey) -> tuple[ApiKeyActor | None, bool]:
        owner = await resolve_apikey_owner(db, api_key)
        if owner is None:
            # A legacy key without an owner has no id to own rows by.
            _check_apikey_permissions(api_key, [all_perm])
            return None, True
        _check_apikey_permissions(api_key, [all_perm, own_perm], owner=owner, require_any=True)
        actor = ApiKeyActor(api_key, owner)
        if actor.has_permission(all_perm):
            return None, True
        return actor, False

    async def checker(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> tuple[User | ApiKeyActor | None, bool]:
        """Returns (user, can_modify_all).

        - can_modify_all=True: user can modify any item
        - can_modify_all=False: user can only modify their own items
        """
        async with async_session() as db:
            auth_enabled = await is_auth_enabled(db)
            if not auth_enabled:
                return None, True  # Auth disabled, allow all

            # GHSA-r2qv-8222-hqg3: previously API keys received (None, True)
            # unconditionally on ownership-modify routes — a "queue-only" key
            # could delete any user's archives, library files, queue items.
            # A key now passes only with the scope flag, and acts as its owner.
            if x_api_key:
                api_key = await _validate_api_key(db, x_api_key)
                if api_key:
                    return await _api_key_ownership(db, api_key)

            # Check for Bearer token (could be JWT or API key)
            if credentials is not None:
                token = credentials.credentials
                # Check if it's an API key (starts with bb_)
                if token.startswith("bb_"):
                    api_key = await _validate_api_key(db, token)
                    if api_key:
                        return await _api_key_ownership(db, api_key)
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API key",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                # Otherwise treat as JWT
                try:
                    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                    username: str = payload.get("sub")
                    if username is None:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    jti: str | None = payload.get("jti")
                    if not jti or await is_jti_revoked(jti, db):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    iat: int | float | None = payload.get("iat")
                except JWTError:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                user = await get_user_by_username(db, username)
                if user is None or not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                if not _is_token_fresh(iat, user):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                if user.has_permission(all_perm):
                    return user, True
                if user.has_permission(own_perm):
                    return user, False

                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing permission: {own_perm} or {all_perm}",
                )

            # No credentials provided
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return checker


async def _user_from_media_token(token: str) -> User:
    """Resolve the ``User`` a media token was minted for, or raise 401 (#3025).

    Fail-closed on every miss: an unknown/expired token, a token minted by an
    API key (empty username -- see :func:`create_media_token`), a username no
    longer in the table, and a deactivated account all raise rather than fall
    through to an anonymous read. The 401 detail names the mint endpoint so a
    stale tab knows how to recover, and the frontend's error handler refreshes
    the token on the first failed <img> load.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid media token required. Obtain one from POST /api/v1/auth/media-token",
    )
    username = await verify_media_token(token)
    if not username:
        raise unauthorized
    async with async_session() as db:
        user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_media_token_permission(*permissions: str | Permission):
    """Media-route dependency for resources with no per-row ownership (#3025).

    Accepts either a ``?token=`` media token (the ``<img>`` case) or the
    ordinary ``Authorization`` / ``X-API-Key`` headers, so a ``fetch()`` or an
    API-keyed integration authenticates here exactly as it does on the
    resource's sibling routes. Requires ALL of ``permissions``, matching
    :func:`require_permission_if_auth_enabled`.

    Returns the resolved ``User``, or ``None`` when auth is disabled or the
    caller is an API key -- the same ``User | None`` contract the header-only
    dependency has, so handlers need no new branch.
    """
    perm_strings = [p.value if isinstance(p, Permission) else p for p in permissions]
    header_checker = require_permission_if_auth_enabled(*permissions)

    async def checker(
        token: str | None = None,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return None  # Auth disabled, allow access
        if token:
            user = await _user_from_media_token(token)
            missing = [p for p in perm_strings if not user.has_permission(p)]
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permissions: {', '.join(missing)}",
                )
            return user
        return await header_checker(credentials=credentials, x_api_key=x_api_key)

    return checker


def require_media_token_ownership(
    all_permission: str | Permission,
    own_permission: str | Permission,
):
    """Media-route dependency for ownership-scoped resources (#3025).

    The ownership counterpart of :func:`require_media_token_permission`, and
    the reason media tokens carry a principal at all: it returns the same
    ``(user, can_read_all)`` pair as :func:`require_ownership_permission`, so a
    thumbnail route can hand it straight to the ``_ensure_*_visible`` gate its
    header-authenticated siblings already use instead of serving any row to any
    token holder.

    Header callers are delegated to :func:`require_ownership_permission`
    unchanged -- including its API-key rule, where a key acts as its owner: its
    owner's ALL permission gives ``can_read_all=True``, the OWN one an
    ``ApiKeyActor`` limited to the owner's rows.
    """
    all_perm = all_permission.value if isinstance(all_permission, Permission) else all_permission
    own_perm = own_permission.value if isinstance(own_permission, Permission) else own_permission
    header_checker = require_ownership_permission(all_permission, own_permission)

    async def checker(
        token: str | None = None,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> tuple[User | ApiKeyActor | None, bool]:
        async with async_session() as db:
            if not await is_auth_enabled(db):
                return None, True  # Auth disabled, allow all
        if token:
            user = await _user_from_media_token(token)
            if user.has_permission(all_perm):
                return user, True
            if user.has_permission(own_perm):
                return user, False
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {own_perm} or {all_perm}",
            )
        return await header_checker(credentials=credentials, x_api_key=x_api_key)

    return checker


def require_media_token_printer_permission(permission: str | Permission):
    """Media-route dependency for per-printer resources (#3025).

    :func:`require_media_token_permission` plus the caller's printer scope for
    the path's ``printer_id``, mirroring
    :func:`require_printer_permission_if_auth_enabled`. A media token names a
    user, so their scope applies; a header caller gets the request's scope.
    """
    media_checker = require_media_token_permission(permission)

    async def checker(
        printer_id: int,
        token: str | None = None,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> User | None:
        user = await media_checker(token=token, credentials=credentials, x_api_key=x_api_key)
        if token and user is not None:
            async with async_session() as db:
                scope = await resolve_user_printer_scope(db, user)
        else:
            scope = await resolve_request_printer_scope(credentials, x_api_key)
        scope.ensure(printer_id)
        return user

    return checker
