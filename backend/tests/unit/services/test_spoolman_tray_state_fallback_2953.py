"""Tray-state slot mapping for printers that can answer no other way (#2953).

#2768 gave the Spoolman writer two ways to recover a print's slot-to-tray
mapping when print start captured none: the printer's ``mapping`` field, and a
colour match of the 3MF's slots against the loaded trays. An A1 can satisfy
neither. It publishes no ``mapping`` field, and it drops the MQTT connection
when Bambuddy subscribes to its request topic, so the slicer's own instruction
never arrives either. That leaves the colour match, which compares hex strings
exactly.

The reporter sliced with a generic black profile (``#000000``) against a tray
they had set to ``#111111``. No match, so every print fell through to the
positional default and charged slot 1 to the first loaded tray -- the grey PLA+
in tray 0 -- while the print was actually fed from tray 3. Their log carries
the printer's own answer, ``Tray change during print: tray=3 at layer=0``,
recorded 90 seconds into the print and already read by ``_print_used_tray_keys``
further down the same completion pass.

Values throughout are the ones from archive 12 of the reporter's support
bundle.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.spoolman_tracking import (
    _resolve_slot_to_tray_fallback,
    _single_slot_tray_from_state,
)

# The reporter's AMS at completion: tray 1 is empty, and no tray is #000000.
REPORTER_AMS = [
    {
        "id": 0,
        "tray": [
            {"id": 0, "tray_color": "888888FF", "tray_type": "PLA+"},
            {"id": 1, "tray_color": None, "tray_type": None},
            {"id": 2, "tray_color": "5F4036FF", "tray_type": "PLA"},
            {"id": 3, "tray_color": "111111FF", "tray_type": "PLA+"},
        ],
    }
]
REPORTER_USAGE = [{"slot_id": 1, "used_g": 2.17, "type": "PLA+", "color": "#000000"}]


class _AsyncCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _state(tray_change_log=None, tray_now=255, last_loaded_tray=-1, **raw):
    """An A1 as it looks at completion: no mapping field, nothing loaded."""
    return SimpleNamespace(
        raw_data=raw,
        layer_num=0,
        total_layers=0,
        tray_change_log=list(tray_change_log or []),
        tray_now=tray_now,
        last_loaded_tray=last_loaded_tray,
    )


def _patched_pm(state):
    pm = MagicMock()
    pm.get_status.return_value = state
    return pm


class TestSingleSlotTrayFromState:
    def test_the_mid_print_tray_change_is_the_answer(self):
        """``Tray change during print: tray=3 at layer=0``. The printer
        announced the switch while the job was running, so it describes this
        print and no other."""
        state = _state(tray_change_log=[(3, 0)])
        assert _single_slot_tray_from_state(state, REPORTER_USAGE, 255) == (1, 3)

    def test_declines_a_multi_slot_print(self):
        """Every colour change moves ``tray_now``, so one tray reading can't be
        attributed to one slot. Same gate the internal writer uses."""
        usage = [
            {"slot_id": 1, "used_g": 10.0, "color": "#000000"},
            {"slot_id": 2, "used_g": 5.0, "color": "#FFFFFF"},
        ]
        assert _single_slot_tray_from_state(_state(tray_change_log=[(3, 0)]), usage, None) is None

    def test_declines_when_the_print_switched_trays(self):
        """Two entries means AMS backup swapped a spool mid-print (#957).
        ``report_usage`` splits those per segment; handing it a single-tray
        mapping instead would charge the whole print to one of them."""
        state = _state(tray_change_log=[(3, 0), (0, 120)])
        assert _single_slot_tray_from_state(state, REPORTER_USAGE, None) is None

    def test_slots_that_consumed_nothing_do_not_count_as_a_second_slot(self):
        """A purge-only slot is in the 3MF with zero grams. It is not a second
        filament and must not disqualify the print."""
        usage = [
            {"slot_id": 1, "used_g": 2.17, "color": "#000000"},
            {"slot_id": 2, "used_g": 0.0, "color": "#FFFFFF"},
        ]
        assert _single_slot_tray_from_state(_state(tray_change_log=[(3, 0)]), usage, None) == (1, 3)

    def test_falls_back_to_tray_now_at_start(self):
        state = _state(tray_change_log=[])
        assert _single_slot_tray_from_state(state, REPORTER_USAGE, 2) == (1, 2)

    def test_falls_back_to_current_tray_now(self):
        state = _state(tray_change_log=[], tray_now=2)
        assert _single_slot_tray_from_state(state, REPORTER_USAGE, 255) == (1, 2)

    def test_falls_back_to_last_loaded_tray(self):
        """The reporter's A1 parks ``tray_now`` at 255 the moment the print
        ends, and their ``tray_now_at_start`` was 255 too because print start
        fires before the filament is loaded. ``last_loaded_tray`` only ever
        latches real trays, so it is the one still holding the answer."""
        state = _state(tray_change_log=[], tray_now=255, last_loaded_tray=3)
        assert _single_slot_tray_from_state(state, REPORTER_USAGE, 255) == (1, 3)

    def test_255_is_not_a_tray(self):
        """255 is ``tray_now`` at rest and what an unparseable reading falls
        back to. Reading it as a slot would charge a spool on no evidence."""
        state = _state(tray_change_log=[], tray_now=255, last_loaded_tray=255)
        assert _single_slot_tray_from_state(state, REPORTER_USAGE, 255) is None

    def test_no_state_at_all(self):
        assert _single_slot_tray_from_state(None, REPORTER_USAGE, None) is None


class TestResolveSlotToTrayFallbackRung:
    def test_the_reporters_print_resolves_to_tray_3(self):
        """End of the chain: no mapping field, colours don't match, and the
        tray-change log settles it."""
        pm = _patched_pm(_state(tray_change_log=[(3, 0)], ams=REPORTER_AMS))

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, REPORTER_USAGE, 255)

        assert mapping == [3]
        assert source == "tray_state"

    def test_colour_match_still_wins(self):
        """When the slicer colour does equal a tray's, that is a direct
        statement about this slot and outranks a tray reading."""
        usage = [{"slot_id": 1, "used_g": 2.17, "color": "#5F4036"}]
        pm = _patched_pm(_state(tray_change_log=[(3, 0)], ams=REPORTER_AMS))

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, usage, 255)

        assert mapping == [2]
        assert source == "color_match"

    def test_mapping_field_still_wins(self):
        pm = _patched_pm(_state(tray_change_log=[(3, 0)], mapping=[0], ams=REPORTER_AMS))

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, REPORTER_USAGE, 255)

        assert mapping == [0]
        assert source == "mqtt"

    def test_an_empty_status_payload_still_reaches_the_tray_rung(self):
        """``tray_change_log`` lives on the state object, not in the status
        payload, so an empty payload must not short-circuit past it."""
        pm = _patched_pm(_state(tray_change_log=[(3, 0)]))

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, REPORTER_USAGE, None)

        assert mapping == [3]
        assert source == "tray_state"

    def test_only_the_used_slot_is_claimed(self):
        """A print whose one filament is slot 3 pads the array so the index
        lines up. The padding is -1, which no caller resolves: those slots
        consumed nothing."""
        usage = [
            {"slot_id": 1, "used_g": 0.0, "color": "#AAAAAA"},
            {"slot_id": 3, "used_g": 2.17, "color": "#000000"},
        ]
        pm = _patched_pm(_state(tray_change_log=[(3, 0)]))

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, usage, None)

        assert mapping == [-1, -1, 3]
        assert source == "tray_state"

    def test_still_says_none_when_the_printer_offers_nothing(self):
        pm = _patched_pm(_state(tray_change_log=[], tray_now=255, last_loaded_tray=-1, ams=REPORTER_AMS))

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, REPORTER_USAGE, 255)

        assert mapping is None
        assert source == "none"


class TestReportUsageChargesTheRightSpool:
    """The reporter's archive 12, end to end."""

    @staticmethod
    def _run(tracking, state, spool_by_tag, archive):
        rows = iter([tracking])

        def _next_row(*_args, **_kwargs):
            result = MagicMock()
            result.scalar_one_or_none.return_value = next(rows, archive)
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_next_row)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        client = AsyncMock()
        client.find_spool_by_tag = AsyncMock(side_effect=lambda tag: spool_by_tag.get(tag))
        client.use_spool = AsyncMock()

        pm = _patched_pm(state)

        async def _go():
            from backend.app.services.spoolman_tracking import report_usage

            with (
                patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
                patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
                patch(
                    "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                    AsyncMock(return_value=client),
                ),
                patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SER")),
                patch(
                    "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                    AsyncMock(return_value=None),
                ),
                patch("backend.app.services.printer_manager.printer_manager", pm),
            ):
                await report_usage(printer_id=1, archive_id=12)

        return _go, client

    @staticmethod
    def _tracking():
        return SimpleNamespace(
            filament_usage=list(REPORTER_USAGE),
            ams_trays={
                "0": {"tray_uuid": "TRAY0", "tag_uid": "", "tray_type": "PLA+"},
                "2": {"tray_uuid": "TRAY2", "tag_uid": "", "tray_type": "PLA"},
                "3": {"tray_uuid": "TRAY3", "tag_uid": "", "tray_type": "PLA+"},
            },
            slot_to_tray=None,
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
            tray_now_at_start=255,
        )

    SPOOLS = {
        # Spool 41 is the grey PLA+ that was wrongly charged 2.17 g.
        "TRAY0": {"id": 41, "filament": {"color_hex": "888888", "material": "PLA+"}},
        "TRAY2": {"id": 20, "filament": {"color_hex": "5F4036", "material": "PLA"}},
        "TRAY3": {"id": 46, "filament": {"color_hex": "111111", "material": "PLA+"}},
    }

    @pytest.mark.asyncio
    async def test_the_tray_the_printer_named_is_charged(self):
        archive = SimpleNamespace(filament_color="#000000", filament_type="PLA+")
        state = _state(tray_change_log=[(3, 0)], tray_now=255, last_loaded_tray=3, ams=REPORTER_AMS)

        run, client = self._run(self._tracking(), state, self.SPOOLS, archive)
        await run()

        client.use_spool.assert_awaited_once_with(46, 2.17)

    @pytest.mark.asyncio
    async def test_the_archive_is_not_restamped_from_a_positional_guess(self):
        """Nothing named a tray, so slot 1 is charged by position. The grams
        can be put back; overwriting what the slicer recorded cannot, so the
        archive keeps the colour and material it was printed with."""
        archive = SimpleNamespace(filament_color="#000000", filament_type="PLA+")
        state = _state(tray_change_log=[], tray_now=255, last_loaded_tray=-1, ams=REPORTER_AMS)

        run, client = self._run(self._tracking(), state, self.SPOOLS, archive)
        await run()

        client.use_spool.assert_awaited_once_with(41, 2.17)
        assert archive.filament_color == "#000000"
        assert archive.filament_type == "PLA+"

    @pytest.mark.asyncio
    async def test_a_resolved_mapping_still_restamps_the_archive(self):
        """#1494 and #2563 are unchanged when the mapping is actually known."""
        archive = SimpleNamespace(filament_color="#000000", filament_type="PLA+")
        state = _state(tray_change_log=[(3, 0)], tray_now=255, last_loaded_tray=3, ams=REPORTER_AMS)

        run, _client = self._run(self._tracking(), state, self.SPOOLS, archive)
        await run()

        assert archive.filament_color == "#111111"


class TestTraySplitIsNotAPositionalGuess:
    """A print that switched trays mid-run is attributed per segment from the
    tray-change log (#1793). That path never reads ``slot_to_tray``, so the
    absence of a mapping says nothing about it -- treating it as a positional
    guess would suppress the archive rewrite for the prints whose attribution
    is best supported, and log a warning naming a mechanism that did not run.
    """

    @pytest.mark.asyncio
    async def test_a_runout_switch_without_a_mapping_still_restamps_the_archive(self):
        from backend.app.services.spoolman_tracking import report_usage

        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 72.56, "type": "PLA+", "color": "#000000"}],
            ams_trays={
                "0": {"tray_uuid": "TRAY0", "tag_uid": "", "tray_type": "PLA+"},
                "3": {"tray_uuid": "TRAY3", "tag_uid": "", "tray_type": "PLA+"},
            },
            # The #2768 condition: a Studio print, so print start stored nothing.
            slot_to_tray=None,
            tray_remain_start=None,
            layer_usage={},
            filament_properties={},
            tray_now_at_start=255,
        )
        # The #1793 condition: AMS backup switched tray 0 -> tray 3 at layer 50.
        state = SimpleNamespace(
            raw_data={"ams": REPORTER_AMS},
            tray_change_log=[(0, 0), (3, 50)],
            total_layers=100,
            layer_num=100,
            tray_now=255,
            last_loaded_tray=3,
        )
        spools = {
            "TRAY0": {"id": 41, "filament": {"color_hex": "888888", "material": "PLA+"}},
            "TRAY3": {"id": 46, "filament": {"color_hex": "111111", "material": "PLA+"}},
        }
        archive = SimpleNamespace(filament_color="#000000", filament_type="PLA+")

        rows = iter([tracking])

        def _next_row(*_args, **_kwargs):
            result = MagicMock()
            result.scalar_one_or_none.return_value = next(rows, archive)
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_next_row)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        client = AsyncMock()
        client.find_spool_by_tag = AsyncMock(side_effect=lambda tag: spools.get(tag))
        client.use_spool = AsyncMock()

        with (
            patch("backend.app.services.spoolman_tracking.async_session", lambda: _AsyncCtx(db)),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
            patch(
                "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
                AsyncMock(return_value=client),
            ),
            patch("backend.app.services.spoolman_tracking._get_printer_serial", AsyncMock(return_value="SER")),
            patch(
                "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment",
                AsyncMock(return_value=None),
            ),
            patch("backend.app.services.printer_manager.printer_manager", _patched_pm(state)),
        ):
            await report_usage(printer_id=1, archive_id=12)

        # Both segments charged, so the split path is what ran.
        charged = {c.args[0] for c in client.use_spool.await_args_list}
        assert charged == {41, 46}

        # And the archive was rewritten from the spools the segments named,
        # rather than being left alone as a guess would be.
        assert archive.filament_color != "#000000"
