"""FilamentScope semantics (#3522)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.app.core.filament_scope import (
    ALL_FILAMENTS,
    NO_FILAMENTS,
    FilamentRule,
    FilamentScope,
    GroupFilamentPolicy,
    clean_rules,
    describe_filament,
    ensure_requirements_allowed,
    judged_requirements,
    policy_for_group,
    resolve_user_filament_scope,
    resolve_user_id_filament_scope,
)

PETG = FilamentRule("material", "PETG")
PETG_MATTE = FilamentRule("filament_id", "GFG03")


def _block(*rules):
    return GroupFilamentPolicy("Friends", "block", tuple(rules))


def _allow(*rules):
    return GroupFilamentPolicy("Friends", "allow", tuple(rules))


class TestRule:
    def test_material_matches_case_insensitively_and_exactly(self):
        assert PETG.matches("petg", None)
        assert PETG.matches(" PETG ", "")
        assert not PETG.matches("PETG-HF", None)

    def test_filament_id_matches_the_tray_info_idx(self):
        assert PETG_MATTE.matches("PETG", "gfg03")
        assert not PETG_MATTE.matches("PETG", "GFG99")
        assert not PETG_MATTE.matches("PETG", None)

    def test_empty_value_matches_nothing(self):
        assert not FilamentRule("material", " ").matches("", "")


class TestPolicy:
    def test_block_list_allows_what_it_does_not_name(self):
        policy = _block(PETG_MATTE)
        assert not policy.allows("PETG", "GFG03")
        assert policy.allows("PETG", "GFG99")

    def test_allow_list_allows_only_what_it_names(self):
        policy = _allow(FilamentRule("material", "PLA"))
        assert policy.allows("PLA", "GFA00")
        assert not policy.allows("PETG", "GFG03")


class TestScope:
    def test_unrestricted_allows_everything(self):
        assert ALL_FILAMENTS.is_unrestricted
        assert ALL_FILAMENTS.allows("PAHT-CF", "GFN03")

    def test_union_of_groups(self):
        scope = FilamentScope((_allow(FilamentRule("material", "PLA")), _allow(FilamentRule("material", "PETG"))))
        assert scope.allows("PLA", None)
        assert scope.allows("PETG", None)
        assert not scope.allows("ABS", None)

    def test_block_group_does_not_narrow_an_allow_group(self):
        # Allowed by any restricted group means allowed.
        scope = FilamentScope((_block(PETG), _allow(PETG)))
        assert scope.allows("PETG", None)

    def test_unidentifiable_filament_is_allowed(self):
        assert FilamentScope((_allow(FilamentRule("material", "PLA")),)).allows("", "")
        assert NO_FILAMENTS.allows(None, None)

    def test_no_policies_allows_nothing_identifiable(self):
        assert not NO_FILAMENTS.allows("PLA", None)

    def test_blocked_requirements_and_trays(self):
        scope = FilamentScope((_block(PETG_MATTE),))
        reqs = [
            {"slot_id": 1, "type": "PETG", "tray_info_idx": "GFG03"},
            {"slot_id": 2, "type": "PLA", "tray_info_idx": "GFA00"},
        ]
        assert [r["slot_id"] for r in scope.blocked_requirements(reqs)] == [1]
        loaded = [
            {"type": "PETG", "tray_info_idx": "GFG03", "global_tray_id": 0},
            {"type": "PETG", "tray_info_idx": "GFG99", "global_tray_id": 1},
        ]
        assert scope.blocked_tray_ids(loaded) == {0}


class TestCleanRules:
    def test_strips_dedupes_and_drops_junk(self):
        raw = [
            {"match": "material", "value": " petg "},
            {"match": "material", "value": "PETG"},
            {"match": "filament_id", "value": "GFG03"},
            {"match": "colour", "value": "red"},
            {"match": "material", "value": ""},
            "junk",
        ]
        assert clean_rules(raw) == [
            {"match": "material", "value": "PETG"},
            {"match": "filament_id", "value": "GFG03"},
        ]

    def test_accepts_objects_with_attributes(self):
        assert clean_rules([SimpleNamespace(match="filament_id", value="GFG03")]) == [
            {"match": "filament_id", "value": "GFG03"}
        ]


def _group(name, restrict, mode="block", rules=None):
    return SimpleNamespace(name=name, restrict_filaments=restrict, filament_rule_mode=mode, filament_rules=rules)


class TestResolveUser:
    def test_admin_is_unrestricted(self):
        user = SimpleNamespace(
            is_admin=True, groups=[_group("Friends", True, rules=[{"match": "material", "value": "PETG"}])]
        )
        assert resolve_user_filament_scope(user).is_unrestricted

    def test_user_in_no_restricted_group_is_unrestricted(self):
        user = SimpleNamespace(is_admin=False, groups=[_group("Operators", False)])
        assert resolve_user_filament_scope(user).is_unrestricted

    def test_only_restricted_groups_count(self):
        user = SimpleNamespace(
            is_admin=False,
            groups=[
                _group("Operators", False),
                _group("Friends", True, rules=[{"match": "material", "value": "PETG"}]),
            ],
        )
        scope = resolve_user_filament_scope(user)
        assert not scope.allows("PETG", None)
        assert scope.allows("PLA", None)

    def test_policy_for_group_tolerates_missing_values(self):
        policy = policy_for_group(_group("Friends", True, mode=None, rules=None))
        assert policy.mode == "block"
        assert policy.rules == ()


class TestMessages:
    def test_describe(self):
        assert describe_filament({"type": "PETG", "tray_info_idx": "GFG03"}) == "PETG (GFG03)"
        assert describe_filament({"type": "PLA", "tray_info_idx": ""}) == "PLA"
        assert describe_filament({"type": "", "tray_info_idx": "GFG03"}) == "GFG03"

    def test_ensure_raises_403_naming_the_filament(self):
        scope = FilamentScope((_block(PETG_MATTE),))
        with pytest.raises(HTTPException) as exc:
            ensure_requirements_allowed(scope, [{"type": "PETG", "tray_info_idx": "GFG03"}])
        assert exc.value.status_code == 403
        assert "PETG (GFG03)" in exc.value.detail

    def test_ensure_passes_when_allowed(self):
        ensure_requirements_allowed(ALL_FILAMENTS, [{"type": "PETG", "tray_info_idx": "GFG03"}])

    def test_ensure_passes_a_restricted_user_whose_filaments_are_all_allowed(self):
        scope = FilamentScope((_block(PETG_MATTE),))
        ensure_requirements_allowed(
            scope, [{"type": "PETG", "tray_info_idx": "GFG99"}, {"type": "PLA", "tray_info_idx": "GFA00"}]
        )


async def _resolve_id(user, *, auth_enabled: bool = True, user_id: int | None = 7):
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    with patch("backend.app.core.auth.is_auth_enabled", AsyncMock(return_value=auth_enabled)):
        return await resolve_user_id_filament_scope(db, user_id), db


def _restricted_user(**fields):
    group = SimpleNamespace(
        name="Friends",
        restrict_filaments=True,
        filament_rule_mode="block",
        filament_rules=[{"match": "filament_id", "value": "GFG03"}],
    )
    values = {"is_admin": False, "is_active": True, "groups": [group]}
    values.update(fields)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
class TestResolveUserId:
    """The scheduler and the queue route read a scope by user id (#3522)."""

    async def test_auth_off_is_unrestricted_without_a_lookup(self):
        scope, db = await _resolve_id(_restricted_user(), auth_enabled=False)
        assert scope is ALL_FILAMENTS
        db.execute.assert_not_awaited()

    async def test_no_user_is_unrestricted(self):
        scope, db = await _resolve_id(_restricted_user(), user_id=None)
        assert scope is ALL_FILAMENTS
        db.execute.assert_not_awaited()

    async def test_a_deactivated_user_may_use_nothing_identifiable(self):
        scope, _ = await _resolve_id(_restricted_user(is_active=False))
        assert scope is NO_FILAMENTS

    async def test_a_deleted_user_may_use_nothing_identifiable(self):
        scope, _ = await _resolve_id(None)
        assert scope is NO_FILAMENTS

    async def test_an_active_user_gets_their_groups_rules(self):
        scope, _ = await _resolve_id(_restricted_user())
        assert not scope.allows("PETG", "GFG03")
        assert scope.allows("PETG", "GFG99")


# The print dialog's copy of these rules (``frontend/src/utils/filamentScope.ts``)
# reads the same cases, so the two can't disagree on a filament.
_SHARED_CASES = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "__tests__" / "utils" / "filamentScopeCases.json"
)


@pytest.mark.parametrize("case", json.loads(_SHARED_CASES.read_text())["cases"], ids=lambda c: c["name"])
def test_shared_cases_with_the_print_dialog(case):
    rules = tuple(FilamentRule(r["match"], r["value"]) for r in case["rules"])
    scope = FilamentScope((GroupFilamentPolicy("Friends", case["mode"], rules),))
    assert scope.allows(case["type"], case["filament_id"]) is case["allowed"]


class TestJudgedRequirements:
    """One rule for overrides wherever a file is judged: the queue route, the scheduler, the dialog (#3522)."""

    REQS = [
        {"slot_id": 1, "type": "PETG", "tray_info_idx": "GFG03"},
        {"slot_id": 2, "type": "PLA", "tray_info_idx": "GFA00"},
    ]

    def test_without_overrides_the_file_is_judged_as_sliced(self):
        assert judged_requirements(self.REQS, None) == self.REQS

    def test_a_swap_is_left_to_the_tray_matcher(self):
        swap = [{"slot_id": 1, "type": "PETG", "color": "#000000", "force_color_match": False}]
        assert judged_requirements(self.REQS, swap) == [self.REQS[1]]

    def test_a_forced_colour_naming_an_id_is_judged_as_it_names_it(self):
        forced = [{"slot_id": 2, "type": "PETG", "tray_info_idx": "GFG03", "force_color_match": True}]
        assert judged_requirements(self.REQS, forced) == [
            self.REQS[0],
            {"slot_id": 2, "type": "PETG", "tray_info_idx": "GFG03"},
        ]

    def test_a_forced_colour_naming_no_id_is_left_to_the_tray_matcher(self):
        forced = [{"slot_id": 1, "type": "PETG", "force_color_match": True}]
        assert judged_requirements(self.REQS, forced) == [self.REQS[1]]

    def test_junk_entries_are_ignored(self):
        assert judged_requirements(self.REQS, ["x", {"slot_id": True}, {"type": "PLA"}]) == self.REQS
