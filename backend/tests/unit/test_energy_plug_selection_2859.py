"""Energy tracking with more than one plug linked to a printer (#2859).

The reporter had two Home Assistant plugs on a P1S -- its own plug plus a dry
box he wanted to switch from the printer card -- and no archive on that printer
ever carried an energy figure, while his single-plug X2D was fine. Both energy
call sites selected every plug for the printer and then called
``scalar_one_or_none()``, which raises on two rows; the print-start handler
caught that as an ordinary failure, so ``energy_start_kwh`` was never written
and the print-end handler reported "no start kWh recorded" -- indistinguishable
from having no plug at all.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.energy_plug import energy_plug_candidates, select_energy_reading


def _plug(plug_id: int, name: str, *, power: bool = True, ha_entity_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=plug_id,
        name=name,
        controls_printer_power=power,
        plug_type="homeassistant" if ha_entity_id else "tasmota",
        ha_entity_id=ha_entity_id,
    )


class TestCandidateOrdering:
    """Ordering has to be stable: the print-end delta is only meaningful if it
    reads the same counter the print-start reading came from."""

    @pytest.mark.asyncio
    async def test_no_plugs_for_printer(self, db_session, printer_factory):
        printer = await printer_factory()

        assert await energy_plug_candidates(db_session, printer.id) == []

    @pytest.mark.asyncio
    async def test_single_plug_is_the_candidate(self, db_session, printer_factory, smart_plug_factory):
        printer = await printer_factory()
        plug = await smart_plug_factory(name="P1S Power", printer_id=printer.id)

        candidates = await energy_plug_candidates(db_session, printer.id)

        assert [c.id for c in candidates] == [plug.id]

    @pytest.mark.asyncio
    async def test_power_plug_sorts_ahead_of_earlier_accessory(self, db_session, printer_factory, smart_plug_factory):
        printer = await printer_factory()
        accessory = await smart_plug_factory(
            name="Dry Box",
            plug_type="homeassistant",
            printer_id=printer.id,
            controls_printer_power=False,
        )
        power = await smart_plug_factory(
            name="P1S Power",
            plug_type="homeassistant",
            printer_id=printer.id,
            controls_printer_power=True,
        )

        candidates = await energy_plug_candidates(db_session, printer.id)

        assert [c.id for c in candidates] == [power.id, accessory.id]

    @pytest.mark.asyncio
    async def test_ties_break_by_id(self, db_session, printer_factory, smart_plug_factory):
        """`controls_printer_power` defaults to on, so two plugs claiming it is
        the normal case rather than a misconfiguration."""
        printer = await printer_factory()
        first = await smart_plug_factory(name="First", plug_type="homeassistant", printer_id=printer.id)
        second = await smart_plug_factory(name="Second", plug_type="homeassistant", printer_id=printer.id)

        candidates = await energy_plug_candidates(db_session, printer.id)

        assert [c.id for c in candidates] == [first.id, second.id]

    @pytest.mark.asyncio
    async def test_other_printers_plugs_are_not_candidates(self, db_session, printer_factory, smart_plug_factory):
        mine = await printer_factory(name="P1S")
        theirs = await printer_factory(name="X2D")
        plug = await smart_plug_factory(name="P1S Power", printer_id=mine.id)
        await smart_plug_factory(name="X2D Power", printer_id=theirs.id)

        candidates = await energy_plug_candidates(db_session, mine.id)

        assert [c.id for c in candidates] == [plug.id]

    @pytest.mark.asyncio
    async def test_unlinked_plug_is_not_a_candidate(self, db_session, printer_factory, smart_plug_factory):
        printer = await printer_factory()
        await smart_plug_factory(name="Bench Plug", printer_id=None)

        assert await energy_plug_candidates(db_session, printer.id) == []

    @pytest.mark.asyncio
    async def test_no_printer_matches_nothing(self, db_session, smart_plug_factory):
        """`printer_id == None` would compile to `IS NULL` and hand back every
        unlinked plug, so a print could be billed against a bench plug."""
        await smart_plug_factory(name="Bench Plug", printer_id=None)

        assert await energy_plug_candidates(db_session, None) == []

    @pytest.mark.asyncio
    async def test_disabled_plug_sorts_last(self, db_session, printer_factory, smart_plug_factory):
        printer = await printer_factory()
        retired = await smart_plug_factory(
            name="Retired Plug",
            plug_type="homeassistant",
            printer_id=printer.id,
            enabled=False,
        )
        live = await smart_plug_factory(name="P1S Power", plug_type="homeassistant", printer_id=printer.id)

        candidates = await energy_plug_candidates(db_session, printer.id)

        assert [c.id for c in candidates] == [live.id, retired.id]

    @pytest.mark.asyncio
    async def test_only_plug_is_used_even_when_disabled(self, db_session, printer_factory, smart_plug_factory):
        """Ranking, not filtering: a printer whose single plug is disabled
        tracked energy before this module existed and has to keep doing so."""
        printer = await printer_factory()
        retired = await smart_plug_factory(name="Retired Plug", printer_id=printer.id, enabled=False)

        candidates = await energy_plug_candidates(db_session, printer.id)

        assert [c.id for c in candidates] == [retired.id]

    @pytest.mark.asyncio
    async def test_home_assistant_script_sorts_last(self, db_session, printer_factory, smart_plug_factory):
        """A `script.*` entity is linked for the automation triggers and has
        nothing to meter, so it is asked only if nothing else answers."""
        printer = await printer_factory()
        script = await smart_plug_factory(
            name="Notify Script",
            plug_type="homeassistant",
            ha_entity_id="script.notify_done",
            printer_id=printer.id,
        )
        plug = await smart_plug_factory(
            name="P1S Power",
            plug_type="homeassistant",
            ha_entity_id="switch.p1s",
            printer_id=printer.id,
            enabled=False,
        )

        candidates = await energy_plug_candidates(db_session, printer.id)

        # Even disabled, a real switch outranks a script.
        assert [c.id for c in candidates] == [plug.id, script.id]


class TestSelectEnergyReading:
    @pytest.mark.asyncio
    async def test_picks_the_plug_that_reports_a_counter(self):
        """The decisive test in practice: an accessory is usually switch-only,
        so it drops out without the user configuring anything."""
        dry_box = _plug(1, "Dry Box")
        printer_plug = _plug(2, "P1S Power")

        async def read(plug, _db):
            return {"power": 3.0} if plug is dry_box else {"power": 120.0, "total": 41.5}

        selected = await select_energy_reading([dry_box, printer_plug], read, db=None)

        assert selected is not None
        plug, energy = selected
        assert plug is printer_plug
        assert energy["total"] == 41.5

    @pytest.mark.asyncio
    async def test_stops_at_the_first_usable_reading(self):
        first = _plug(1, "P1S Power")
        second = _plug(2, "Dry Box")
        seen = []

        async def read(plug, _db):
            seen.append(plug.name)
            return {"total": 1.0}

        selected = await select_energy_reading([first, second], read, db=None)

        assert selected[0] is first
        assert seen == ["P1S Power"]

    @pytest.mark.asyncio
    async def test_unreachable_plug_does_not_end_the_search(self):
        offline = _plug(1, "Offline")
        printer_plug = _plug(2, "P1S Power")

        async def read(plug, _db):
            return None if plug is offline else {"total": 7.0}

        selected = await select_energy_reading([offline, printer_plug], read, db=None)

        assert selected[0] is printer_plug

    @pytest.mark.asyncio
    async def test_zero_is_a_reading(self):
        """A freshly reset counter is a perfectly good baseline; treating 0 as
        missing would drop the first print after a plug replacement."""
        plug = _plug(1, "P1S Power")

        selected = await select_energy_reading([plug], AsyncMock(return_value={"total": 0.0}), db=None)

        assert selected is not None
        assert selected[1]["total"] == 0.0

    @pytest.mark.asyncio
    async def test_none_when_nothing_reports_a_counter(self):
        async def read(_plug, _db):
            return {"power": 3.0, "total": None}

        assert await select_energy_reading([_plug(1, "Dry Box")], read, db=None) is None

    @pytest.mark.asyncio
    async def test_none_for_an_empty_candidate_list(self):
        assert await select_energy_reading([], AsyncMock(), db=None) is None


class TestRecordEnergyStart:
    """The reported failure, end to end."""

    @pytest.mark.asyncio
    async def test_two_plugs_no_longer_lose_the_start_reading(
        self, db_session, printer_factory, smart_plug_factory, archive_factory
    ):
        printer = await printer_factory()
        await smart_plug_factory(
            name="Dry Box",
            plug_type="homeassistant",
            printer_id=printer.id,
            controls_printer_power=False,
        )
        await smart_plug_factory(
            name="P1S Power",
            plug_type="homeassistant",
            printer_id=printer.id,
            controls_printer_power=True,
        )
        archive = await archive_factory(printer.id)

        from backend.app.main import _record_energy_start

        async def read(plug, _db):
            return {"power": 120.0, "total": 41.5} if plug.name == "P1S Power" else {"power": 2.0}

        with patch("backend.app.main._get_plug_energy", side_effect=read):
            recorded = await _record_energy_start(archive, printer.id, db_session)

        assert recorded is True
        assert archive.energy_start_kwh == 41.5

    @pytest.mark.asyncio
    async def test_single_plug_is_unchanged(self, db_session, printer_factory, smart_plug_factory, archive_factory):
        printer = await printer_factory()
        await smart_plug_factory(name="X2D Power", printer_id=printer.id)
        archive = await archive_factory(printer.id)

        from backend.app.main import _record_energy_start

        with patch("backend.app.main._get_plug_energy", AsyncMock(return_value={"total": 12.25})):
            recorded = await _record_energy_start(archive, printer.id, db_session)

        assert recorded is True
        assert archive.energy_start_kwh == 12.25

    @pytest.mark.asyncio
    async def test_no_plug_records_nothing(self, db_session, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)

        from backend.app.main import _record_energy_start

        recorded = await _record_energy_start(archive, printer.id, db_session)

        assert recorded is False
        assert archive.energy_start_kwh is None

    @pytest.mark.asyncio
    async def test_names_the_plugs_it_tried_when_none_measures(
        self, db_session, printer_factory, smart_plug_factory, archive_factory, capture_logs
    ):
        """ "No plug reports energy" and "no plug at all" used to log the same
        way, which is what made this invisible for the reporter."""
        printer = await printer_factory()
        await smart_plug_factory(name="Dry Box", plug_type="homeassistant", printer_id=printer.id)
        await smart_plug_factory(name="Chamber Light", plug_type="homeassistant", printer_id=printer.id)
        archive = await archive_factory(printer.id)

        from backend.app.main import _record_energy_start

        with patch("backend.app.main._get_plug_energy", AsyncMock(return_value={"power": 1.0})):
            recorded = await _record_energy_start(archive, printer.id, db_session)

        assert recorded is False
        assert archive.energy_start_kwh is None
        logged = "\n".join(record.getMessage() for record in capture_logs.get_warnings())
        assert "Dry Box" in logged
        assert "Chamber Light" in logged
