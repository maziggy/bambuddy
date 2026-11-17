"""check_queue holds a job its creator may not print, and starts it once allowed (#3522).

Run on a real database with the dispatch drained, as the printer-scope hold
(#1727) is in ``test_scheduler_check_queue_holds.py``: the job stays pending
with a reason naming the filament, is never failed, and goes out on its own
on the first pass after the group's rules stop blocking it.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import update

import backend.app.services.print_scheduler as scheduler_module
from backend.app.models.group import Group
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_scheduler_busy_reasons_3018 import _scheduler, one_printer  # noqa: F401
from backend.tests.unit.test_scheduler_filament_scope_3522 import (
    FILE_NEEDS_PETG_MATTE,
    REQ_PETG_MATTE,
    REQ_WHITE_PETG,
    SPOOL_IS_PETG_MATTE,
    _external,
    _tray,
)


async def _job_by_a_creator_blocked_from_petg_matte(ctx, **fields) -> tuple[int, int]:
    """Queue a job created by a user whose group blocks PETG Matte; returns (item id, group id)."""
    async with ctx.session_maker() as db:
        db.add(Settings(key="auth_enabled", value="true"))
        group = Group(
            name="friends",
            permissions=["queue:create"],
            restrict_filaments=True,
            filament_rule_mode="block",
            filament_rules=[{"match": "filament_id", "value": "GFG03"}],
        )
        user = User(username="friend", password_hash="x", is_active=True, groups=[group])
        db.add_all([group, user])
        await db.commit()
    item_id = await ctx.add_item()
    async with ctx.session_maker() as db:
        await db.execute(
            update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(created_by_id=user.id, **fields)
        )
        await db.commit()
    return item_id, group.id


async def _load(ctx, item_id: int) -> PrintQueueItem:
    async with ctx.session_maker() as db:
        return await db.get(PrintQueueItem, item_id)


async def _lift_the_rule(session_maker, group_id: int) -> None:
    async with session_maker() as db:
        await db.execute(update(Group).where(Group.id == group_id).values(filament_rules=[]))
        await db.commit()


def _file_prints_petg_matte():
    return patch.object(PrintScheduler, "_get_filament_requirements", AsyncMock(return_value=[dict(REQ_PETG_MATTE)]))


class TestAJobOnAFixedPrinter:
    @pytest.mark.asyncio
    async def test_is_held_then_starts_once_the_rule_is_lifted(self, one_printer):  # noqa: F811
        item_id, group_id = await _job_by_a_creator_blocked_from_petg_matte(one_printer)

        # One scheduler for both passes, as in production: each pass must read
        # the group's rules afresh, not keep the last pass's answer.
        with _file_prints_petg_matte():
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()
                assert not scheduler._inflight

                item = await _load(one_printer, item_id)
                assert item.status == "pending"
                assert item.waiting_reason == FILE_NEEDS_PETG_MATTE
                assert not item.manual_start
                assert item.error_message is None

                await _lift_the_rule(one_printer.session_maker, group_id)
                await scheduler.check_queue()

        item = await _load(one_printer, item_id)
        assert item.status != "pending"
        assert item.status != "failed"


class TestAJobForAnyPrinterOfAModel:
    @pytest.mark.asyncio
    async def test_is_held_unassigned_then_starts_once_the_rule_is_lifted(self, one_printer):  # noqa: F811
        item_id, group_id = await _job_by_a_creator_blocked_from_petg_matte(
            one_printer, printer_id=None, target_model="X1C"
        )

        with (
            _file_prints_petg_matte(),
            patch(
                "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
                AsyncMock(),
            ) as assigned,
            patch(
                "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
                AsyncMock(),
            ),
        ):
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()
                assert not scheduler._inflight

                item = await _load(one_printer, item_id)
                assert (item.status, item.printer_id) == ("pending", None)
                assert item.waiting_reason == FILE_NEEDS_PETG_MATTE
                # Never assigned to a printer only to be given back on every pass.
                assigned.assert_not_awaited()

                await _lift_the_rule(one_printer.session_maker, group_id)
                await scheduler.check_queue()

        item = await _load(one_printer, item_id)
        assert item.status not in ("pending", "failed")


class TestAnEditBetweenTheCheckAndTheClaim:
    """The pass judges the job as loaded; dispatch sends the row as claimed (#3522).

    An edit landing in between must not carry the job onto a blocked spool:
    it is re-judged once claimed, when edits are refused, and left to the
    next pass, which holds it.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "edit",
        [
            # Without the AMS the blocked external spool would feed.
            {"use_ams": False, "ams_mapping": "[254]"},
            # A real tray turns the AMS back on (#2595): the blocked tray would feed.
            {"use_ams": False, "ams_mapping": "[0]"},
            {"ams_mapping": "[0]"},
        ],
        ids=["use_ams_off_onto_the_external_spool", "use_ams_off_onto_a_blocked_tray", "blocked_tray"],
    )
    async def test_is_not_dispatched_as_edited(self, one_printer, edit):  # noqa: F811
        item_id, _ = await _job_by_a_creator_blocked_from_petg_matte(one_printer)

        # Tray 1 is allowed and is what the pass maps the slot to; tray 0 and
        # the external spool hold PETG Matte, which the group blocks.
        loaded = [_tray(0, "GFG03"), _tray(1, "GFG99"), _external(254, "GFG03")]

        with (
            patch.object(PrintScheduler, "_get_filament_requirements", AsyncMock(return_value=[dict(REQ_WHITE_PETG)])),
            patch.object(PrintScheduler, "_build_loaded_filaments", lambda self, status: [dict(f) for f in loaded]),
        ):
            async with _scheduler(one_printer, idle=True) as scheduler:
                real_claim = scheduler._claim_for_dispatch

                async def claim_after_an_edit(db, claimed_id, printer_id=None):
                    async with one_printer.session_maker() as edit_db:
                        await edit_db.execute(
                            update(PrintQueueItem).where(PrintQueueItem.id == claimed_id).values(**edit)
                        )
                        await edit_db.commit()
                    return await real_claim(db, claimed_id, printer_id)

                clear_claim = AsyncMock(wraps=scheduler._clear_dispatch_claim)
                with (
                    patch.object(scheduler, "_claim_for_dispatch", claim_after_an_edit),
                    patch.object(scheduler, "_clear_dispatch_claim", clear_claim),
                ):
                    await scheduler.check_queue()
                    if scheduler._inflight:
                        await asyncio.gather(*(t for t, _ in scheduler._inflight.values()), return_exceptions=True)
                start_print = scheduler_module.printer_manager.start_print
                start_print.assert_not_called()
                # The claim is backed out, as on every exit from a dispatch.
                assert [c.args[1] for c in clear_claim.await_args_list] == [item_id]

            async with one_printer.session_maker() as db:
                assert (await db.get(PrintQueueItem, item_id)).status == "pending"
                # The harness's in-memory SQLite shares one connection between
                # sessions (StaticPool), so the pass closing its session can
                # roll back the clear the dispatch task just wrote. Done again
                # here so the next pass can claim the row, as it could in
                # production.
                await db.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(dispatching_at=None))
                await db.commit()

            # A stored blocked tray is re-picked onto the allowed one (#3522),
            # which feeds from the AMS whatever use_ams says.
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()
                await asyncio.gather(*(t for t, _ in scheduler._inflight.values()), return_exceptions=True)
                start_print = scheduler_module.printer_manager.start_print
                assert start_print.call_args.kwargs["ams_mapping"] == [1]


def _spools(loaded):
    """The printer's spools as ``_build_loaded_filaments`` reports them."""
    return patch.object(PrintScheduler, "_build_loaded_filaments", lambda self, status: [dict(f) for f in loaded])


def _assignment_notifications():
    return (
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
            AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            AsyncMock(),
        ),
    )


class TestAModelJobWithoutTheAms:
    """The model branch judges the external spool before assigning a printer (#3522)."""

    @pytest.mark.asyncio
    async def test_on_a_dual_nozzle_printer_a_blocked_external_spool_holds_it_unassigned(self, one_printer):  # noqa: F811
        # use_ams picks the extruder there, so every external spool counts.
        async with one_printer.session_maker() as db:
            await db.execute(update(Printer).where(Printer.id == one_printer.printer_id).values(model="H2D"))
            await db.commit()
        item_id, _ = await _job_by_a_creator_blocked_from_petg_matte(
            one_printer, printer_id=None, target_model="H2D", use_ams=False
        )

        assigned_patch, waiting_patch = _assignment_notifications()
        with (
            patch.object(PrintScheduler, "_get_filament_requirements", AsyncMock(return_value=[dict(REQ_WHITE_PETG)])),
            _spools([_tray(1, "GFG99"), _external(254, "GFG03")]),
            assigned_patch as assigned,
            waiting_patch as waiting,
        ):
            async with _scheduler(one_printer, idle=True) as scheduler:
                for _ in range(2):
                    await scheduler.check_queue()
                    assert not scheduler._inflight

        item = await _load(one_printer, item_id)
        assert (item.status, item.printer_id) == ("pending", None)
        assert item.waiting_reason == SPOOL_IS_PETG_MATTE
        # Not assigned only to be given back on every pass.
        assigned.assert_not_awaited()
        # Held before a printer is assigned, the job says so once, as any
        # waiting model job does. Held only after the assignment, it would
        # be given back without a word.
        waiting.assert_awaited_once()
        assert waiting.await_args.kwargs["waiting_reason"] == SPOOL_IS_PETG_MATTE

    @pytest.mark.asyncio
    async def test_on_a_single_nozzle_printer_the_ams_tray_it_is_sent_decides(self, one_printer):  # noqa: F811
        # A real tray in the mapping turns the AMS back on (#2595), so the
        # blocked spool on the holder isn't fed and holds nothing.
        item_id, _ = await _job_by_a_creator_blocked_from_petg_matte(
            one_printer, printer_id=None, target_model="X1C", use_ams=False
        )

        assigned_patch, waiting_patch = _assignment_notifications()
        with (
            patch.object(PrintScheduler, "_get_filament_requirements", AsyncMock(return_value=[dict(REQ_WHITE_PETG)])),
            _spools([_tray(1, "GFG99"), _external(254, "GFG03")]),
            assigned_patch,
            waiting_patch,
        ):
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()
                await asyncio.gather(*(t for t, _ in scheduler._inflight.values()), return_exceptions=True)
                start_print = scheduler_module.printer_manager.start_print
                assert start_print.call_args.kwargs["ams_mapping"] == [1]


class TestAnEditToUseAms:
    @pytest.mark.asyncio
    async def test_counts_as_an_edit_since_the_pass_loaded_the_job(self, one_printer):  # noqa: F811
        # use_ams decides the feed the filament check judges (#3522), so a
        # pass must not act on a job whose use_ams changed under it.
        item_id = await one_printer.add_item()
        async with one_printer.session_maker() as db:
            item = await db.get(PrintQueueItem, item_id)
            assert not await scheduler_module._edited_since_loaded(db, item)
            async with one_printer.session_maker() as edit_db:
                await edit_db.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(use_ams=False))
                await edit_db.commit()
            assert await scheduler_module._edited_since_loaded(db, item)


class TestAJobHeldByALoadedSpool:
    @pytest.mark.asyncio
    async def test_starts_once_the_spool_is_swapped(self, one_printer):  # noqa: F811
        # Sent without the AMS, the job prints from the holder; no AMS tray
        # matches, so nothing turns the AMS back on.
        item_id, _ = await _job_by_a_creator_blocked_from_petg_matte(one_printer, use_ams=False)
        loaded = [_tray(1, "GFA00", "PLA"), _external(254, "GFG03")]

        with (
            patch.object(PrintScheduler, "_get_filament_requirements", AsyncMock(return_value=[dict(REQ_WHITE_PETG)])),
            patch.object(PrintScheduler, "_build_loaded_filaments", lambda self, status: [dict(f) for f in loaded]),
        ):
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()
                assert not scheduler._inflight
                item = await _load(one_printer, item_id)
                assert (item.status, item.waiting_reason) == ("pending", SPOOL_IS_PETG_MATTE)

                loaded[1] = _external(254, "GFG99")
                await scheduler.check_queue()
                await asyncio.gather(*(t for t, _ in scheduler._inflight.values()), return_exceptions=True)
                start_print = scheduler_module.printer_manager.start_print
                assert start_print.call_args.kwargs["ams_mapping"] == [254]


class TestAModelJobHeldOnceAssigned:
    @pytest.mark.asyncio
    async def test_is_released_back_to_the_pool_not_failed_and_never_announced(self, one_printer):  # noqa: F811
        # A file with no readable filament list passes every check made while
        # choosing a printer; once assigned, the printer's blocked tray holds
        # it, and "any X1C" means any, so the printer is given back. That
        # repeats on every pass, so "assigned" is only announced once the job
        # has passed the checks that follow the assignment.
        item_id, _ = await _job_by_a_creator_blocked_from_petg_matte(one_printer, printer_id=None, target_model="X1C")

        assigned_patch, waiting_patch = _assignment_notifications()
        with (
            patch.object(PrintScheduler, "_get_filament_requirements", AsyncMock(return_value=None)),
            _spools([_tray(0, "GFG03"), _tray(1, "GFG99")]),
            assigned_patch as assigned,
            waiting_patch,
        ):
            async with _scheduler(one_printer, idle=True) as scheduler:
                for _ in range(2):
                    await scheduler.check_queue()
                    assert not scheduler._inflight
        assigned.assert_not_awaited()

        item = await _load(one_printer, item_id)
        assert (item.status, item.printer_id, item.error_message) == ("pending", None, None)
        assert item.waiting_reason == (
            "This file doesn't list its filaments, and the printer has a loaded spool "
            "the job creator's group can't print with: PETG (GFG03)"
        )


class TestAJobWhoseFileIsGone:
    @pytest.mark.asyncio
    async def test_fails_as_missing_instead_of_waiting_as_unlisted(self, one_printer):  # noqa: F811
        # A missing file lists no filaments either, but there is nothing to
        # judge or print: it takes the dispatch's missing-file failure.
        item_id, _ = await _job_by_a_creator_blocked_from_petg_matte(one_printer)
        (one_printer.base_dir / "archives" / "job-0.3mf").unlink()

        with _spools([_tray(0, "GFG03")]):
            async with _scheduler(one_printer, idle=True) as scheduler:
                await scheduler.check_queue()

        item = await _load(one_printer, item_id)
        assert item.status == "failed"
        assert "can't print with" not in (item.waiting_reason or "")
