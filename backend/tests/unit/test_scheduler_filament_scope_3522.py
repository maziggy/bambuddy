"""The scheduler holds a job to its creator's filament scope (#3522)."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.filament_scope import ALL_FILAMENTS, FilamentRule, FilamentScope, GroupFilamentPolicy
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import FilamentScopeHold, PrintScheduler

BLOCK_PETG_MATTE = FilamentScope((GroupFilamentPolicy("Friends", "block", (FilamentRule("filament_id", "GFG03"),)),))
# A hold names who may not print it in the third person: an admin reads it too.
FILE_NEEDS_PETG_MATTE = "This file needs a filament the job creator's group can't print with: PETG (GFG03)"
SPOOL_IS_PETG_MATTE = "It would print from a loaded spool the job creator's group can't print with: PETG (GFG03)"


def _tray(global_id: int, tray_info_idx: str, filament_type: str = "PETG") -> dict:
    return {
        "type": filament_type,
        "color": "#FFFFFF",
        "color_known": True,
        "tray_info_idx": tray_info_idx,
        "ams_id": 0,
        "tray_id": global_id,
        "is_ht": False,
        "is_external": False,
        "global_tray_id": global_id,
        "extruder_id": None,
        "remain": 80,
    }


def _external(global_id: int, tray_info_idx: str, filament_type: str = "PETG") -> dict:
    return {**_tray(global_id, tray_info_idx, filament_type), "ams_id": -1, "tray_id": 0, "is_external": True}


REQ_WHITE_PETG = {"slot_id": 1, "type": "PETG", "color": "#FFFFFF", "tray_info_idx": "", "used_grams": 10.0}
REQ_PETG_MATTE = {"slot_id": 1, "type": "PETG", "color": "#FFFFFF", "tray_info_idx": "GFG03", "used_grams": 10.0}
REQ_WHITE_PLA = {"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "", "used_grams": 10.0}


def _item(**kwargs) -> PrintQueueItem:
    defaults = {"id": 1, "printer_id": 1, "archive_id": 1, "created_by_id": 7, "plate_id": None}
    defaults.update(kwargs)
    return PrintQueueItem(**defaults)


async def _run(
    call,
    scope=BLOCK_PETG_MATTE,
    *,
    requirements=(REQ_WHITE_PETG,),
    loaded,
    printer_model="X1C",
    raw_data=None,
    source: Path | None = None,
):
    """Await ``call(scheduler)`` with the creator's scope, the file and the printer's spools stubbed."""
    status = SimpleNamespace(raw_data=raw_data or {}, fila_switch=None, ams_filament_backup=None)
    patches = [
        patch.object(PrintScheduler, "_get_printer", AsyncMock(return_value=SimpleNamespace(model=printer_model))),
        patch.object(scheduler_module, "resolve_user_id_filament_scope", AsyncMock(return_value=scope)),
        patch.object(scheduler_module.printer_manager, "get_status", MagicMock(return_value=status)),
        patch.object(
            PrintScheduler, "_get_filament_requirements", AsyncMock(return_value=[dict(r) for r in requirements])
        ),
        patch.object(PrintScheduler, "_build_loaded_filaments", MagicMock(return_value=loaded)),
        patch.object(PrintScheduler, "_get_bool_setting", AsyncMock(return_value=False)),
        # The job's file is on disk: this module stands in for it.
        patch.object(PrintScheduler, "_source_file_path", AsyncMock(return_value=source or Path(__file__))),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await call(PrintScheduler())


def _compute(item=None):
    return lambda sched: sched._compute_ams_mapping_for_printer(AsyncMock(), 1, item or _item())


def _violation(item=None):
    return lambda sched: sched._filament_scope_violation(AsyncMock(), item or _item())


def _ensure(item):
    return lambda sched: sched._ensure_ams_mapping(AsyncMock(), 1, item)


@pytest.mark.asyncio
class TestMappingSkipsBlockedTrays:
    @pytest.mark.parametrize(
        ("scope", "loaded", "mapping"),
        [
            pytest.param(BLOCK_PETG_MATTE, [_tray(0, "GFG03"), _tray(1, "GFG99")], [1], id="blocked_tray_never_chosen"),
            pytest.param(BLOCK_PETG_MATTE, [_tray(0, "GFG03")], None, id="only_blocked_trays_means_no_mapping"),
            pytest.param(ALL_FILAMENTS, [_tray(0, "GFG03")], [0], id="unrestricted_creator_may_use_any_tray"),
        ],
    )
    async def test_mapping(self, scope, loaded, mapping):
        assert await _run(_compute(), scope, loaded=loaded) == mapping


@pytest.mark.asyncio
class TestViolation:
    @pytest.mark.parametrize(
        ("scope", "requirements", "message"),
        [
            pytest.param(BLOCK_PETG_MATTE, [REQ_PETG_MATTE], FILE_NEEDS_PETG_MATTE, id="file_needs_a_blocked_filament"),
            pytest.param(BLOCK_PETG_MATTE, [REQ_WHITE_PETG], None, id="allowed_file"),
            pytest.param(ALL_FILAMENTS, [REQ_PETG_MATTE], None, id="unrestricted_skips_the_check"),
        ],
    )
    async def test_violation(self, scope, requirements, message):
        loaded = [_tray(0, "GFG03"), _tray(1, "GFG99")]
        assert await _run(_violation(), scope, requirements=requirements, loaded=loaded) == message


@pytest.mark.asyncio
class TestStoredMappingOnBlockedTray:
    @pytest.mark.parametrize("print_anyway", [False, True], ids=["checked", "print_anyway"])
    async def test_is_recomputed_onto_an_allowed_tray(self, print_anyway):
        # "Print anyway" skips the filament check, but never keeps a blocked tray.
        item = _item(ams_mapping=json.dumps([0]), skip_filament_check=print_anyway)
        result = await _run(_ensure(item), loaded=[_tray(0, "GFG03"), _tray(1, "GFG99")])
        assert result is None
        assert json.loads(item.ams_mapping)[0] == 1

    @pytest.mark.parametrize(("stored", "uses_blocked"), [([1], False), ([0], True)], ids=["allowed", "blocked"])
    async def test_mapping_uses_blocked_tray(self, stored, uses_blocked):
        uses = await _run(
            lambda sched: sched._mapping_uses_blocked_tray(AsyncMock(), 1, _item(), stored),
            loaded=[_tray(0, "GFG03"), _tray(1, "GFG99")],
        )
        assert uses is uses_blocked


def _force_override(tray_info_idx: str) -> str:
    """A ``force_color_match`` override for slot 1, white PETG, as the print dialog stores it."""
    return json.dumps(
        [{"slot_id": 1, "type": "PETG", "color": "#FFFFFF", "tray_info_idx": tray_info_idx, "force_color_match": True}]
    )


@pytest.mark.asyncio
class TestForcedOverridesWithoutFileRequirements:
    """A 3MF without slice info falls back to its force-colour overrides (#3280); so does the scope."""

    async def test_a_forced_blocked_filament_is_a_violation(self):
        item = _item(filament_overrides=_force_override("GFG03"))
        message = await _run(_violation(item), requirements=[], loaded=[_tray(1, "GFG99")])
        assert message == FILE_NEEDS_PETG_MATTE

    async def test_the_direct_override_mapping_skips_blocked_trays(self):
        item = _item(filament_overrides=_force_override(""))
        mapping = await _run(_compute(item), requirements=[], loaded=[_tray(0, "GFG03"), _tray(1, "GFG99")])
        assert mapping is not None
        assert 0 not in mapping
        assert mapping[0] == 1


PRINT_ANYWAY = {"skip_filament_check": True}
PRINT_ANYWAY_LEAVES_PETG = (
    "Print anyway leaves PETG without a slot, and the printer could pick a loaded spool "
    "the job creator's group can't print with: PETG (GFG03)"
)


@pytest.mark.asyncio
class TestHoldNotFail:
    """A permission finding holds the job, it doesn't fail it (#3522)."""

    @pytest.mark.parametrize(
        ("item_fields", "requirements", "loaded", "reason"),
        [
            pytest.param(
                {"ams_mapping": "[1]"},
                [REQ_PETG_MATTE],
                [_tray(1, "GFG99")],
                FILE_NEEDS_PETG_MATTE,
                id="file_needs_a_blocked_filament",
            ),
            pytest.param(
                {"ams_mapping": "[1]", **PRINT_ANYWAY},
                [REQ_PETG_MATTE],
                [_tray(1, "GFG99")],
                FILE_NEEDS_PETG_MATTE,
                id="file_needs_a_blocked_filament_even_with_print_anyway",
            ),
            pytest.param(
                {"ams_mapping": "[0]", **PRINT_ANYWAY},
                [REQ_WHITE_PETG],
                [_tray(0, "GFG03")],
                PRINT_ANYWAY_LEAVES_PETG,
                id="print_anyway_with_only_blocked_trays",
            ),
            pytest.param(
                {"ams_mapping": None, **PRINT_ANYWAY},
                [REQ_WHITE_PETG],
                [_tray(0, "GFG03")],
                PRINT_ANYWAY_LEAVES_PETG,
                id="print_anyway_with_no_mapping_and_only_blocked_trays",
            ),
            pytest.param(
                # No AMS tray matches, so nothing turns the AMS back on (#2595).
                {"use_ams": False},
                [REQ_WHITE_PETG],
                [_tray(1, "GFA00", "PLA"), _external(254, "GFG03")],
                SPOOL_IS_PETG_MATTE,
                id="external_spool_with_a_blocked_filament_without_the_ams",
            ),
        ],
    )
    async def test_is_held_with_the_reason(self, item_fields, requirements, loaded, reason):
        result = await _run(_ensure(_item(**item_fields)), requirements=requirements, loaded=loaded)
        assert isinstance(result, FilamentScopeHold)
        assert result.reason == reason

    @pytest.mark.parametrize(
        ("item_fields", "loaded"),
        [
            pytest.param(
                {"ams_mapping": "[1]", **PRINT_ANYWAY},
                [_tray(0, "GFG03"), _tray(1, "GFG99")],
                id="print_anyway_with_an_allowed_tray",
            ),
            pytest.param(
                {"use_ams": False},
                [_tray(1, "GFG03"), _external(254, "GFG99")],
                id="external_spool_with_an_allowed_filament_without_the_ams",
            ),
        ],
    )
    async def test_dispatches(self, item_fields, loaded):
        assert await _run(_ensure(_item(**item_fields)), loaded=loaded) is None

    async def test_unrestricted_print_anyway_still_short_circuits(self):
        item = _item(ams_mapping=json.dumps([0]), use_ams=False, **PRINT_ANYWAY)
        result = await _run(
            _ensure(item),
            ALL_FILAMENTS,
            requirements=[REQ_PETG_MATTE],
            loaded=[_tray(0, "GFG03"), _external(254, "GFG03")],
        )
        assert result is None
        assert json.loads(item.ams_mapping) == [0]


@pytest.mark.asyncio
class TestScopeIsResolvedOncePerPass:
    async def test_two_candidate_printers_resolve_the_scope_once(self):
        async def two_printers(sched):
            await sched._compute_ams_mapping_for_printer(AsyncMock(), 1, _item())
            await sched._compute_ams_mapping_for_printer(AsyncMock(), 2, _item())
            return scheduler_module.resolve_user_id_filament_scope.await_count

        assert await _run(two_printers, loaded=[_tray(1, "GFG99")]) == 1

    async def test_memo_answers_repeat_calls_and_is_cleared_each_pass(self):
        sched = PrintScheduler()
        resolve = AsyncMock(return_value=BLOCK_PETG_MATTE)
        with patch.object(scheduler_module, "resolve_user_id_filament_scope", resolve):
            for _ in range(3):
                assert await sched._creator_filament_scope(AsyncMock(), 7) is BLOCK_PETG_MATTE
            assert resolve.await_count == 1
            assert (await sched._creator_filament_scope(AsyncMock(), None)).is_unrestricted
            assert resolve.await_count == 1
            sched._filament_scope_memo.clear()
            await sched._creator_filament_scope(AsyncMock(), 7)
            assert resolve.await_count == 2


@pytest.mark.asyncio
class TestModelSelectionUnderPrintAnyway:
    async def _unmatched(self, scope):
        item = _item(printer_id=None, target_model="X1C", **PRINT_ANYWAY)
        return await _run(lambda sched: sched._unmatched_on(AsyncMock(), item, 1), scope, loaded=[_tray(0, "GFG03")])

    async def test_a_restricted_creator_does_not_skip_the_printer_match(self):
        assert await self._unmatched(BLOCK_PETG_MATTE)

    async def test_an_unrestricted_creator_still_skips_it(self):
        assert await self._unmatched(ALL_FILAMENTS) == []


@pytest.mark.asyncio
class TestWithoutTheAmsTheMappingDoesNotDecide:
    """With use_ams off and no tray mapped, a single-nozzle printer feeds its external spool (#3522)."""

    async def _judge(self, model: str):
        return await _run(
            _ensure(_item(use_ams=False, **PRINT_ANYWAY)),
            # "Print anyway" for PLA from an allowed PETG spool: no slot maps,
            # and a blocked tray is loaded the printer could pick.
            requirements=[REQ_WHITE_PLA],
            loaded=[_tray(0, "GFG03"), _external(254, "GFG99")],
            printer_model=model,
        )

    async def test_an_allowed_external_spool_is_enough_on_a_single_nozzle_printer(self):
        assert await self._judge("X1C") is None

    async def test_a_dual_nozzle_printer_still_has_its_mapping_judged(self):
        assert isinstance(await self._judge("H2D"), FilamentScopeHold)


def _claimed(item):
    return lambda sched: sched._claimed_scope_violation(AsyncMock(), item)


BLOCK_TPU = FilamentScope((GroupFilamentPolicy("Friends", "block", (FilamentRule("material", "TPU"),)),))
ALLOW_ONLY_PETG_MATTE = FilamentScope(
    (GroupFilamentPolicy("Friends", "allow", (FilamentRule("filament_id", "GFG03"),)),)
)
# Slot 1 white PETG, slot 2 white PLA: two filaments the plate prints.
TWO_SLOTS = (REQ_WHITE_PETG, {**REQ_WHITE_PLA, "slot_id": 2})


@pytest.mark.asyncio
class TestTheFeedThePrinterIsSent:
    """The feed is judged as the printer receives it (#3522).

    On a single-nozzle printer the command builder turns ``use_ams=False``
    back on when the mapping names a real tray (#2595), unless the plate
    prints only from the spool holder; and a printer that reported no AMS
    feeds the spool holder whatever ``use_ams`` says (#3356).
    """

    @pytest.mark.parametrize(
        ("item_fields", "requirements", "loaded", "raw_data", "reason"),
        [
            pytest.param(
                {"use_ams": False, "ams_mapping": "[0]"},
                (REQ_WHITE_PETG,),
                [_tray(0, "GFG03"), _external(254, "GFG99")],
                None,
                SPOOL_IS_PETG_MATTE,
                id="use_ams_off_but_the_mapping_names_a_blocked_tray",
            ),
            pytest.param(
                {"use_ams": False, "ams_mapping": "[1, -1]", **PRINT_ANYWAY},
                TWO_SLOTS,
                [_tray(0, "GFG03"), _tray(1, "GFG99"), _external(254, "GFG99")],
                None,
                "Print anyway leaves PLA without a slot, and the printer could pick a loaded spool "
                "the job creator's group can't print with: PETG (GFG03)",
                id="use_ams_off_print_anyway_with_an_unresolved_slot",
            ),
            pytest.param(
                {"ams_mapping": None},
                (REQ_WHITE_PETG,),
                [_external(254, "GFG03")],
                {"ams": []},
                SPOOL_IS_PETG_MATTE,
                id="no_ams_feeds_the_spool_holder_even_with_use_ams_on",
            ),
        ],
    )
    async def test_is_held(self, item_fields, requirements, loaded, raw_data, reason):
        result = await _run(_claimed(_item(**item_fields)), requirements=requirements, loaded=loaded, raw_data=raw_data)
        assert result == reason

    @pytest.mark.parametrize(
        ("item_fields", "requirements", "loaded"),
        [
            pytest.param(
                {"use_ams": False, "ams_mapping": "[1]"},
                (REQ_WHITE_PETG,),
                [_tray(1, "GFG99"), _external(254, "GFG03")],
                id="use_ams_off_but_the_mapping_names_an_allowed_tray",
            ),
            pytest.param(
                # Slot 2 is another plate's: this plate prints only from the
                # spool holder, so the AMS stays off (#3087) and tray 0 unused.
                {"use_ams": False, "ams_mapping": "[254, 0]"},
                (REQ_WHITE_PETG,),
                [_tray(0, "GFG03"), _external(254, "GFG99")],
                id="plate_prints_only_the_allowed_external_spool",
            ),
        ],
    )
    async def test_dispatches(self, item_fields, requirements, loaded):
        assert await _run(_claimed(_item(**item_fields)), requirements=requirements, loaded=loaded) is None

    async def test_a_blocked_external_spool_does_not_hold_a_job_the_ams_feeds(self):
        # use_ams=False as a Virtual Printer stamps it; the matcher picks the
        # allowed AMS tray, which turns the AMS back on (#2595).
        item = _item(use_ams=False)
        result = await _run(_ensure(item), loaded=[_tray(1, "GFG99"), _external(254, "GFG03")])
        assert result is None
        assert json.loads(item.ams_mapping) == [1]


@pytest.mark.asyncio
class TestOverriddenSlotsAndFilamentIdRules:
    """An overridden slot is left to the tray filter, not judged without its filament id (#3522)."""

    @staticmethod
    def _override(*, force: bool, tray_info_idx: str = "GFG03") -> str:
        return json.dumps(
            [
                {
                    "slot_id": 1,
                    "type": "PETG",
                    "color": "#000000",
                    "tray_info_idx": tray_info_idx,
                    "force_color_match": force,
                }
            ]
        )

    @pytest.mark.parametrize(
        ("override", "message"),
        [
            # A colour swap to another allowed GFG03 spool clears the id.
            pytest.param({"force": False}, None, id="colour_swap"),
            pytest.param({"force": True, "tray_info_idx": ""}, None, id="forced_colour_naming_no_id"),
            pytest.param({"force": True}, None, id="forced_colour_of_the_allowed_id"),
        ],
    )
    async def test_allowed_override_is_not_held(self, override, message):
        item = _item(filament_overrides=self._override(**override))
        result = await _run(
            _violation(item), ALLOW_ONLY_PETG_MATTE, requirements=[REQ_PETG_MATTE], loaded=[_tray(0, "GFG03")]
        )
        assert result == message

    async def test_a_forced_override_naming_a_blocked_id_is_still_held(self):
        item = _item(filament_overrides=self._override(force=True))
        result = await _run(_violation(item), requirements=[REQ_WHITE_PETG], loaded=[_tray(1, "GFG99")])
        assert result == FILE_NEEDS_PETG_MATTE

    async def test_an_override_onto_a_blocked_tray_with_print_anyway_is_held_at_dispatch(self):
        # Skipped by the file check; the tray filter leaves the slot unmapped,
        # and "Print anyway" doesn't let the printer pick the blocked tray.
        item = _item(filament_overrides=self._override(force=False), **PRINT_ANYWAY)
        result = await _run(_ensure(item), requirements=[REQ_WHITE_PLA], loaded=[_tray(0, "GFG03")])
        assert isinstance(result, FilamentScopeHold)
        assert "PETG (GFG03)" in result.reason


@pytest.mark.asyncio
class TestPrintAnywayOnlyWhereABlockedTrayIsLoaded:
    """The unresolved-slot check needs a blocked tray on the printer (#3522)."""

    async def test_nothing_blocked_loaded_lets_print_anyway_through(self):
        item = _item(ams_mapping=None, **PRINT_ANYWAY)
        result = await _run(_claimed(item), BLOCK_TPU, requirements=[REQ_WHITE_PLA], loaded=[_tray(0, "GFG99")])
        assert result is None

    async def test_a_blocked_tray_holds_it_naming_only_the_blocked_filament(self):
        item = _item(ams_mapping=None, **PRINT_ANYWAY)
        result = await _run(
            _claimed(item),
            BLOCK_TPU,
            requirements=[REQ_WHITE_PLA],
            loaded=[_tray(0, "", "TPU"), _tray(1, "GFG99")],
        )
        assert result == (
            "Print anyway leaves PLA without a slot, and the printer could pick a loaded spool "
            "the job creator's group can't print with: TPU"
        )

    async def test_a_blocked_external_spool_the_ams_job_is_not_fed_from_holds_nothing(self):
        # Single nozzle, AMS on: a slot left without a tray never goes to the
        # spool holder, so the blocked spool there is not one it could pick.
        item = _item(ams_mapping=None, **PRINT_ANYWAY)
        loaded = [_tray(1, "GFG99"), _external(254, "GFG03")]
        assert await _run(_claimed(item), requirements=[REQ_WHITE_PLA], loaded=loaded) is None

    async def test_a_blocked_external_spool_the_job_is_fed_from_still_holds_it(self):
        item = _item(ams_mapping=None, use_ams=False, **PRINT_ANYWAY)
        loaded = [_tray(1, "GFG99"), _external(254, "GFG03")]
        assert await _run(_claimed(item), requirements=[REQ_WHITE_PLA], loaded=loaded) == SPOOL_IS_PETG_MATTE


H2D_WITHOUT_AMS = {"ams": [], "vt_tray": [{"id": "254"}, {"id": "255"}]}


@pytest.mark.asyncio
class TestDualNozzleWithOnlyExternalSpools:
    """A blocked spool that would have matched holds the job, it doesn't fail it (#3522)."""

    @pytest.mark.parametrize(
        "loaded",
        [
            pytest.param([_external(254, "GFG03"), _external(255, "GFA00", "PLA")], id="next_to_an_allowed_spool"),
            pytest.param([_external(254, "GFG03")], id="alone"),
        ],
    )
    async def test_is_held_with_the_permission_reason(self, loaded):
        result = await _run(_ensure(_item()), loaded=loaded, raw_data=H2D_WITHOUT_AMS, printer_model="H2D")
        assert result == FilamentScopeHold(SPOOL_IS_PETG_MATTE)

    async def test_the_failure_does_not_list_a_blocked_spool_as_loaded(self):
        # Nothing loaded matches TPU, blocked or not: the job fails (#2771),
        # naming only the spools its creator may use.
        tpu = {**REQ_WHITE_PLA, "type": "TPU"}
        result = await _run(
            _ensure(_item()),
            requirements=[tpu],
            loaded=[_external(254, "GFG03"), _external(255, "GFA00", "PLA")],
            raw_data=H2D_WITHOUT_AMS,
            printer_model="H2D",
        )
        assert isinstance(result, str)
        assert "PLA" in result
        assert "PETG" not in result


@pytest.mark.asyncio
class TestModelSelectionUnderPrintAnywayWithNothingBlockedLoaded:
    async def test_print_anyway_is_honoured_where_no_blocked_tray_is_loaded(self):
        # Dispatch lets it through with nothing blocked loaded, so choosing a
        # printer does too.
        item = _item(printer_id=None, target_model="X1C", **PRINT_ANYWAY)
        unmatched = await _run(
            lambda sched: sched._unmatched_on(AsyncMock(), item, 1),
            BLOCK_TPU,
            requirements=[REQ_WHITE_PLA],
            loaded=[_tray(0, "GFG03")],
        )
        assert unmatched == []


@pytest.mark.asyncio
class TestAMappedTrayThePrinterDoesNotHave:
    """A tray id the printer doesn't report loaded settles nothing for a restricted creator (#3522).

    Only a hand-written payload carries one, and "Print anyway" keeps it past
    the fit check, so it is read as a slot left for the printer to fill.
    """

    async def test_on_a_printer_without_an_ams_the_blocked_external_spool_holds_it(self):
        # use_ams stays on, but the command builder turns it off for a printer
        # that reports no AMS, so the spool holder is what feeds.
        item = _item(ams_mapping="[0]", **PRINT_ANYWAY)
        result = await _run(
            _ensure(item), requirements=[REQ_WHITE_PLA], loaded=[_external(254, "GFG03")], raw_data={"ams": []}
        )
        assert result == FilamentScopeHold(SPOOL_IS_PETG_MATTE)

    async def test_on_a_printer_with_an_ams_it_is_left_for_the_printer_to_fill(self):
        item = _item(ams_mapping="[5]", **PRINT_ANYWAY)
        result = await _run(
            _ensure(item), requirements=[REQ_WHITE_PLA], loaded=[_tray(0, "GFG03"), _tray(1, "GFA00", "PLA")]
        )
        assert result == FilamentScopeHold(
            "Print anyway leaves PLA without a slot, and the printer could pick a loaded spool "
            "the job creator's group can't print with: PETG (GFG03)"
        )


# An H3C has an external input per hotend, 247-255, and reports the ones in use.
H3C_INPUTS = {"vt_tray": [{"id": "250"}, {"id": "251"}]}


@pytest.mark.asyncio
class TestARackPrintersExternalInputs:
    """On an H3C the mapping names the external input that feeds, so only that one is judged (#3522)."""

    @pytest.mark.parametrize(
        ("mapping", "raw_data", "reason"),
        [
            pytest.param("[250]", H3C_INPUTS, None, id="mapped_to_the_allowed_input"),
            pytest.param("[251]", H3C_INPUTS, SPOOL_IS_PETG_MATTE, id="mapped_to_the_blocked_input"),
            pytest.param("[250, 251]", H3C_INPUTS, SPOOL_IS_PETG_MATTE, id="mapped_to_both"),
            # No AMS and no input named: any of them may feed.
            pytest.param(None, {**H3C_INPUTS, "ams": []}, SPOOL_IS_PETG_MATTE, id="no_input_named"),
        ],
    )
    async def test_only_a_named_input_holds_the_job(self, mapping, raw_data, reason):
        result = await _run(
            _claimed(_item(ams_mapping=mapping)),
            loaded=[_external(250, "GFG99"), _external(251, "GFG03")],
            raw_data=raw_data,
            printer_model="H3C",
        )
        assert result == reason


UNLISTED_NEXT_TO_PETG_MATTE = (
    "This file doesn't list its filaments, and the printer has a loaded spool "
    "the job creator's group can't print with: PETG (GFG03)"
)


@pytest.mark.asyncio
class TestAFileThatListsNoFilaments:
    """A file without readable slice info can't be judged from the file, so the printer is (#3522)."""

    @pytest.mark.parametrize(
        ("scope", "loaded", "raw_data", "result"),
        [
            pytest.param(
                BLOCK_PETG_MATTE,
                [_external(254, "GFG03")],
                {"ams": []},
                FilamentScopeHold(SPOOL_IS_PETG_MATTE),
                id="no_ams_and_a_blocked_external_spool",
            ),
            pytest.param(BLOCK_PETG_MATTE, [_external(254, "GFG99")], {"ams": []}, None, id="allowed_external_spool"),
            pytest.param(
                BLOCK_PETG_MATTE,
                [_tray(0, "GFG03"), _tray(1, "GFG99")],
                None,
                FilamentScopeHold(UNLISTED_NEXT_TO_PETG_MATTE),
                id="a_blocked_ams_tray_the_printer_could_pick",
            ),
            pytest.param(
                # The AMS feeds, so the spool holder isn't one it could pick.
                BLOCK_PETG_MATTE,
                [_tray(1, "GFG99"), _external(254, "GFG03")],
                None,
                None,
                id="only_the_unfed_external_spool_is_blocked",
            ),
            pytest.param(ALL_FILAMENTS, [_tray(0, "GFG03")], None, None, id="unrestricted_creator_is_unaffected"),
        ],
    )
    async def test_the_spools_it_could_be_fed_from_decide(self, scope, loaded, raw_data, result):
        assert await _run(_ensure(_item()), scope, requirements=[], loaded=loaded, raw_data=raw_data) == result

    async def test_a_file_that_is_gone_is_left_to_the_missing_file_failure(self, tmp_path):
        result = await _run(_ensure(_item()), requirements=[], loaded=[_tray(0, "GFG03")], source=tmp_path / "gone.3mf")
        assert result is None

    @pytest.mark.parametrize("item_fields", [{}, {"ams_mapping": "[254]"}], ids=["no_mapping", "mapped_to_it"])
    async def test_a_dual_nozzle_printer_without_an_ams_may_feed_its_blocked_external_spool(self, item_fields):
        # use_ams picks the extruder there, not the AMS, so it stays on and
        # the external spools are all the printer has to feed from.
        result = await _run(
            _ensure(_item(**item_fields)),
            requirements=[],
            loaded=[_external(254, "GFG03"), _external(255, "GFA00", "PLA")],
            raw_data=H2D_WITHOUT_AMS,
            printer_model="H2D",
        )
        assert result == FilamentScopeHold(UNLISTED_NEXT_TO_PETG_MATTE)


@pytest.mark.asyncio
class TestTheCheckOnceClaimed:
    async def test_reads_the_creators_rules_afresh(self):
        # A pass can wait minutes on a smart plug; a rule tightened meanwhile
        # applies to the job it dispatches next.
        async def stale_then_claimed(sched):
            sched._filament_scope_memo[7] = ALL_FILAMENTS
            return await sched._claimed_scope_violation(AsyncMock(), _item())

        assert await _run(stale_then_claimed, requirements=[REQ_PETG_MATTE], loaded=[]) == FILE_NEEDS_PETG_MATTE


@pytest.mark.asyncio
class TestStartingAStagedJob:
    async def test_reads_the_creators_rules_afresh(self):
        # Start is a request, not a pass: a rule lifted since the last pass
        # lets the job map onto the tray it was held from.
        async def stale_then_start(sched):
            sched._filament_scope_memo[7] = BLOCK_PETG_MATTE
            return await sched.missing_filament_for_start(AsyncMock(), _item())

        assert await _run(stale_then_start, ALL_FILAMENTS, loaded=[_tray(0, "GFG03")]) is None
