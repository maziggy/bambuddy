"""Which filaments a caller may print with (#3522).

Permissions answer *what* a caller may do and a printer scope *on which
printers* (#1727); a filament scope answers *with which filaments*. It is
checked when a job is queued (the file's filaments) and again by the scheduler
before dispatch (the file's filaments and the AMS trays it maps them to), so a
job created any other way -- webhook, pipeline, batch, virtual printer -- is
held to it too.

How a scope is derived, as for printers:

* Auth disabled, no user, or an admin: every filament.
* A user: each of their groups with ``restrict_filaments`` set is a policy, and
  a filament any of those policies allows is allowed. A user in no such group
  may use every filament, so installs that never configure this are unchanged.
* A deleted or deactivated user: no policies, so nothing identifiable.

A policy is an allow list or a block list (``filament_rule_mode``). A rule
matches a material type ("PETG", case-insensitive, by name: "PETG" is not
"PETG-CF", but the materials the tray matcher treats as one, such as PA-CF,
PA12-CF and PAHT-CF, count as one here too) or a slicer filament id ("GFG03":
the ``tray_info_idx`` an AMS tray reports and a 3MF records). A
filament with neither a type nor an id can't be judged and is allowed: the
scheduler only blocks dispatch on a positive finding.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.schemas.auth import FilamentPolicyBrief
from backend.app.schemas.group import FilamentRuleSchema
from backend.app.utils.filament_types import canonical_filament_type

RuleMatch = Literal["material", "filament_id"]
RuleMode = Literal["allow", "block"]
RULE_MATCHES: tuple[str, ...] = ("material", "filament_id")
RULE_MODES: tuple[str, ...] = ("allow", "block")


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


@dataclass(frozen=True)
class FilamentRule:
    match: RuleMatch
    value: str

    def matches(self, filament_type: str | None, filament_id: str | None) -> bool:
        wanted = _norm(self.value)
        if not wanted:
            return False
        if self.match == "material":
            # The tray matcher treats some materials as one (PA-CF, PA12-CF
            # and PAHT-CF, ``canonical_filament_type``); a rule naming one of
            # them names them all, or the matcher would map a job onto a
            # spool the rule meant to keep it from, or hold it off one the
            # rule allows.
            return canonical_filament_type(_norm(filament_type)) == canonical_filament_type(wanted)
        return _norm(filament_id) == wanted


@dataclass(frozen=True)
class GroupFilamentPolicy:
    group_name: str
    mode: RuleMode
    rules: tuple[FilamentRule, ...]

    def allows(self, filament_type: str | None, filament_id: str | None) -> bool:
        hit = any(rule.matches(filament_type, filament_id) for rule in self.rules)
        return hit if self.mode == "allow" else not hit


@dataclass(frozen=True)
class FilamentScope:
    """The filaments a caller may use. ``policies=None`` means all of them."""

    policies: tuple[GroupFilamentPolicy, ...] | None = None

    @property
    def is_unrestricted(self) -> bool:
        return self.policies is None

    def allows(self, filament_type: str | None, filament_id: str | None) -> bool:
        if self.policies is None:
            return True
        if not _norm(filament_type) and not _norm(filament_id):
            return True
        return any(policy.allows(filament_type, filament_id) for policy in self.policies)

    def blocked_requirements(self, requirements: Iterable[Mapping]) -> list[Mapping]:
        """The file's filament slots (``extract_filament_requirements`` entries) not allowed."""
        return [r for r in requirements if not self.allows(r.get("type"), r.get("tray_info_idx"))]

    def blocked_tray_ids(self, loaded: Iterable[Mapping]) -> set[int]:
        """Global tray ids (``_build_loaded_filaments`` entries) holding a filament not allowed."""
        return {
            int(tray["global_tray_id"])
            for tray in loaded
            if not self.allows(tray.get("type"), tray.get("tray_info_idx"))
        }


ALL_FILAMENTS = FilamentScope()
NO_FILAMENTS = FilamentScope(())


def clean_rules(raw: Iterable[Mapping | object] | None) -> list[dict]:
    """Rules as stored: known kinds only, values stripped, materials upper-cased, no repeats."""
    cleaned: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw or ():
        if isinstance(entry, Mapping):
            match, value = entry.get("match"), entry.get("value")
        else:
            match, value = getattr(entry, "match", None), getattr(entry, "value", None)
        if match not in RULE_MATCHES or not isinstance(value, str) or not value.strip():
            continue
        value = value.strip().upper() if match == "material" else value.strip()
        key = (match, _norm(value))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"match": match, "value": value})
    return cleaned


def policy_for_group(group) -> GroupFilamentPolicy:
    mode = group.filament_rule_mode if group.filament_rule_mode in RULE_MODES else "block"
    rules = tuple(FilamentRule(r["match"], r["value"]) for r in clean_rules(group.filament_rules))
    return GroupFilamentPolicy(group.name, mode, rules)


def resolve_user_filament_scope(user) -> FilamentScope:
    """Scope of a loaded ``User`` (``groups`` must already be loaded)."""
    if user.is_admin:
        return ALL_FILAMENTS
    restricted = [g for g in user.groups if g.restrict_filaments]
    if not restricted:
        return ALL_FILAMENTS
    return FilamentScope(tuple(policy_for_group(g) for g in restricted))


def filament_policies_for(user) -> list[FilamentPolicyBrief] | None:
    """The user's filament rules for the print dialog; None when unrestricted.

    Every ``UserResponse`` builder fills ``filament_policies`` from this:
    None there means "may print with every filament", so leaving it out for a
    restricted user would say the wrong thing. ``user.groups`` must be loaded.
    """
    scope = resolve_user_filament_scope(user)
    if scope.policies is None:
        return None
    return [
        FilamentPolicyBrief(
            group=policy.group_name,
            mode=policy.mode,
            rules=[FilamentRuleSchema(match=rule.match, value=rule.value) for rule in policy.rules],
        )
        for policy in scope.policies
    ]


async def resolve_user_id_filament_scope(db: AsyncSession, user_id: int | None) -> FilamentScope:
    """Scope of the user with ``user_id``, as the scheduler and the queue routes read it."""
    from sqlalchemy.orm import selectinload

    from backend.app.core.auth import is_auth_enabled
    from backend.app.models.user import User

    if user_id is None or not await is_auth_enabled(db):
        return ALL_FILAMENTS
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return NO_FILAMENTS
    return resolve_user_filament_scope(user)


def describe_filament(entry: Mapping) -> str:
    """Name a filament for messages: "PETG (GFG03)", "PETG" or "GFG03"."""
    kind = str(entry.get("type") or "").strip()
    ident = str(entry.get("tray_info_idx") or "").strip()
    if kind and ident:
        return f"{kind} ({ident})"
    return kind or ident


def describe_filaments(entries: Iterable[Mapping]) -> str:
    """Each filament in ``entries`` named once, sorted: "PETG (GFG03), PLA"."""
    return ", ".join(sorted({describe_filament(e) for e in entries}))


def blocked_message(entries: Iterable[Mapping]) -> str:
    """What the queue route's 403 tells the caller about filaments their groups don't allow."""
    return f"Your group can't print with: {describe_filaments(entries)}"


# The scheduler's holds are read on the queue row, by an admin as well as by
# the job's creator, so they name the creator's group in the third person. A
# file finding and a spool finding are worded apart: the first waits for a
# rule change or another file, the second also ends when the spool is swapped.
_CREATOR_CANT = "the job creator's group can't print with"


def file_hold_message(entries: Iterable[Mapping]) -> str:
    """The scheduler's hold for a file that needs filaments its creator's groups don't allow."""
    return f"This file needs a filament {_CREATOR_CANT}: {describe_filaments(entries)}"


def spool_hold_message(entries: Iterable[Mapping]) -> str:
    """The scheduler's hold for a job that would print from loaded spools its creator's groups don't allow."""
    return f"It would print from a loaded spool {_CREATOR_CANT}: {describe_filaments(entries)}"


def unlisted_file_hold_message(blocked: Iterable[Mapping]) -> str:
    """The scheduler's hold for a file that lists no filaments, on a printer that could feed it a blocked spool."""
    return (
        f"This file doesn't list its filaments, and the printer has a loaded spool {_CREATOR_CANT}: "
        f"{describe_filaments(blocked)}"
    )


def print_anyway_hold_message(unresolved: Iterable[Mapping], blocked: Iterable[Mapping]) -> str:
    """The scheduler's hold for "Print anyway" leaving slots for the printer to fill, next to blocked spools.

    The unmatched filament is named apart: it isn't the one the group blocks,
    and may well be allowed.
    """
    return (
        f"Print anyway leaves {describe_filaments(unresolved)} without a slot, and the printer could pick "
        f"a loaded spool {_CREATOR_CANT}: {describe_filaments(blocked)}"
    )


def judged_requirements(requirements: Iterable[Mapping], overrides: Iterable[object] | None) -> list[Mapping]:
    """The file's filament slots as a scope judges them once the job's overrides apply (#3522).

    One rule wherever a file is judged: ``POST /queue/``, the scheduler and
    the print dialog (``judgedFilaments`` in ``filamentScope.ts``). An
    overridden slot is judged as its override names it: the override's type,
    and its filament id only for a forced colour, since a swap clears the id
    (``PrintScheduler._apply_filament_overrides``). A slot left naming no
    filament id isn't judged here at all: the tray matcher never maps it onto
    a spool the creator may not use, so the tray it prints from is what is
    judged. Judged by its type alone, a group that allows only filament ids
    would refuse a colour swap onto one of its own spools.
    """
    by_slot: dict[int, Mapping] = {}
    for override in overrides or ():
        if isinstance(override, Mapping):
            slot_id = override.get("slot_id")
            if isinstance(slot_id, int) and not isinstance(slot_id, bool):
                by_slot[slot_id] = override
    judged: list[Mapping] = []
    for req in requirements:
        slot_id = req.get("slot_id")
        override = by_slot.get(slot_id) if isinstance(slot_id, int) else None
        if override is None:
            judged.append(req)
            continue
        filament_id = str(override.get("tray_info_idx") or "").strip() if override.get("force_color_match") else ""
        if filament_id:
            judged.append({**req, "type": override.get("type", ""), "tray_info_idx": filament_id})
    return judged


def ensure_requirements_allowed(scope: FilamentScope, requirements: Iterable[Mapping]) -> None:
    """403 naming each filament of the file the caller's groups don't allow."""
    if scope.is_unrestricted:
        return
    blocked = scope.blocked_requirements(requirements)
    if blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=blocked_message(blocked))
