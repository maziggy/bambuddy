"""The inventory mode toggle is no longer destructive (#2812).

Turning Spoolman mode on ran an unfiltered ``delete(SpoolAssignment)`` across
every printer. Turning it straight back off cleared the *other* table instead,
so the built-in assignments were simply gone -- and the setting auto-saves on a
500 ms debounce with no confirmation, so inspecting the mode destroyed the
configuration. The reporter toggled four times in 85 seconds and never got
their assignments back.

The deletion had a real reason: checks that read both assignment tables would
otherwise let a row in the mode you are *not* using answer for the mode you
are. The fix is to make those checks ask which mode is active, which is where
that decision belongs, and then stop deleting.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.slot_kprofile import find_slot_kprofile_for_extruder


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def first(self):
        return self._value

    def all(self):
        return [self._value] if self._value is not None else []


class _TableRoutingSession:
    """Returns a row per model, so a test can populate either table or both."""

    def __init__(self, rows: dict):
        self._rows = rows
        self.queried = []

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        name = entity.__name__
        self.queried.append(name)
        return _Result(self._rows.get(name))


class TestKProfileIgnoresTheInactiveModesTable:
    """slot_kprofile checked the built-in table first and, on a hit with no
    matching profile, deliberately returned None rather than falling through to
    Spoolman. That was safe only while the built-in table was guaranteed empty
    in Spoolman mode. A leftover row would otherwise shadow the Spoolman
    binding -- the symptom #1556 reported from the other direction.
    """

    @pytest.mark.asyncio
    async def test_spoolman_mode_does_not_read_the_built_in_table(self):
        session = _TableRoutingSession(
            {
                # A leftover from before the user switched modes.
                "SpoolAssignment": SimpleNamespace(spool_id=7),
                "SpoolmanSlotAssignment": None,
            }
        )

        with patch(
            "backend.app.services.slot_kprofile.spoolman_owns_assignments",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await find_slot_kprofile_for_extruder(
                session, printer_id=1, ams_id=0, tray_id=0, extruder=0, nozzle_diameter="0.4"
            )

        assert result is None
        assert "SpoolAssignment" not in session.queried

    @pytest.mark.asyncio
    async def test_built_in_mode_does_not_read_the_spoolman_table(self):
        session = _TableRoutingSession(
            {
                "SpoolAssignment": None,
                "SpoolmanSlotAssignment": SimpleNamespace(spoolman_spool_id=9),
            }
        )

        with patch(
            "backend.app.services.slot_kprofile.spoolman_owns_assignments",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await find_slot_kprofile_for_extruder(
                session, printer_id=1, ams_id=0, tray_id=0, extruder=0, nozzle_diameter="0.4"
            )

        assert result is None
        assert "SpoolmanSlotAssignment" not in session.queried


class TestCostEstimateIgnoresTheInactiveModesTable:
    """A leftover built-in assignment must not price a pre-print estimate from
    a spool the printer is not drawing on. The default rate is the honest
    answer once the mode has moved on."""

    @staticmethod
    async def _estimate(spoolman_mode: bool):
        from backend.app.services import print_cost_estimate as pce

        library_file = SimpleNamespace(
            file_path="nowhere/never.3mf",
            file_metadata={"filament_used_grams": 100.0},
            source_folder=None,
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=AssertionError("the built-in table must not be queried"))

        with (
            patch(
                "backend.app.services.print_cost_estimate.spoolman_owns_assignments",
                new_callable=AsyncMock,
                return_value=spoolman_mode,
            ),
            patch(
                "backend.app.services.print_cost_estimate._default_cost_per_kg",
                new_callable=AsyncMock,
                return_value=25.0,
            ),
            patch(
                "backend.app.services.print_cost_estimate._source_path",
                return_value=__import__("pathlib").Path("/nonexistent/never.3mf"),
            ),
        ):
            return await pce.estimate_queue_source_cost(
                db,
                library_file=library_file,
                ams_mapping=[0],
                printer_id=1,
            )

    @pytest.mark.asyncio
    async def test_spoolman_mode_does_not_price_from_built_in_spools(self):
        # 100 g at the 25/kg default. The AsyncMock would raise if the
        # built-in assignment table were queried.
        assert await self._estimate(spoolman_mode=True) == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_built_in_mode_still_reads_its_own_table(self):
        """The guard must not switch the built-in path off as well."""
        with pytest.raises(AssertionError, match="must not be queried"):
            await self._estimate(spoolman_mode=False)


class TestCompletionNotifiesTheLostDebit:
    """The half that turned a mis-click into lost filament.

    A print whose assignments existed at print start and were gone by the time
    it finished resolved its 3MF, resolved its grams, resolved its tray, and
    then skipped the debit because the row was missing -- at INFO, with no
    notification, while the completion notification fired as usual. The
    reporter's 65.49 g was never deducted and nothing surfaced it.

    The print-start check cannot cover this: it runs before the job and was
    correct to stay quiet, because at that moment the assignments existed.
    """

    @pytest.mark.asyncio
    async def test_a_skipped_debit_notifies_at_completion(self):
        from backend.app.services.spool_assignment_notifications import (
            notify_missing_spool_assignments_on_print_complete,
        )

        db = AsyncMock()
        db.get = AsyncMock(return_value=SimpleNamespace(name="Printer A"))
        logger = __import__("logging").getLogger(__name__)

        with (
            patch(
                "backend.app.services.spool_assignment_notifications.printer_manager.get_status",
                return_value=None,
            ),
            patch(
                "backend.app.services.spool_assignment_notifications.ws_manager.send_missing_spool_assignment",
                new_callable=AsyncMock,
            ) as mock_ws,
            patch(
                "backend.app.services.spool_assignment_notifications.notification_service."
                "on_print_missing_spool_assignment",
                new_callable=AsyncMock,
            ) as mock_notify,
        ):
            await notify_missing_spool_assignments_on_print_complete(1, [2], db, logger)

        mock_ws.assert_awaited_once()
        assert mock_ws.await_args.kwargs["missing_slots"] == [{"slot": "A3", "profile": "Unknown", "color": "Unknown"}]
        mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_print_that_debited_everything_stays_quiet(self):
        from backend.app.services.spool_assignment_notifications import (
            notify_missing_spool_assignments_on_print_complete,
        )

        db = AsyncMock()
        logger = __import__("logging").getLogger(__name__)

        with patch(
            "backend.app.services.spool_assignment_notifications.ws_manager.send_missing_spool_assignment",
            new_callable=AsyncMock,
        ) as mock_ws:
            await notify_missing_spool_assignments_on_print_complete(1, [], db, logger)

        mock_ws.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failure_here_never_fails_the_completed_print(self):
        """The print is already done and its spools already written. A
        notification that cannot be sent must not surface as a failed
        completion."""
        from backend.app.services.spool_assignment_notifications import (
            notify_missing_spool_assignments_on_print_complete,
        )

        db = AsyncMock()
        db.get = AsyncMock(side_effect=RuntimeError("db gone"))
        logger = __import__("logging").getLogger(__name__)

        await notify_missing_spool_assignments_on_print_complete(1, [2], db, logger)
