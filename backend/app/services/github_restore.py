"""Restore Bambuddy data from a Git provider backup (issue #2656).

The backup side (``github_backup.py``) is push-only: it collects a handful of
JSON documents and commits them. This module is the read side — it walks the
backup repository's history, lets a caller inspect what a given commit contains,
and applies selected categories back into the local database (or, for
K-profiles, back onto the printers).

Design notes worth knowing before editing:

* **A restore never reuses the backup's primary keys.** ``spool.id`` and
  ``print_archives.id`` are bare autoincrement columns, so the ids in a backup
  taken weeks ago very likely belong to unrelated rows today. Rows are matched
  on natural keys instead, inserted without an explicit id, and an
  ``old_id -> new_id`` map is threaded through so foreign keys in dependent
  tables (spool usage history) still line up.

  The printer-side ``cali_idx`` behaves the same way and gets the same
  treatment. Editing a K-profile in Bambuddy is a delete-then-add on a
  single-nozzle printer, which re-keys it, and ``extrusion_cali_set`` aimed at a
  slot that no longer exists is silently dropped — so the live index is read
  back and matched before writing, never taken from the backup.
* **Categories are applied archives -> spools -> settings -> kprofiles.**
  Archives first because spool usage history references ``archive_id``;
  K-profiles last because they leave the database and talk to hardware.
* **Cloud profiles are not restorable.** Restoring a preset means writing to a
  Bambu or Orca Cloud account, which is a different operation from everything
  else here — every other category lands in the local database or, for
  K-profiles, on a printer the instance already owns. Tracked separately from
  #2656. (The collector does write ``cloud_profiles/*.json`` as of #2717; the
  earlier claim that it did not is no longer true.)
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field as dataclasses_field
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import async_session
from backend.app.models.archive import PrintArchive
from backend.app.models.github_backup import GitHubBackupConfig, GitHubBackupLog
from backend.app.models.printer import Printer
from backend.app.models.project import Project
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory
from backend.app.models.user import User
from backend.app.schemas.github_backup import RestoreCategory
from backend.app.services.git_providers.factory import get_provider_backend
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

METADATA_PATH = "backup_metadata.json"
SETTINGS_PATH = "settings/app_settings.json"
SPOOLS_PATH = "spools/inventory.json"
SPOOL_USAGE_PATH = "spools/usage_history.json"
ARCHIVES_PATH = "archives/print_history.json"

# kprofiles/{printer_serial}/{nozzle_diameter}.json
_KPROFILE_PATH_RE = re.compile(r"^kprofiles/([^/]+)/([^/]+)\.json$")

# Settings keys the backup collector already refuses to write. Applied again on
# the read side because a backup taken before that denylist existed can still
# contain them, and a restore must not resurrect a stale credential.
_SENSITIVE_SETTING_KEYS = {"bambu_cloud_token", "auth_secret_key"}

# The primary refusal, not a backstop for the set above. The collector filters
# exactly bambu_cloud_token and auth_secret_key, so every other credential —
# mqtt_password, ldap_bind_password, ha_token, prometheus_token — is present in
# a current backup and is skipped only because its key matches a hint here.
# _COMPANION_CREDENTIALS sits downstream of that: it withholds a toggle when the
# credential it needs was refused, so shortening this tuple would both write a
# stale credential and quietly make that rule inert.
_SECRET_KEY_HINTS = ("token", "secret", "password", "access_code", "api_key", "passphrase")

# Settings the MQTT relay reads only when it is (re)configured, so restoring the
# rows is not enough on its own. Mirrors the set the settings PUT handler
# watches. mqtt_password is in here for the configure() payload's sake — the
# credential blocklist means a restore never writes it.
_MQTT_SETTING_KEYS = {
    "mqtt_enabled",
    "mqtt_broker",
    "mqtt_port",
    "mqtt_username",
    "mqtt_password",
    "mqtt_topic_prefix",
    "mqtt_use_tls",
}

# Keys that decide *who can reach the instance* rather than how it behaves. The
# backup collector writes them like any other Settings row, so a backup taken
# before auth was turned on carries auth_enabled=false — and a restore reaches
# the table directly, so honouring them would:
#
#   * disable authentication outright. ``set_auth_enabled`` pairs its write with
#     ``invalidate_auth_enabled_cache()``; we cannot, so the 30 s TTL in
#     core.auth is the only thing between the write and an open instance. That
#     cache is built to fail closed — writing the stored value behind its back
#     is what would make it fail open.
#   * bypass the lockout refusals ``update_settings`` enforces (a
#     ``local_login_enabled=false`` with no enabled OIDC provider, or with no
#     OIDC link on the caller, is a 400 there — #1589).
#   * cross a permission boundary: a restore would be a way to rewrite auth
#     config without SETTINGS_UPDATE. (The endpoint gates each category on the
#     permission owning its rows now, but that is settings:update — still not
#     the auth UI's own guards, which is what these keys actually need.)
#
# Auth is reconfigured through the auth UI, which has the guards. Restoring it
# from a snapshot has no safe reading.
_PROTECTED_SETTING_KEYS = {
    "auth_enabled",
    "advanced_auth_enabled",
    "local_login_enabled",
    "setup_completed",
}

# The LDAP family, refused for the same reason and by prefix rather than by
# name, so a key added to the schema later is refused by default.
#
# These are not "how the instance behaves" settings — together they name *which
# directory server decides who you are*. auth.py reads them live from this table
# on every login (see the ldap_keys list in _get_ldap_settings), so a restore
# that writes them substitutes the authentication source wholesale:
# ldap_server_url points at another directory, ldap_auto_provision creates a
# local account for whoever it vouches for, and ldap_default_group decides what
# that account gets — Administrators, if the backup says so.
#
# The companion rule does NOT cover this, which is the trap. ldap_enabled is
# paired with ldap_bind_password there, but an *anonymous* bind is a working
# config, so a backup that simply omits the password skips the refusal at the
# _COMPANION_EXPOSURE_TOGGLES check and the toggle is written. Omitting a
# credential is exactly what an attacker authoring this file would do — they own
# the directory being pointed at, so they need no bind credential from us.
_PROTECTED_SETTING_PREFIXES = ("ldap_",)

# Nozzle diameters the backup collector iterates. A path outside this set means
# the backup was written by a newer version, so accept it rather than dropping
# data, but keep the list for validation messages.
_KNOWN_NOZZLES = {"0.2", "0.4", "0.6", "0.8"}


def _parse_dt(value) -> datetime | None:
    """Best-effort parse of a datetime the backup wrote via ``str(...)``.

    Normalised to naive UTC, because that is what every ``DateTime`` column
    here holds: the models write ``datetime.now(timezone.utc)`` into naive
    columns and both dialects drop the offset on the way in. Carrying an aware
    value through would store the wrong wall clock, and comparing one against a
    value read back out of a naive column raises ``TypeError``. The collector
    only ever writes naive strings, so this is a guard on hand-edited or
    foreign backups rather than a path Bambuddy takes itself.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _created_at_matches(row, created_at: datetime | None) -> bool:
    """Does ``row.created_at`` equal a timestamp read out of a backup?

    Compared in Python, not in SQL, and that is the whole point. Every
    ``created_at`` these callers dedupe on is ``server_default=func.now()``, so
    SQLite fills it from ``CURRENT_TIMESTAMP``, which has second precision and
    stores ``'2026-08-02 11:28:41'``. SQLAlchemy binds a Python datetime as
    ``'2026-08-02 11:28:41.000000'``, and SQLite compares the two as strings —
    so ``Model.created_at == created_at`` never matches a row the application
    itself created, not even when handed that row's own value straight back.
    Every dedupe keyed on it misses, and the restore inserts a duplicate of
    everything instead of recognising what is already there.

    Reading the candidates back and comparing the parsed datetimes sidesteps
    the bind format entirely, and is equally correct on PostgreSQL (where the
    column keeps microseconds and the SQL comparison happened to work).
    """
    return created_at is not None and row.created_at == created_at


def _is_blocked_setting_key(key: str) -> bool:
    lowered = key.lower()
    return key in _SENSITIVE_SETTING_KEYS or any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _is_protected_setting_key(key: str) -> bool:
    # Lowered for the prefix test for the same reason _is_blocked_setting_key
    # lowers: the key comes from the backup's JSON, not from our own writer, so
    # its casing is whatever the file says. An exact-match name stays exact —
    # those four are ours and are only ever written lowercase.
    return key in _PROTECTED_SETTING_KEYS or key.lower().startswith(_PROTECTED_SETTING_PREFIXES)


# There used to be an ``_is_skipped_setting_key`` here, the union of the two
# predicates above, shared by the preview and the restore so neither could drift
# from the other. It is gone because a name is no longer enough to decide: the
# third refusal below depends on the payload's *other* values and on local
# database state. ``_plan_settings`` is the shared classifier now, and it covers
# all three reasons.


# Toggles whose *safety* depends on a companion credential that the blocklist
# above refuses to restore. Writing the toggle alone is not a partial restore,
# it is a downgrade:
#
#   * prometheus_enabled with no token opens /api/v1/metrics. The route is on
#     PUBLIC_API_ROUTES and its own gate is ``if token:`` (api/routes/metrics.py),
#     so an empty or absent token means no authentication at all — a full,
#     unauthenticated dump of the instance to anyone who can reach the port. On
#     an instance that never enabled Prometheus there is no token row, so
#     overwrite-off alone is enough to do it.
#   * the other four switch an integration on with no way to authenticate to it,
#     which breaks the login path (LDAP) or the connection (MQTT, HA).
#
# virtual_printer_enabled is largely vestigial post-migration — core/database.py
# copies the rows into the virtual_printers table — but it is the same shape, and
# refusing a vestigial toggle is a harmless no-op.
#
# ldap_enabled is deliberately NOT here. It was, paired with
# ldap_bind_password — but this rule judges availability ("will the integration
# work?"), and that is the wrong question for an authentication source. An
# anonymous bind is a working config, so the pair let a backup omit the password
# and have the toggle written; the whole LDAP family is refused by prefix above
# instead. _is_protected_setting_key runs first in _plan_settings, so leaving the
# entry here would be dead code that reads like coverage.
_COMPANION_CREDENTIALS = {
    "prometheus_enabled": "prometheus_token",
    "mqtt_enabled": "mqtt_password",
    "ha_enabled": "ha_token",
    "virtual_printer_enabled": "virtual_printer_access_code",
}

# Companion credentials a reader takes from the environment rather than from a
# Settings row. ha_token is the only one: get_homeassistant_settings prefers
# HA_TOKEN over the row, and auto-enables ha_enabled when HA_URL and HA_TOKEN are
# both set, so an env-configured instance has a usable credential and no row.
_COMPANION_CREDENTIAL_ENV = {"ha_token": "HA_TOKEN"}

# The pairs above divide into two classes, because "did the *backup* carry a
# usable credential?" does not mean the same thing for both.
#
# For the availability pairs it is the condition that stops the rule
# over-refusing. An anonymous MQTT broker and an anonymous LDAP bind are working
# configs, so a backup with an empty credential is describing something that
# works, and refusing its toggle would be a false positive. Those pairs only
# matter when the restore would produce a config weaker than *both* the backup
# and the local instance.
#
# For the exposure pair it does not transfer. An empty prometheus_token removes
# /api/v1/metrics' only gate (the route is on PUBLIC_API_ROUTES and its own
# check is ``if token:``), so the exposure is a property of the toggle itself,
# not of a downgrade relative to the backup: a backup taken on an instance that
# enabled Prometheus *without* a token — the field is optional and defaults to
# "" — is the more likely source of one, not the less. So an exposure toggle
# skips this condition and is judged on local state alone.
_COMPANION_EXPOSURE_TOGGLES = frozenset({"prometheus_enabled"})


def _setting_value_is_true(value: object) -> bool:
    """True if a settings *payload* value would be stored as "on".

    Deliberately as narrow as ``api.routes.settings.setting_is_true``: a restore
    writes ``str(value)`` verbatim and no reader in the codebase treats "1",
    "on" or "yes" as on, so restoring one of those cannot switch anything on.
    Bool-tolerant because a backup's JSON can carry a real boolean.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() == "true"


def _is_usable_credential(value: object) -> bool:
    """True if a credential value is present and not blank.

    A present-but-*blank* ``prometheus_token`` row counts as unusable, because an
    empty token is exactly the ``if token:`` hole the companion rule exists to
    stop a restore from opening.
    """
    return value is not None and bool(str(value).strip())


@dataclass(frozen=True)
class _SettingsPlan:
    """Which keys of a settings payload will not be written, and why.

    Built once, before anything is added to the session, and shared by the
    preview and the restore so the two cannot disagree about what a commit will
    change. The companion bucket is why this needs a session at all: unlike the
    two name-based buckets it depends on local database state.

    The three buckets are disjoint — a key is classified once, in order.
    """

    blocked: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    companion: tuple[str, ...] = ()

    @property
    def refused(self) -> frozenset[str]:
        return frozenset(self.blocked) | frozenset(self.protected) | frozenset(self.companion)

    @property
    def refused_count(self) -> int:
        return len(self.blocked) + len(self.protected) + len(self.companion)


@dataclass(frozen=True)
class _Detail:
    """A preview caveat, as a translation code plus its English rendering.

    Same contract as a note: the client translates ``code`` with ``params`` and
    falls back to ``message``.
    """

    code: str
    message: str
    params: dict[str, str | int] = dataclasses_field(default_factory=dict)


class _CategoryTally:
    """Mutable accumulator matching ``GitHubRestoreCategoryResult``."""

    def __init__(self) -> None:
        self.restored = 0
        self.skipped = 0
        self.failed = 0
        self.notes: list[dict] = []

    def note(self, code: str, message: str, **params) -> None:
        """Record a note as a translation code, its params and an English fallback.

        Deduped on ``(code, params)`` rather than on the rendered text, which is
        the same thing today but keeps two notes that differ only in a printer
        name from collapsing into one. Bounded for the reason it always was: the
        UI renders every note, so a large backup must not emit one per row.
        """
        if any(existing["code"] == code and existing["params"] == params for existing in self.notes):
            return
        if len(self.notes) >= 20:
            return
        self.notes.append({"code": code, "params": params, "message": message})

    def as_dict(self) -> dict:
        return {"restored": self.restored, "skipped": self.skipped, "failed": self.failed, "notes": self.notes}


class GitHubRestoreService:
    """Reads a backup repository and applies selected categories locally."""

    def __init__(self) -> None:
        self._running_restore: bool = False
        self._progress: str | None = None
        self._http_client: httpx.AsyncClient | None = None
        # Guards the check-then-set on ``_running_restore``. Without it two
        # concurrent POSTs can both observe False before either sets it.
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    @property
    def is_running(self) -> bool:
        return self._running_restore

    @property
    def progress(self) -> str | None:
        return self._progress

    # --- Repository reads --------------------------------------------------

    async def list_commits(self, config: GitHubBackupConfig, limit: int = 20) -> dict:
        """List recent commits on the configured branch."""
        backend = get_provider_backend(config.provider)
        client = await self._get_client()
        result = await backend.list_commits(
            repo_url=config.repository_url,
            token=config.access_token,
            branch=config.branch,
            client=client,
            limit=limit,
        )
        result["branch"] = config.branch
        return result

    async def _resolve_ref(self, config: GitHubBackupConfig, ref: str) -> tuple[str | None, str, dict | None]:
        """Turn ``HEAD`` into a concrete commit SHA.

        Done once up front so a preview and the restore that follows it act on
        the same commit even if a scheduled backup lands in between.

        The third element is the commit entry, when resolving already fetched
        one. ``preview`` displays it, and taking it from here means the ``HEAD``
        case — by far the common one — costs one ``list_commits`` call rather
        than two.
        """
        if ref and ref.upper() != "HEAD":
            return ref, "", None
        result = await self.list_commits(config, limit=1)
        if not result.get("success"):
            return None, result.get("message") or "Could not read the backup repository", None
        commits = result.get("commits") or []
        if not commits:
            return None, f"Branch '{config.branch}' has no commits to restore from", None
        return commits[0]["sha"], "", commits[0]

    async def _describe_commit(self, config: GitHubBackupConfig, resolved: str) -> dict | None:
        """Find the display metadata for one commit SHA.

        Two things used to leave ``commit: null`` in a preview, and the second is
        the one that bit in practice:

        * the commit is older than the 20 the picker lists, so it is not in the
          scan at all — that is what ``get_commit`` is for;
        * ``REF_PATTERN`` accepts a 7-character ref while providers return the
          full 40, so an exact ``==`` never matched an abbreviated SHA *even when
          the commit was in the window*. Hence the prefix comparison.

        Best-effort throughout: this is a subject line and a date, so a failure
        returns None and the preview renders without them rather than failing.
        """
        commits = (await self.list_commits(config, limit=20)).get("commits") or []
        for entry in commits:
            sha = entry.get("sha") or ""
            if sha == resolved or sha.startswith(resolved) or resolved.startswith(sha):
                return entry

        backend = get_provider_backend(config.provider)
        client = await self._get_client()
        result = await backend.get_commit(
            repo_url=config.repository_url, token=config.access_token, ref=resolved, client=client
        )
        return result.get("commit") if result.get("success") else None

    def _category_paths(self, category: RestoreCategory, available: list[str]) -> list[str]:
        """Return the paths in ``available`` that belong to ``category``."""
        if category == RestoreCategory.SETTINGS:
            return [p for p in (SETTINGS_PATH,) if p in available]
        if category == RestoreCategory.SPOOLS:
            return [p for p in (SPOOLS_PATH, SPOOL_USAGE_PATH) if p in available]
        if category == RestoreCategory.ARCHIVES:
            return [p for p in (ARCHIVES_PATH,) if p in available]
        if category == RestoreCategory.KPROFILES:
            return sorted(p for p in available if _KPROFILE_PATH_RE.match(p))
        return []

    @staticmethod
    def _parse_json_files(raw: dict[str, str]) -> tuple[dict[str, object], list[str]]:
        """Parse each fetched file, collecting paths that failed to parse."""
        parsed: dict[str, object] = {}
        bad: list[str] = []
        for path, text in raw.items():
            try:
                parsed[path] = json.loads(text)
            except (ValueError, TypeError):
                bad.append(path)
        return parsed, bad

    @staticmethod
    async def _plan_settings(db: AsyncSession, values: dict) -> _SettingsPlan:
        """Classify every key of a settings payload into its refusal bucket.

        Keys with an unusable name land in no bucket: they are the restore's
        ``failed``, not a refusal, and the preview counts them because the run
        will still report on them.

        Reads local state, so it must run before anything is added to the
        session — otherwise "does this instance already have a credential" would
        see the restore's own writes.
        """
        blocked: list[str] = []
        protected: list[str] = []
        # Toggle -> credential for the pairs that survived the payload-only
        # conditions and still need local state to judge.
        candidates: dict[str, str] = {}

        for key, value in values.items():
            if not isinstance(key, str) or not key:
                continue
            if _is_blocked_setting_key(key):
                blocked.append(key)
                continue
            if _is_protected_setting_key(key):
                protected.append(key)
                continue

            credential = _COMPANION_CREDENTIALS.get(key)
            if credential is None:
                continue
            # Turning something *off* is always safe to write.
            if not _setting_value_is_true(value):
                continue
            # Expressed as the predicate rather than assumed, so the map cannot
            # go quietly inert if _SECRET_KEY_HINTS is ever edited: a credential
            # the restore is willing to write travels with its toggle.
            if not _is_blocked_setting_key(credential):
                continue
            # The backup itself carried no credential here. For an availability
            # pair that describes a working config — an anonymous MQTT broker and
            # an anonymous LDAP bind both are (mqtt_relay.py and ldap_service.py
            # pass empty credentials straight through) — so refusing the toggle
            # would be a false positive. For an exposure pair a blank credential
            # is the hole itself, so the condition is skipped and only local
            # state decides. See _COMPANION_EXPOSURE_TOGGLES.
            if key not in _COMPANION_EXPOSURE_TOGGLES and not _is_usable_credential(values.get(credential)):
                continue
            candidates[key] = credential

        if not candidates:
            return _SettingsPlan(blocked=tuple(blocked), protected=tuple(protected))

        # One SELECT covering both halves of every candidate pair.
        wanted = set(candidates) | set(candidates.values())
        rows = await db.execute(select(Settings).where(Settings.key.in_(wanted)))
        local = {row.key: row.value for row in rows.scalars().all()}

        companion: list[str] = []
        for toggle, credential in candidates.items():
            if _is_usable_credential(local.get(credential)):
                continue
            env_name = _COMPANION_CREDENTIAL_ENV.get(credential)
            if env_name and _is_usable_credential(os.environ.get(env_name)):
                continue
            # Already on locally with no credential: the exposure pre-dates this
            # restore, so refusing changes nothing and "left switched off" would
            # be a lie.
            if _setting_value_is_true(local.get(toggle)):
                continue
            companion.append(toggle)

        return _SettingsPlan(
            blocked=tuple(blocked),
            protected=tuple(protected),
            companion=tuple(companion),
        )

    async def preview(self, db: AsyncSession, config: GitHubBackupConfig, ref: str = "HEAD") -> dict:
        """Report which categories a commit contains, and how much is in each.

        Takes a session because the settings count depends on local state — see
        ``_plan_settings``. ``ref`` stays keyword-friendly for callers.
        """
        resolved, error, commit_info = await self._resolve_ref(config, ref)
        if resolved is None:
            return {"success": False, "message": error, "ref": ref, "categories": []}

        backend = get_provider_backend(config.provider)
        client = await self._get_client()

        tree = await backend.list_tree(
            repo_url=config.repository_url, token=config.access_token, ref=resolved, client=client
        )
        if not tree.get("success"):
            return {"success": False, "message": tree.get("message") or "Could not list the commit", "ref": resolved}
        available: list[str] = tree.get("paths") or []

        # One batched read covers metadata plus every category payload.
        wanted = [METADATA_PATH] if METADATA_PATH in available else []
        for category in RestoreCategory:
            wanted.extend(self._category_paths(category, available))

        fetched = await backend.fetch_files(
            repo_url=config.repository_url,
            token=config.access_token,
            ref=resolved,
            paths=wanted,
            client=client,
            # The listing above already built this map; without it the GitHub
            # family would GET the same recursive tree a second time.
            blob_shas=tree.get("blob_shas") or None,
        )
        if not fetched.get("success"):
            return {
                "success": False,
                "message": fetched.get("message") or "Could not read the commit contents",
                "ref": resolved,
            }
        parsed, bad_paths = self._parse_json_files(fetched.get("files") or {})

        metadata = parsed.get(METADATA_PATH)
        metadata_version = metadata.get("version") if isinstance(metadata, dict) else None

        categories = []
        for category in RestoreCategory:
            paths = self._category_paths(category, available)
            if not paths:
                categories.append(
                    self._category_entry(category, False, 0, _Detail("notPresent", "Not present in this backup commit"))
                )
                continue
            unreadable = [p for p in paths if p in bad_paths]
            if unreadable:
                joined = ", ".join(unreadable)
                categories.append(
                    self._category_entry(
                        category,
                        False,
                        0,
                        _Detail("unreadableJson", f"Unreadable JSON: {joined}", {"paths": joined}),
                    )
                )
                continue
            count, detail = await self._count_items(db, category, parsed)
            categories.append(self._category_entry(category, True, count, detail))

        if commit_info is None:
            commit_info = await self._describe_commit(config, resolved)

        return {
            "success": True,
            "message": "OK",
            "ref": resolved,
            "commit": commit_info,
            "metadata_version": metadata_version,
            "categories": categories,
        }

    @staticmethod
    def _category_entry(category: RestoreCategory, available: bool, item_count: int, detail: _Detail | None) -> dict:
        """Shape one ``GitHubRestorePreviewCategory``, translated detail included."""
        return {
            "category": category,
            "available": available,
            "item_count": item_count,
            "detail": detail.message if detail else None,
            "detail_code": detail.code if detail else None,
            "detail_params": detail.params if detail else {},
        }

    async def _count_items(
        self, db: AsyncSession, category: RestoreCategory, parsed: dict
    ) -> tuple[int, _Detail | None]:
        """Count restorable items for ``category`` and describe any caveat."""
        if category == RestoreCategory.SETTINGS:
            payload = parsed.get(SETTINGS_PATH)
            values = payload.get("settings") if isinstance(payload, dict) else None
            if not isinstance(values, dict):
                return 0, _Detail("settingsNoPayload", "No settings in payload")
            # Every refusal is subtracted so the count matches what the restore
            # actually writes. The wording calls out the credential ones (what a
            # user might expect to come back) and the companion ones (a
            # behaviour change worth explaining before it happens); the auth
            # policy keys stay unmentioned on purpose.
            plan = await self._plan_settings(db, values)
            detail = None
            if plan.companion and not plan.blocked:
                # An exposure toggle becomes a candidate whether or not the
                # backup carried its credential, so this commit can refuse a
                # switch without having a single credential-like key to skip —
                # "0 credential-like key(s) will be skipped" would read as noise.
                detail = _Detail(
                    "settingsCompanionOnlyWillSkip",
                    f"{len(plan.companion)} switch(es) will be left off — the credential each one needs "
                    "cannot be restored from a backup",
                    {"companion": len(plan.companion)},
                )
            elif plan.companion:
                detail = _Detail(
                    "settingsCompanionWillSkip",
                    f"{len(plan.blocked)} credential-like key(s) will be skipped, and "
                    f"{len(plan.companion)} switch(es) that depend on them will be left off",
                    {"count": len(plan.blocked), "companion": len(plan.companion)},
                )
            elif plan.blocked:
                detail = _Detail(
                    "settingsCredentialsWillSkip",
                    f"{len(plan.blocked)} credential-like keys will be skipped",
                    {"count": len(plan.blocked)},
                )
            return len(values) - plan.refused_count, detail

        if category == RestoreCategory.SPOOLS:
            payload = parsed.get(SPOOLS_PATH)
            spools = payload.get("spools") if isinstance(payload, dict) else None
            usage_payload = parsed.get(SPOOL_USAGE_PATH)
            usage = usage_payload.get("usage_history") if isinstance(usage_payload, dict) else None
            # Usage records are counted here, not just described in the detail:
            # _restore_spool_usage increments this category's tally, so counting
            # only the spools broke restored + skipped + failed == item_count —
            # the invariant the settings count is careful to hold. The detail
            # breaks the total down rather than adding to it.
            count = len(spools) if isinstance(spools, list) else 0
            detail = None
            if isinstance(usage, list) and usage:
                count += len(usage)
                detail = _Detail("spoolsUsageCount", f"including {len(usage)} usage records", {"count": len(usage)})
            return count, detail

        if category == RestoreCategory.ARCHIVES:
            payload = parsed.get(ARCHIVES_PATH)
            archives = payload.get("archives") if isinstance(payload, dict) else None
            count = len(archives) if isinstance(archives, list) else 0
            return count, _Detail(
                "archivesMetadataOnly", "Metadata only — 3MF files and thumbnails are not in a Git backup"
            )

        if category == RestoreCategory.KPROFILES:
            total = 0
            serials = set()
            for path, payload in parsed.items():
                match = _KPROFILE_PATH_RE.match(path)
                if not match or not isinstance(payload, dict):
                    continue
                serials.add(match.group(1))
                profiles = payload.get("profiles")
                if isinstance(profiles, list):
                    total += len(profiles)
            detail = None
            if serials:
                detail = _Detail("kprofilesPrinterCount", f"across {len(serials)} printer(s)", {"count": len(serials)})
            return total, detail

        return 0, None

    # --- Restore -----------------------------------------------------------

    async def run_restore(
        self,
        config_id: int,
        ref: str,
        categories: list[RestoreCategory],
        overwrite_existing: bool = False,
    ) -> dict:
        """Apply selected categories from one backup commit."""
        # Import locally to avoid a module-level cycle: the backup service takes
        # the mirror-image lock against us.
        from backend.app.services.github_backup import github_backup_service

        # The lock serialises two concurrent restores; the backup side has no
        # lock of its own, and relies on this region staying await-free after the
        # acquisition. Both flags are plain bools on one event loop, so with no
        # suspension point between the two reads and the write, the loop cannot
        # slip github_backup.run_backup's mirror-image check in between. Adding an
        # `await` below the acquisition and above `self._running_restore = True`
        # would let a backup and a restore run at once.
        async with self._lock:
            if self._running_restore:
                return {"success": False, "message": "A restore is already running", "results": {}}
            if github_backup_service.is_running:
                return {
                    "success": False,
                    "message": "A backup is currently running. Wait for it to finish before restoring.",
                    "results": {},
                }
            self._running_restore = True

        log_id = None
        try:
            async with async_session() as db:
                result = await db.execute(select(GitHubBackupConfig).where(GitHubBackupConfig.id == config_id))
                config = result.scalar_one_or_none()
                if not config:
                    return {"success": False, "message": "Configuration not found", "results": {}}

                self._progress = "Resolving commit..."
                resolved, error, _ = await self._resolve_ref(config, ref)
                if resolved is None:
                    return {"success": False, "message": error, "results": {}}

                log = GitHubBackupLog(config_id=config_id, status="running", trigger="restore", commit_sha=resolved)
                db.add(log)
                await db.commit()
                await db.refresh(log)
                log_id = log.id

                # Owned here rather than by _apply so the failure path can see
                # the categories that were already committed when the raise
                # happened. _apply records a tally only after its category's
                # commit, so every entry present is on disk.
                results: dict[str, _CategoryTally] = {}
                try:
                    payload, error = await self._read_categories(config, resolved, categories)
                    if error:
                        raise RuntimeError(error)

                    settings_keys_written: set[str] = set()
                    await self._apply(
                        db, payload, categories, overwrite_existing, settings_keys_written, results=results
                    )
                    await db.commit()

                    # After the commit: this reconnects the relay, which is not
                    # something to do on values that could still roll back.
                    settings_tally = results.get(RestoreCategory.SETTINGS.value)
                    if settings_tally is not None:
                        self._progress = "Reconnecting the MQTT relay..."
                        await self._reconfigure_mqtt_relay(db, settings_keys_written, settings_tally)

                    total_restored = sum(tally.restored for tally in results.values())
                    any_failed = any(tally.failed for tally in results.values())

                    log.status = "failed" if any_failed and total_restored == 0 else "success"
                    log.completed_at = datetime.now(timezone.utc)
                    log.files_changed = total_restored
                    if any_failed:
                        log.error_message = "Some items could not be restored — see the restore result for detail"
                    await db.commit()

                    return {
                        "success": True,
                        "message": f"Restored {total_restored} item(s) from {resolved[:7]}",
                        "log_id": log_id,
                        "ref": resolved,
                        "results": {name: tally.as_dict() for name, tally in results.items()},
                    }

                except Exception as e:
                    # Rolls back the category that was mid-flight. Every category
                    # already in ``results`` committed as it finished (see
                    # _apply), so those rows survive this — and reporting an
                    # empty result over them would tell the user nothing was
                    # restored while their archives and spools are on disk.
                    logger.exception("Restore failed for config %s ref %s", config_id, resolved)
                    await db.rollback()
                    committed = sum(tally.restored for tally in results.values())
                    log.status = "failed"
                    log.completed_at = datetime.now(timezone.utc)
                    log.files_changed = committed
                    log.error_message = str(e)[:1000]
                    await db.commit()
                    return {
                        "success": False,
                        "message": str(e),
                        "log_id": log_id,
                        "ref": resolved,
                        "results": {name: tally.as_dict() for name, tally in results.items()},
                    }

        finally:
            self._running_restore = False
            self._progress = None

    async def _read_categories(
        self, config: GitHubBackupConfig, ref: str, categories: list[RestoreCategory]
    ) -> tuple[dict, str]:
        """Fetch and parse just the files the requested categories need."""
        backend = get_provider_backend(config.provider)
        client = await self._get_client()

        self._progress = "Listing backup contents..."
        tree = await backend.list_tree(
            repo_url=config.repository_url, token=config.access_token, ref=ref, client=client
        )
        if not tree.get("success"):
            return {}, tree.get("message") or "Could not list the commit"
        available: list[str] = tree.get("paths") or []

        wanted: list[str] = []
        for category in categories:
            wanted.extend(self._category_paths(category, available))
        if not wanted:
            return {}, "None of the selected categories are present in that commit"

        self._progress = "Downloading backup files..."
        fetched = await backend.fetch_files(
            repo_url=config.repository_url,
            token=config.access_token,
            ref=ref,
            paths=wanted,
            client=client,
            blob_shas=tree.get("blob_shas") or None,
        )
        if not fetched.get("success"):
            return {}, fetched.get("message") or "Could not read the commit contents"

        parsed, bad = self._parse_json_files(fetched.get("files") or {})
        if bad:
            return {}, f"Backup contains unreadable JSON: {', '.join(sorted(bad))}"
        return parsed, ""

    async def _apply(
        self,
        db: AsyncSession,
        payload: dict,
        categories: list[RestoreCategory],
        overwrite: bool,
        settings_keys_written: set[str] | None = None,
        results: dict[str, _CategoryTally] | None = None,
    ) -> dict[str, _CategoryTally]:
        """Apply categories in dependency order and return per-category tallies.

        ``settings_keys_written``, if given, collects the setting keys actually
        written, for the caller's post-commit side effects (see
        ``_reconfigure_mqtt_relay``).

        ``results``, if given, is the caller's own dict rather than a fresh one.
        Each category is committed before it is recorded there, so on a raise
        the caller can report exactly what is already on disk — see the
        per-category commit below.
        """
        results = {} if results is None else results
        archive_id_map: dict[int, int] = {}

        # Every database category commits before the next one starts, and only
        # then is its tally recorded. Two reasons:
        #
        #  * SQLite has one writer. Each category is a long run of one SELECT per
        #    row or per key — _find_archive, _find_spool, the usage dedupe,
        #    _restore_settings — interleaved with autoflushed INSERTs, all inside
        #    the open write transaction. A few thousand archives plus a full
        #    usage history plausibly passes the 15 s busy_timeout
        #    (core/database.py), at which point every concurrent writer in the
        #    app fails with "database is locked". This is the same hold the
        #    K-profile phase had, arriving by volume rather than by awaiting a
        #    sulking printer.
        #  * The ordering tolerates it: the only cross-category state is
        #    archive_id_map and spool_id_map, both plain dicts in memory, and
        #    the session is expire_on_commit=False so nothing reloads.
        #
        # The cost is that a later failure no longer rolls back an earlier
        # category — which is why the tally is recorded after the commit, so
        # run_restore's failure path reports the rows that really landed instead
        # of claiming nothing was restored.

        # Archives first: spool usage history references archive_id.
        if RestoreCategory.ARCHIVES in categories:
            self._progress = "Restoring print archives..."
            tally = _CategoryTally()
            await self._restore_archives(db, payload.get(ARCHIVES_PATH), overwrite, tally, archive_id_map)
            await db.commit()
            results[RestoreCategory.ARCHIVES.value] = tally

        if RestoreCategory.SPOOLS in categories:
            self._progress = "Restoring spool inventory..."
            tally = _CategoryTally()
            await self._restore_spools(
                db,
                payload.get(SPOOLS_PATH),
                payload.get(SPOOL_USAGE_PATH),
                overwrite,
                tally,
                archive_id_map,
            )
            await db.commit()
            results[RestoreCategory.SPOOLS.value] = tally

        if RestoreCategory.SETTINGS in categories:
            self._progress = "Restoring app settings..."
            tally = _CategoryTally()
            await self._restore_settings(
                db, payload.get(SETTINGS_PATH), overwrite, tally, keys_written=settings_keys_written
            )
            await db.commit()
            results[RestoreCategory.SETTINGS.value] = tally

        # Last, because it leaves the database and publishes over MQTT.
        if RestoreCategory.KPROFILES in categories:
            # The database categories are already committed by the loop above,
            # and that is load-bearing here rather than tidiness:
            # _restore_kprofiles awaits get_kprofiles per printer per nozzle,
            # which is timeout=5.0 * max_retries=3, i.e. up to ~15 s each against
            # an unresponsive printer. Holding SQLite's writer across that would
            # pass the 15 s busy_timeout on a farm with a couple of sulking
            # printers.
            #
            # The cost is that a K-profile failure no longer rolls back the
            # categories that already succeeded. That is the correct trade
            # anyway: extrusion_cali_set has left for the printer by then and
            # cannot be rolled back either, so a rollback would only have made
            # the database disagree with the hardware.

            self._progress = "Sending K-profiles to printers..."
            tally = _CategoryTally()
            try:
                await self._restore_kprofiles(db, payload, tally)
            except Exception as e:
                # Everything above is committed and cannot be un-committed, so
                # letting this reach run_restore's handler would report
                # "nothing was restored" over durable archive, spool and
                # settings rows — and skip the post-commit MQTT reconfigure,
                # leaving the relay on the pre-restore broker. The K-profile
                # phase is the last thing that runs, so containing it here is
                # what keeps the result honest about what actually landed.
                logger.exception("The K-profile step failed after the database categories were committed")
                # Discards the phase's own read transaction. The rows above went
                # in at the commit two statements up; this only stops a session
                # left in a failed state by a database error from turning the
                # caller's commit into that same false report.
                await db.rollback()
                outstanding = self._kprofile_profile_count(
                    content for path, content in payload.items() if _KPROFILE_PATH_RE.match(path)
                )
                outstanding -= tally.restored + tally.skipped + tally.failed
                tally.failed += max(outstanding, 0)
                tally.note(
                    "kprofilesStepFailed",
                    f"The K-profile step could not be completed: {e}",
                    reason=str(e)[:200],
                )
            results[RestoreCategory.KPROFILES.value] = tally

        return results

    # --- Per-category appliers --------------------------------------------

    async def _restore_archives(
        self,
        db: AsyncSession,
        payload,
        overwrite: bool,
        tally: _CategoryTally,
        id_map: dict[int, int],
    ) -> None:
        archives = payload.get("archives") if isinstance(payload, dict) else None
        if not isinstance(archives, list):
            tally.note("noData", "No data of this kind in this backup")
            return

        valid_printers = set((await db.execute(select(Printer.id))).scalars().all())
        valid_projects = set((await db.execute(select(Project.id))).scalars().all())
        # Ownership decides visibility, not just attribution: an archive with a
        # NULL created_by_id is a 404 to every caller without archives:read_all
        # (_ensure_archive_visible fails closed on it) and never appears in the
        # ownership-scoped list queries. Hoisted like the two above.
        #
        # username is the natural key and wins, per the module's rule at the top
        # of the file; created_by_id is the fallback for a pre-#2656 commit that
        # carries no username. That ordering is what makes restoring onto a
        # rebuilt instance safe: the users table renumbers there, so a live id
        # can land on a different person, and the id path alone cannot tell that
        # from a correct match. Resolving on the name instead means the one case
        # it cannot resolve — a user renamed since the backup — falls through to
        # ownerless-with-a-note below rather than misattributing in silence.
        users = (await db.execute(select(User.id, User.username))).all()
        valid_users = {user_id for user_id, _ in users}
        users_by_name = {username: user_id for user_id, username in users}

        # Only metadata is backed up, never the 3MF/thumbnail bytes, and
        # print_archives.file_path is NOT NULL — so inserted rows get an empty
        # path and are history-only. Say so once rather than per row.
        warned_files = False

        for entry in archives:
            if not isinstance(entry, dict):
                tally.failed += 1
                continue

            old_id = entry.get("id") if isinstance(entry.get("id"), int) else None
            started_at = _parse_dt(entry.get("started_at"))
            existing = await self._find_archive(db, entry, started_at)

            fields = {
                "print_name": entry.get("print_name"),
                "print_time_seconds": entry.get("print_time_seconds"),
                "filament_used_grams": entry.get("filament_used_grams"),
                "filament_type": entry.get("filament_type"),
                "filament_color": entry.get("filament_color"),
                "layer_height": entry.get("layer_height"),
                "total_layers": entry.get("total_layers"),
                "nozzle_diameter": entry.get("nozzle_diameter"),
                "bed_temperature": entry.get("bed_temperature"),
                "nozzle_temperature": entry.get("nozzle_temperature"),
                "sliced_for_model": entry.get("sliced_for_model"),
                "status": entry.get("status") or "completed",
                "started_at": started_at,
                "completed_at": _parse_dt(entry.get("completed_at")),
                "makerworld_url": entry.get("makerworld_url"),
                "designer": entry.get("designer"),
                "external_url": entry.get("external_url"),
                "is_favorite": bool(entry.get("is_favorite")),
                "tags": entry.get("tags"),
                "notes": entry.get("notes"),
                "cost": entry.get("cost"),
                "failure_reason": entry.get("failure_reason"),
                "quantity": entry.get("quantity") or 1,
                "energy_kwh": entry.get("energy_kwh"),
                "energy_cost": entry.get("energy_cost"),
            }

            printer_id = entry.get("printer_id")
            if printer_id is not None and printer_id not in valid_printers:
                tally.note(
                    "archivesPrinterMissing", "Some archives referenced printers that no longer exist — link cleared"
                )
                printer_id = None
            project_id = entry.get("project_id")
            if project_id is not None and project_id not in valid_projects:
                tally.note(
                    "archivesProjectMissing", "Some archives referenced projects that no longer exist — link cleared"
                )
                project_id = None
            fields["printer_id"] = printer_id
            fields["project_id"] = project_id

            # The ownership pair and deleted_at are the late arrivals — a backup
            # commit taken before the collector wrote them carries neither key.
            # Absent is NOT the same as null here, because the overwrite branch
            # below is a blanket setattr: treating a missing key as None would
            # write NULL over a live owner (_ensure_archive_visible then 404s the
            # archive for the very user who owns it — the failure carrying the
            # column was added to fix) and silently un-delete a row the user
            # deleted. So only carry a column the backup actually knows about;
            # on insert, an absent key just takes the model default.
            # An owner the backup names but this instance cannot resolve is the
            # same epistemic state as an absent key — we do not know who owns
            # this archive — so it takes the same action: the column is left out
            # of ``fields`` entirely rather than set to None. Writing NULL there
            # would take the owner away from a local archive that has a perfectly
            # good one, which is the 404-for-its-own-owner failure this column is
            # carried across to fix, and it would do it on the overwrite path
            # where there is a local answer to keep. On insert there is nothing
            # to keep, so the row takes the model default and lands ownerless,
            # which is what the note says.
            owner_cleared = False
            backup_username = entry.get("created_by_username")
            if isinstance(backup_username, str) and backup_username:
                # The natural-key path. A miss here is a user renamed or deleted
                # since the backup, and there is nothing else to resolve on: the
                # id alongside it is from the source instance's numbering, so
                # trusting it is exactly the misattribution the name is here to
                # prevent. Not a reason to fail the row — the archive is still
                # worth having, and an admin can reassign it — but said out loud
                # on insert, because an ownerless archive is not silent-safe.
                created_by_id = users_by_name.get(backup_username)
                if created_by_id is None:
                    if existing is None:
                        tally.note(
                            "archivesOwnerUnmatched",
                            "Some archives name an owner this instance does not have — owner cleared rather than "
                            "guessed from the backup's user id, so they are visible only to users with the "
                            "archives:read_all permission until an admin reassigns them",
                        )
                        owner_cleared = True
                else:
                    fields["created_by_id"] = created_by_id
            elif "created_by_id" in entry:
                # Fallback for a commit taken before the collector recorded the
                # username. Validated rather than trusted, so a *stale* id is
                # dropped instead of pointing somewhere wrong; a live id
                # belonging to a different person on a rebuilt instance is the
                # case this path cannot see, and is why the branch above exists.
                # An explicit null is not a miss — the backup is saying the
                # archive had no owner — so it is written, and overwrite keeps
                # meaning "make the local row match the backup".
                created_by_id = entry.get("created_by_id")
                if created_by_id is not None and created_by_id not in valid_users:
                    if existing is None:
                        tally.note(
                            "archivesOwnerCleared",
                            "Some archives referenced users that no longer exist — owner cleared, so they are "
                            "visible only to users with the archives:read_all permission until an admin "
                            "reassigns them",
                        )
                        owner_cleared = True
                else:
                    fields["created_by_id"] = created_by_id
            if "deleted_at" in entry:
                # A soft-deleted archive is still in the backup (its row is kept
                # so stats keep counting it), so carry the flag across or the
                # restore turns something the user deleted back into a visible
                # archive.
                fields["deleted_at"] = _parse_dt(entry.get("deleted_at"))

            if existing is not None:
                if old_id is not None:
                    id_map[old_id] = existing.id
                if not overwrite:
                    tally.skipped += 1
                    continue
                # Overwrite means "make the local row match the backup", which
                # includes un-deleting one the user deleted after the backup was
                # taken. Legitimate, but not obvious from a restored/skipped
                # count, so say it.
                if existing.deleted_at is not None and "deleted_at" in fields and fields["deleted_at"] is None:
                    tally.note(
                        "archivesUndeleted",
                        "Archive(s) deleted since the backup are visible again — overwrite was on",
                    )
                for key, value in fields.items():
                    setattr(existing, key, value)
                tally.restored += 1
                continue

            if not warned_files:
                tally.note(
                    "archivesMetadataOnly",
                    "Restored archives carry metadata only — the 3MF and thumbnail files are not in a Git backup",
                )
                warned_files = True

            # Insert-only, and the mirror of the rule above: an owner the backup
            # cannot tell us is never written, so on overwrite the local one
            # survives — but there is no local row here to fall back on, so the
            # archive lands ownerless, a 404 for everyone without
            # archives:read_all. Three ways to get here: a commit taken before
            # the collector recorded the column (every pre-#2656 backup), an
            # archive that genuinely had no owner on the source instance, or one
            # whose owner this instance cannot resolve. All restore fine and all
            # were silent, so the tally said "N archives restored" while the user
            # who asked for them saw none. The unresolved cases above already
            # said their piece; don't say it twice for the same row.
            if fields.get("created_by_id") is None and not owner_cleared:
                tally.note(
                    "archivesOwnerUnknown",
                    "Some archives were restored without an owner — this backup does not record one, so they "
                    "are visible only to users with the archives:read_all permission until an admin reassigns "
                    "them",
                )

            row = PrintArchive(
                filename=entry.get("filename") or "restored-from-backup",
                file_path="",
                file_size=entry.get("file_size") or 0,
                content_hash=entry.get("content_hash"),
                **fields,
            )
            created_at = _parse_dt(entry.get("created_at"))
            if created_at is not None:
                row.created_at = created_at
            db.add(row)
            await db.flush()
            if old_id is not None:
                id_map[old_id] = row.id
            tally.restored += 1

    async def _find_archive(self, db: AsyncSession, entry: dict, started_at: datetime | None) -> PrintArchive | None:
        """Match a backed-up archive to a local row by natural key.

        ``started_at`` is nullable and genuinely NULL for a whole class of rows —
        the re-slice path in ``library.py`` constructs ``PrintArchive`` without
        one — so it cannot be *required* by the key. It narrows the match instead:
        a backed-up row with no ``started_at`` matches a local row that has none
        either. Requiring it meant those archives never matched, so each restore
        re-inserted them as duplicates and overwrite mode could never update them.

        ``content_hash`` identifies the sliced file on its own, which is why it is
        the branch allowed to run without a ``started_at``; ``filename`` is too
        weak for that (re-slices share it) and still requires one. Two backed-up
        rows sharing a hash *and* having no ``started_at`` are indistinguishable
        in the backup, so they collapse onto one local row — better than
        duplicating both on every restore.

        Soft-deleted rows are matched deliberately: there is no ``deleted_at``
        filter here because the row still exists, and matching it is what stops a
        restore inserting a live duplicate of an archive the user has deleted.
        """
        started_predicate = PrintArchive.started_at == started_at if started_at else PrintArchive.started_at.is_(None)

        content_hash = entry.get("content_hash")
        if content_hash:
            result = await db.execute(
                select(PrintArchive).where(PrintArchive.content_hash == content_hash, started_predicate)
            )
            row = result.scalars().first()
            if row is not None:
                return row

        filename = entry.get("filename")
        if filename and started_at:
            result = await db.execute(select(PrintArchive).where(PrintArchive.filename == filename, started_predicate))
            return result.scalars().first()
        return None

    async def _restore_spools(
        self,
        db: AsyncSession,
        inventory,
        usage_payload,
        overwrite: bool,
        tally: _CategoryTally,
        archive_id_map: dict[int, int],
    ) -> None:
        spools = inventory.get("spools") if isinstance(inventory, dict) else None
        if not isinstance(spools, list):
            tally.note("noData", "No data of this kind in this backup")
            return

        spool_id_map: dict[int, int] = {}
        tags_kept = 0

        for entry in spools:
            if not isinstance(entry, dict):
                tally.failed += 1
                continue

            old_id = entry.get("id") if isinstance(entry.get("id"), int) else None
            existing, matched_on = await self._find_spool(db, entry)

            fields = {
                "material": entry.get("material") or "PLA",
                "subtype": entry.get("subtype"),
                "color_name": entry.get("color_name"),
                "rgba": entry.get("rgba"),
                "brand": entry.get("brand"),
                "label_weight": entry.get("label_weight") or 1000,
                "core_weight": entry.get("core_weight") or 250,
                "weight_used": entry.get("weight_used") or 0,
                "weight_locked": bool(entry.get("weight_locked")),
                "slicer_filament": entry.get("slicer_filament"),
                "slicer_filament_name": entry.get("slicer_filament_name"),
                "nozzle_temp_min": entry.get("nozzle_temp_min"),
                "nozzle_temp_max": entry.get("nozzle_temp_max"),
                "note": entry.get("note"),
                "cost_per_kg": entry.get("cost_per_kg"),
                "tag_uid": entry.get("tag_uid"),
                "tray_uuid": entry.get("tray_uuid"),
                "data_origin": entry.get("data_origin"),
                "tag_type": entry.get("tag_type"),
                "archived_at": _parse_dt(entry.get("archived_at")),
            }

            if existing is not None:
                if old_id is not None:
                    spool_id_map[old_id] = existing.id
                if not overwrite:
                    tally.skipped += 1
                    continue
                tags_kept += await self._guard_tag_overwrite(db, existing, fields, matched_on)
                for key, value in fields.items():
                    setattr(existing, key, value)
                tally.restored += 1
                continue

            row = Spool(**fields)
            # Carry the original created_at across. Without it the row would be
            # stamped "now", and the composite fallback in _find_spool (which
            # keys on created_at) would miss on a second restore and insert a
            # duplicate instead of matching.
            created_at = _parse_dt(entry.get("created_at"))
            if created_at is not None:
                row.created_at = created_at
            db.add(row)
            await db.flush()
            if old_id is not None:
                spool_id_map[old_id] = row.id
            tally.restored += 1

        if tags_kept:
            tally.note(
                "spoolTagKept",
                f"{tags_kept} spool tag(s) left as they are — the backup would have cleared a tag that "
                "has since been scanned, or moved one onto a second spool.",
                count=tags_kept,
            )

        await self._restore_spool_usage(db, usage_payload, tally, spool_id_map, archive_id_map)

    async def _find_spool(self, db: AsyncSession, entry: dict) -> tuple[Spool | None, str | None]:
        """Match a backed-up spool to a local row, and say which key matched.

        Physical identity first (an RFID/Bambu tag is the spool), then a
        descriptive composite including ``created_at`` so two otherwise
        identical spools added at different times stay distinct.

        The second element names the column that matched — ``"tag_uid"``,
        ``"tray_uuid"`` or ``None`` for the composite. ``_guard_tag_overwrite``
        needs it: the matched column holds the incoming value by definition, so
        it is the *other* one that overwrite can corrupt.
        """
        tag_uid = entry.get("tag_uid")
        if tag_uid:
            result = await db.execute(select(Spool).where(Spool.tag_uid == tag_uid))
            row = result.scalars().first()
            if row is not None:
                return row, "tag_uid"

        tray_uuid = entry.get("tray_uuid")
        if tray_uuid:
            result = await db.execute(select(Spool).where(Spool.tray_uuid == tray_uuid))
            row = result.scalars().first()
            if row is not None:
                return row, "tray_uuid"

        created_at = _parse_dt(entry.get("created_at"))
        if created_at is None:
            return None, None
        # created_at is filtered in Python, not here — see _created_at_matches.
        result = await db.execute(
            select(Spool).where(
                Spool.material == (entry.get("material") or "PLA"),
                Spool.brand == entry.get("brand"),
                Spool.subtype == entry.get("subtype"),
                Spool.color_name == entry.get("color_name"),
            )
        )
        for row in result.scalars():
            if _created_at_matches(row, created_at):
                return row, None
        return None, None

    @staticmethod
    async def _guard_tag_overwrite(db: AsyncSession, existing: Spool, fields: dict, matched_on: str | None) -> int:
        """Remove tag columns from ``fields`` that an overwrite would corrupt.

        ``tag_uid`` and ``tray_uuid`` are both in ``fields`` and overwrite is a
        blanket ``setattr`` loop, so a spool matched on one key gets the backup's
        *other* key written onto it. Neither column has a unique constraint
        (``models/spool.py``, and no unique index in the migrations), so nothing
        errors — a duplicate tag simply appears, after which ``_find_spool``'s
        ``.first()`` is non-deterministic and an AMS tag lookup resolves to an
        arbitrary one of the two spools. The same loop can also *clear* a tag the
        user has scanned since the backup was taken, when the backup entry holds
        ``None``.

        Two refusals, and the row is otherwise overwritten as normal:

        * the incoming value is empty and the local row has one — the backup
          predates the scan, so the local tag is the newer fact;
        * the incoming value is already held by a different local spool — writing
          it would create the duplicate described above.

        Returns how many columns were left alone, so the caller can say so in the
        tally rather than doing it silently.
        """
        kept = 0
        for column in ("tag_uid", "tray_uuid"):
            # The column we matched on already holds the incoming value.
            if column == matched_on:
                continue

            incoming = fields.get(column)
            current = getattr(existing, column)
            if incoming == current:
                continue

            if not incoming:
                if current:
                    fields.pop(column)
                    kept += 1
                continue

            clash = await db.execute(
                select(Spool.id).where(getattr(Spool, column) == incoming, Spool.id != existing.id)
            )
            if clash.scalars().first() is not None:
                fields.pop(column)
                kept += 1
        return kept

    async def _restore_spool_usage(
        self,
        db: AsyncSession,
        usage_payload,
        tally: _CategoryTally,
        spool_id_map: dict[int, int],
        archive_id_map: dict[int, int],
    ) -> None:
        usage = usage_payload.get("usage_history") if isinstance(usage_payload, dict) else None
        if not isinstance(usage, list) or not usage:
            return

        valid_printers = set((await db.execute(select(Printer.id))).scalars().all())
        unresolved = 0
        unlinked_archives = 0

        for entry in usage:
            if not isinstance(entry, dict):
                tally.failed += 1
                continue

            old_spool_id = entry.get("spool_id")
            spool_id = spool_id_map.get(old_spool_id) if isinstance(old_spool_id, int) else None
            if spool_id is None:
                # The parent spool never made it into the map: the backup's spool
                # list didn't include it, or its entry carried no integer id. A
                # spool that was merely *skipped* (matched locally, overwrite off)
                # is mapped a few lines up in _restore_spools, so it never lands
                # here — which is why the note below offers no remedy.
                unresolved += 1
                tally.skipped += 1
                continue

            created_at = _parse_dt(entry.get("created_at"))
            # Usage history has no natural key of its own, so dedupe on the
            # tuple that makes a consumption event unique in practice. As in
            # _find_spool, created_at is compared in Python — see
            # _created_at_matches. An entry carrying no created_at at all
            # cannot be recognised and is re-inserted, which is what the
            # IS NULL comparison this replaced did too: the column is
            # non-nullable, so it never matched either.
            existing = await db.execute(
                select(SpoolUsageHistory).where(
                    SpoolUsageHistory.spool_id == spool_id,
                    SpoolUsageHistory.weight_used == (entry.get("weight_used") or 0),
                    SpoolUsageHistory.print_name == entry.get("print_name"),
                )
            )
            if any(_created_at_matches(row, created_at) for row in existing.scalars()):
                tally.skipped += 1
                continue

            printer_id = entry.get("printer_id")
            if printer_id is not None and printer_id not in valid_printers:
                printer_id = None

            old_archive_id = entry.get("archive_id")
            archive_id = archive_id_map.get(old_archive_id) if isinstance(old_archive_id, int) else None
            if archive_id is None and isinstance(old_archive_id, int):
                # Restoring spools without archives leaves archive_id_map empty,
                # so every "this print consumed that spool" link is dropped — the
                # local archive may well exist, but its payload wasn't fetched,
                # so there is no natural key here to match it on. Nor is it
                # repairable by a later archives-only restore: the dedupe key
                # above doesn't include archive_id, so these rows are recognised
                # as already-present and skipped. Worth telling the user while
                # they can still redo the run with both categories ticked.
                unlinked_archives += 1

            row = SpoolUsageHistory(
                spool_id=spool_id,
                printer_id=printer_id,
                print_name=entry.get("print_name"),
                archive_id=archive_id,
                weight_used=entry.get("weight_used") or 0,
                percent_used=entry.get("percent_used") or 0,
                status=entry.get("status") or "completed",
                cost=entry.get("cost"),
            )
            if created_at is not None:
                row.created_at = created_at
            db.add(row)
            tally.restored += 1

        if unresolved:
            tally.note(
                "spoolUsageUnresolved",
                f"{unresolved} usage record(s) skipped — their spool is not in this backup's "
                "spool list, so there is nothing to attach them to.",
                count=unresolved,
            )
        if unlinked_archives:
            tally.note(
                "spoolUsageUnlinked",
                f"{unlinked_archives} usage record(s) restored without their print-history link — "
                "select Print archives alongside Spool inventory to keep it.",
                count=unlinked_archives,
            )

    async def _restore_settings(
        self,
        db: AsyncSession,
        payload,
        overwrite: bool,
        tally: _CategoryTally,
        keys_written: set[str] | None = None,
    ) -> None:
        values = payload.get("settings") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            tally.note("noData", "No data of this kind in this backup")
            return

        # Planned before the first write, so the companion rule reads genuinely
        # pre-restore local state, and so the preview and this run classify the
        # payload identically.
        plan = await self._plan_settings(db, values)
        refused = plan.refused

        for key, value in values.items():
            if not isinstance(key, str) or not key:
                tally.failed += 1
                continue
            if key in refused:
                # Refusals are reported in the notes and nowhere else. They are
                # already outside the preview's item count, and the preview is
                # the number the user was shown, so counting them here would
                # make restored + skipped + failed exceed it. The two skips
                # below stay counted because they depend on this run's flags,
                # which the preview cannot see.
                continue
            if value is None:
                tally.skipped += 1
                continue

            result = await db.execute(select(Settings).where(Settings.key == key))
            existing = result.scalar_one_or_none()
            if existing is not None:
                if not overwrite:
                    tally.skipped += 1
                    continue
                existing.value = str(value)
                tally.restored += 1
                if keys_written is not None:
                    keys_written.add(key)
                continue

            db.add(Settings(key=key, value=str(value)))
            tally.restored += 1
            if keys_written is not None:
                keys_written.add(key)

        if plan.blocked:
            tally.note(
                "settingsCredentialsSkipped",
                f"{len(plan.blocked)} credential-like key(s) skipped — re-enter secrets manually",
                count=len(plan.blocked),
            )
        if plan.protected:
            tally.note(
                "settingsAuthSkipped",
                f"{len(plan.protected)} authentication setting(s) skipped — change those in Settings > "
                "Authentication so the lockout checks still run",
                count=len(plan.protected),
            )
        if plan.companion:
            keys = ", ".join(sorted(plan.companion))
            tally.note(
                "settingsCompanionSkipped",
                f"{keys} left switched off — the credential each one needs cannot be restored from a "
                "backup and this instance has none stored, so switching them on would leave the "
                "integration unauthenticated",
                keys=keys,
                count=len(plan.companion),
            )

    async def _reconfigure_mqtt_relay(self, db: AsyncSession, keys_written: set[str], tally: _CategoryTally) -> None:
        """Push restored mqtt_* settings into the live relay.

        The relay reads its broker config once, at configure() time — the
        settings PUT handler reconfigures it for exactly this reason
        (api/routes/settings.py). Writing the rows alone left the relay on the
        pre-restore broker until the next backend restart while the UI showed
        the restored values, which is the one way a restore could look applied
        and not be.

        Called after the commit, never before: configure() tears the connection
        down and rebuilds it, so it must not run against values a later failure
        could roll back. Only mqtt_password can't come back this way (the
        credential blocklist skips it) — the row already in the database is
        reused, so an unchanged broker keeps working.
        """
        if not _MQTT_SETTING_KEYS & keys_written:
            return

        try:
            from backend.app.services.mqtt_relay import mqtt_relay

            rows = await db.execute(select(Settings).where(Settings.key.in_(_MQTT_SETTING_KEYS)))
            stored = {s.key: s.value for s in rows.scalars().all()}

            # Same shape and defaults the settings PUT handler builds.
            await mqtt_relay.configure(
                {
                    "mqtt_enabled": (stored.get("mqtt_enabled") or "false") == "true",
                    "mqtt_broker": stored.get("mqtt_broker") or "",
                    "mqtt_port": int(stored.get("mqtt_port") or "1883"),
                    "mqtt_username": stored.get("mqtt_username") or "",
                    "mqtt_password": stored.get("mqtt_password") or "",
                    "mqtt_topic_prefix": stored.get("mqtt_topic_prefix") or "bambuddy",
                    "mqtt_use_tls": (stored.get("mqtt_use_tls") or "false") == "true",
                }
            )
        except Exception:
            # Same call is best-effort in the settings PUT handler: the rows are
            # committed either way, and a broker that refuses the new config
            # must not turn a successful restore into a failed one. Noted rather
            # than swallowed silently, so the user knows to restart.
            logger.warning("Could not reconfigure the MQTT relay after a settings restore", exc_info=True)
            tally.note(
                "settingsMqttRelayFailed",
                "MQTT settings restored, but the relay could not be reconnected — restart Bambuddy",
            )

    async def _restore_kprofiles(self, db: AsyncSession, payload: dict, tally: _CategoryTally) -> None:
        by_serial: dict[str, list[tuple[str, dict]]] = {}
        for path, content in payload.items():
            match = _KPROFILE_PATH_RE.match(path)
            if not match or not isinstance(content, dict):
                continue
            by_serial.setdefault(match.group(1), []).append((match.group(2), content))

        if not by_serial:
            tally.note("noData", "No data of this kind in this backup")
            return

        result = await db.execute(select(Printer))
        printers = {p.serial_number: p for p in result.scalars().all() if p.serial_number}

        # Overwrite is not offered for K-profiles: extrusion_cali_set replaces
        # the profile occupying a slot, so writing is always an overwrite on the
        # printer side.
        tally.note("kprofilesAlwaysOverwrite", "K-profiles always overwrite the matching slot on the printer")
        # A refusal is now believed and counted failed (#2718 made the ack worth
        # reading), but silence still counts restored, so the caveat stands —
        # narrowed to what is actually left uncertain.
        tally.note(
            "kprofilesAckUnreliable",
            "A printer that does not answer still counts as restored — verify the profiles on the printer",
        )

        for serial, entries in sorted(by_serial.items()):
            profile_total = self._kprofile_profile_count(c for _, c in entries)

            printer = printers.get(serial)
            if printer is None:
                tally.skipped += profile_total
                tally.note("kprofilesPrinterMissing", f"No printer with serial {serial} — skipped", serial=serial)
                continue

            client = printer_manager.get_client(printer.id)
            if not client or not client.state.connected:
                tally.skipped += profile_total
                tally.note(
                    "kprofilesPrinterOffline",
                    f"{printer.name} ({serial}) is not connected — skipped",
                    printer=printer.name,
                    serial=serial,
                )
                continue

            for nozzle, content in sorted(entries):
                profiles = content.get("profiles")
                if not isinstance(profiles, list) or not profiles:
                    continue
                if nozzle not in _KNOWN_NOZZLES:
                    tally.note(
                        "kprofilesUnknownNozzle",
                        f"Unexpected nozzle diameter {nozzle} for {serial} — sent as-is",
                        nozzle=nozzle,
                        serial=serial,
                    )

                # The backup's slot_id is a cali_idx, and cali_idx is as
                # unstable as the autoincrement ids we already refuse to reuse
                # for spools and archives: editing a profile in Bambuddy is a
                # delete-then-add on a single-nozzle printer, which re-keys it.
                # Addressing extrusion_cali_set at a slot that no longer exists
                # is a silent no-op — the printer drops it and we would still
                # report the profile restored. So resolve the live index first.
                current = await self._current_kprofile_index(client, nozzle, serial)

                profile_dicts = []
                unmatched = 0
                # A live profile can only stand in for one backed-up entry. Two
                # entries resolving to the same cali_idx both go into the batch,
                # the second overwrites the first on the printer, and the tally
                # counts two restored where one landed.
                claimed: set[int] = set()
                for p in profiles:
                    if not isinstance(p, dict):
                        # Counted, not dropped. _kprofile_profile_count includes
                        # it, so the offline and printer-missing paths already
                        # count the same entry skipped and the failure path
                        # counts it outstanding — leaving the tally here was the
                        # one place a profile could vanish from
                        # restored + skipped + failed entirely.
                        #
                        # failed here against skipped there is not a
                        # disagreement about the entry. The three counters say
                        # what happened to an item on this run, not whether it
                        # was ever usable: an offline printer skips everything it
                        # holds, well-formed or not, because nothing was
                        # attempted, while here the entry was reached and could
                        # not be used.
                        tally.failed += 1
                        continue
                    match = self._match_kprofile(p, current, claimed)
                    if match is None:
                        unmatched += 1
                    else:
                        claimed.add(match.slot_id)
                    entry = {
                        "filament_id": p.get("filament_id", ""),
                        "name": p.get("name", ""),
                        "k_value": p.get("k_value", "0.020000"),
                        "extruder_id": p.get("extruder_id", 0),
                        # Prefer the live setting_id when we matched: it is
                        # what the printer currently associates with the slot.
                        "setting_id": (match.setting_id if match else None) or p.get("setting_id"),
                        # cali_idx -1 tells the printer to add a new profile
                        # rather than address a slot that isn't there.
                        "cali_idx": match.slot_id if match else -1,
                        # Only consulted for the generated-setting_id
                        # fallback; cali_idx above takes precedence.
                        "slot_id": 0,
                    }

                    # Same precedence as setting_id, and set only when known.
                    # nozzle_id encodes the fitted nozzle's type and diameter
                    # ("HS00-0.4"), so the live value beats the backup's: the
                    # user may have swapped the nozzle since. When neither knows,
                    # the key has to be *absent* — set_kprofiles_batch supplies
                    # HS00-{diameter} via p.get(..., default), which a key
                    # present-and-None defeats, publishing a null nozzle_id.
                    # Printers that omit it are the reason the default is there
                    # (#1748), so it has to be reachable.
                    nozzle_id = (getattr(match, "nozzle_id", None) if match else None) or p.get("nozzle_id")
                    if nozzle_id:
                        entry["nozzle_id"] = nozzle_id
                    profile_dicts.append(entry)
                if not profile_dicts:
                    continue
                if unmatched:
                    tally.note(
                        "kprofilesUnmatched",
                        f"{unmatched} profile(s) for {nozzle} had no counterpart on {printer.name} "
                        "— added as new profiles",
                        count=unmatched,
                        nozzle=nozzle,
                        printer=printer.name,
                    )

                try:
                    seq = client.set_kprofiles_batch(profile_dicts, nozzle)
                except Exception as e:
                    logger.warning("K-profile restore failed for %s nozzle %s: %s", serial, nozzle, e)
                    seq = None

                if not seq:
                    tally.failed += len(profile_dicts)
                    tally.note(
                        "kprofilesSendFailed",
                        f"Failed to send {nozzle} profiles to {printer.name} ({serial})",
                        nozzle=nozzle,
                        printer=printer.name,
                        serial=serial,
                    )
                    continue

                # What came back is the sequence_id the command was published
                # under, not a verdict (#2718) — a truthy string only means the
                # command left the building. The printer answers separately, and
                # every other caller of this API now reads that answer; without
                # this the restore would be the one path left that reports a
                # refused write as saved.
                ok, detail = await self._kprofile_ack(client, seq, serial, nozzle)
                if ok:
                    tally.restored += len(profile_dicts)
                else:
                    tally.failed += len(profile_dicts)
                    tally.note(
                        "kprofilesRefused",
                        f"{printer.name} ({serial}) refused the {nozzle} profiles: {detail}",
                        nozzle=nozzle,
                        printer=printer.name,
                        serial=serial,
                        reason=detail,
                    )

    @staticmethod
    def _kprofile_profile_count(contents) -> int:
        """Count the profiles across parsed K-profile files.

        Defensive on purpose. A hand-edited or truncated backup can carry a
        ``profiles`` value that is not a list, and this count runs *before* the
        per-call guards in the loop below — after ``_apply`` has already
        committed the database categories. A malformed file has to be a skipped
        category, not an exception thrown over committed rows.
        """
        total = 0
        for content in contents:
            profiles = content.get("profiles") if isinstance(content, dict) else None
            if isinstance(profiles, list):
                total += len(profiles)
        return total

    @staticmethod
    async def _kprofile_ack(client, seq: str, serial: str, nozzle: str) -> tuple[bool, str]:
        """Read the printer's verdict on one batch write.

        ``await_cali_ack`` already treats silence as success — no answer is not
        evidence of refusal, and firmware that predates the ack never answers at
        all. An exception reading it is the same situation one layer up, so it
        degrades the same way rather than turning a write that most likely
        landed into a reported failure.
        """
        try:
            ok, detail = await client.await_cali_ack(seq)
            return bool(ok), str(detail or "")
        except Exception as e:
            logger.warning("Could not read the K-profile ack for %s nozzle %s: %s", serial, nozzle, e)
            return True, ""

    @staticmethod
    async def _current_kprofile_index(client, nozzle: str, serial: str) -> list:
        """Read the printer's live profiles for one nozzle.

        Best-effort: a read failure degrades to "nothing matched", which makes
        every profile an add rather than aborting the restore.
        """
        try:
            return list(await client.get_kprofiles(nozzle_diameter=nozzle) or [])
        except Exception as e:
            logger.warning("Could not read live K-profiles for %s nozzle %s: %s", serial, nozzle, e)
            return []

    @staticmethod
    def _match_kprofile(entry: dict, current: list, claimed: set[int]):
        """Find the live profile a backed-up entry corresponds to.

        ``setting_id`` is the filament preset the profile was calibrated for and
        is the strongest signal; a delete-then-add edit regenerates it, so fall
        back to the display name, which Bambuddy's own editor preserves.
        Both are scoped by ``filament_id`` — the same preset on a different
        filament is a different profile — and by ``extruder_id``, because on a
        dual-nozzle printer the same preset on the other extruder is a different
        profile too.

        ``claimed`` holds the slot ids already taken by earlier entries in this
        nozzle's loop, and no live profile may be claimed twice. Without it, two
        backed-up entries sharing a ``filament_id`` and matching on neither
        ``setting_id`` nor ``name`` both fell through to the single-candidate
        arm and both took the same slot — reachable whenever the user has since
        deleted one of a pair, because the delete-then-add re-key is what strips
        the ``setting_id`` match. Returning None for the displaced entry means
        ``cali_idx: -1``, i.e. add-as-new, which is the safe outcome.
        """
        filament_id = entry.get("filament_id")
        if not filament_id:
            return None

        candidates = [c for c in current if c.filament_id == filament_id]

        # The live index is read per nozzle *diameter*, so on an H2D both
        # extruders' profiles come back together. With the same filament
        # calibrated on both — the ordinary case on a dual-nozzle printer, not an
        # exotic one — filament_id alone lets extruder 0's backed-up entry match
        # extruder 1's live profile, and the batch then carries
        # {extruder_id: 0, cali_idx: <extruder-1 slot>}: one extruder's
        # calibration written over the other's, counted restored.
        #
        # Conditional on both sides saying which extruder they mean. A pre-#2656
        # backup carries no extruder_id, and a live index that reports none must
        # not turn every entry into an add.
        extruder_id = entry.get("extruder_id")
        if isinstance(extruder_id, int) and any(getattr(c, "extruder_id", None) is not None for c in candidates):
            candidates = [c for c in candidates if getattr(c, "extruder_id", None) == extruder_id]

        available = [c for c in candidates if c.slot_id not in claimed]
        if not available:
            return None

        setting_id = entry.get("setting_id")
        if setting_id:
            for c in available:
                if c.setting_id == setting_id:
                    return c

        name = entry.get("name")
        if name:
            for c in available:
                if c.name == name:
                    return c

        # Exactly one profile for this filament and no better discriminator:
        # treat it as the same profile rather than duplicating it. Judged
        # against every candidate rather than the unclaimed ones, because two
        # live profiles for one filament are ambiguous whether or not another
        # entry has already taken one of them.
        return available[0] if len(candidates) == 1 else None


# Singleton instance
github_restore_service = GitHubRestoreService()
