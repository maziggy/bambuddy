"""An X2D print from the right-hand external spool, sent without ams_mapping2 (#3405).

The X2D has two external holders and lists both in ``vt_tray``, loaded or not.
A print command that carries no ``ams_mapping2`` leaves the external spool as -1
in the flat mapping (#3166 resolves it only from ams_mapping2). Until #3166's
follow-up, Spoolman mode read that -1 as the first external holder present,
254, and charged the empty left holder; since then it charged nothing. The
built-in inventory drops a mapping that names no tray for any used slot and
lets the printer's ``tray_now`` decide. Spoolman mode now does the same, and
reads ``tray_now`` 255 as the right-hand holder on a printer that has one, as
the built-in inventory does.

Values are the reporter's ``/status``: 254 empty, 255 loaded with red PLA,
``tray_now`` 255.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import spoolman_tracking
from backend.app.services.spoolman_tracking import _single_slot_tray_from_state
from backend.app.services.usage_tracker import has_second_external_holder, mapping_names_no_used_tray

X2D_VT_TRAY = [
    {"id": 254, "tray_type": "", "tray_color": "00000000", "tray_info_idx": ""},
    {"id": 255, "tray_type": "PLA", "tray_color": "F72323FF", "tray_info_idx": "GFA01"},
]
USAGE = [{"slot_id": 1, "used_g": 12.5, "type": "PLA", "color": "#F72323"}]
RIGHT_HOLDER_SPOOL = 77


def _x2d_state(tray_now=255, vt_tray=X2D_VT_TRAY, last_loaded_tray=-1, tray_change_log=(), **raw):
    return SimpleNamespace(
        raw_data={"vt_tray": vt_tray, **raw},
        layer_num=0,
        total_layers=0,
        tray_change_log=list(tray_change_log),
        tray_now=tray_now,
        last_loaded_tray=last_loaded_tray,
    )


class TestTheRules:
    def test_a_mapping_of_only_minus_one_names_no_used_tray(self):
        assert mapping_names_no_used_tray([-1], USAGE)
        assert not mapping_names_no_used_tray([255], USAGE)
        # Another slot is mapped: the -1 is a slot the print does not feed.
        two = [*USAGE, {"slot_id": 2, "used_g": 3.0}]
        assert not mapping_names_no_used_tray([-1, 5], two)
        assert not mapping_names_no_used_tray(None, USAGE)

    def test_255_is_a_tray_only_on_a_printer_with_two_holders(self):
        assert has_second_external_holder({"vt_tray": X2D_VT_TRAY})
        assert not has_second_external_holder({"vt_tray": [X2D_VT_TRAY[0]]})
        assert not has_second_external_holder(None)

    def test_tray_now_255_is_the_right_holder(self):
        assert _single_slot_tray_from_state(_x2d_state(), USAGE, 255) == (1, 255)

    @pytest.mark.parametrize("stale", [3, 254])
    def test_the_right_holder_beats_a_tray_an_earlier_print_loaded(self, stale):
        """A print from 255 never updates last_loaded_tray: it still names the
        AMS tray, or the left holder, an earlier print used."""
        state = _x2d_state(last_loaded_tray=stale)
        assert _single_slot_tray_from_state(state, USAGE, 255) == (1, 255)

    def test_a_tray_loaded_during_the_print_still_wins_over_255(self):
        state = _x2d_state(last_loaded_tray=3, tray_change_log=[(3, 2)])
        assert _single_slot_tray_from_state(state, USAGE, 255) == (1, 3)

    def test_last_loaded_254_is_no_survivor(self):
        """Same cap as the built-in inventory: 0-253 only."""
        state = _x2d_state(vt_tray=[X2D_VT_TRAY[0]], last_loaded_tray=254)
        assert _single_slot_tray_from_state(state, USAGE, 255) is None

    def test_tray_now_255_on_a_one_holder_printer_is_nothing(self):
        assert _single_slot_tray_from_state(_x2d_state(vt_tray=[X2D_VT_TRAY[0]]), USAGE, 255) is None


class _AsyncCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _slot_assignment(_printer_id, ams_id, tray_id):
    # The reporter's spool, assigned to the holder it sits on: 255 is (255, 1).
    return RIGHT_HOLDER_SPOOL if (ams_id, tray_id) == (255, 1) else None


def _client():
    client = AsyncMock()
    client.find_spool_by_tag = AsyncMock(return_value=None)
    client.get_spool = AsyncMock(
        return_value={"id": RIGHT_HOLDER_SPOOL, "filament": {"color_hex": "F72323", "material": "PLA"}}
    )
    client.use_spool = AsyncMock()
    return client


def _patches(client, state, db):
    pm = MagicMock()
    pm.get_status.return_value = state
    return (
        patch.object(spoolman_tracking, "async_session", lambda: _AsyncCtx(db)),
        patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        patch.object(spoolman_tracking, "_get_spoolman_client_with_fallback", AsyncMock(return_value=client)),
        patch.object(spoolman_tracking, "_get_printer_serial", AsyncMock(return_value="SER")),
        patch.object(
            spoolman_tracking, "_resolve_spool_id_via_slot_assignment", AsyncMock(side_effect=_slot_assignment)
        ),
        patch("backend.app.services.printer_manager.printer_manager", pm),
    )


def _tracking(slot_to_tray):
    return SimpleNamespace(
        filament_usage=list(USAGE),
        ams_trays={
            "254": {"tray_uuid": "", "tag_uid": "", "tray_type": ""},
            "255": {"tray_uuid": "", "tag_uid": "", "tray_type": "PLA"},
        },
        slot_to_tray=slot_to_tray,
        tray_remain_start=None,
        layer_usage=None,
        filament_properties=None,
        tray_now_at_start=255,
        archive_id=12,
    )


class TestReportUsage:
    @staticmethod
    async def _run(slot_to_tray, state=None):
        archive = SimpleNamespace(filament_color="#F72323", filament_type="PLA")
        rows = iter([_tracking(slot_to_tray)])

        def _next_row(*_args, **_kwargs):
            result = MagicMock()
            result.scalar_one_or_none.return_value = next(rows, archive)
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_next_row)
        db.delete = AsyncMock()
        db.commit = AsyncMock()
        client = _client()
        p = _patches(client, state or _x2d_state(), db)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            await spoolman_tracking.report_usage(printer_id=1, archive_id=12)
        return client

    @pytest.mark.asyncio
    async def test_the_loaded_right_holder_is_charged(self):
        client = await self._run([-1])

        client.use_spool.assert_awaited_once_with(RIGHT_HOLDER_SPOOL, 12.5)

    @pytest.mark.asyncio
    async def test_a_same_coloured_ams_tray_is_not_charged_instead(self):
        """The colour match knows no external holder; the built-in inventory
        never runs it for a mapping that named no tray."""
        ams = [{"id": 0, "tray": [{"id": 0, "tray_color": "F72323FF", "tray_type": "PLA"}]}]
        client = await self._run([-1], state=_x2d_state(ams=ams))

        client.use_spool.assert_awaited_once_with(RIGHT_HOLDER_SPOOL, 12.5)

    @pytest.mark.asyncio
    async def test_a_resolved_mapping_is_unchanged(self):
        """With ams_mapping2 the captured mapping already says 255 (#3166)."""
        client = await self._run([255])

        client.use_spool.assert_awaited_once_with(RIGHT_HOLDER_SPOOL, 12.5)


class TestPartialUsage:
    @pytest.mark.asyncio
    async def test_an_aborted_print_charges_the_right_holder_too(self):
        tracking = _tracking([-1])
        tracking.layer_usage = {}
        # A same-coloured AMS tray must not take it either, as on completion.
        ams = [{"id": 0, "tray": [{"id": 0, "tray_color": "F72323FF", "tray_type": "PLA"}]}]
        state = _x2d_state(ams=ams)
        state.layer_num, state.total_layers = 50, 100
        report = AsyncMock(return_value=1)
        p = _patches(_client(), state, AsyncMock())
        with (
            p[0],
            p[1],
            p[2],
            p[3],
            p[4],
            p[5],
            patch.object(spoolman_tracking, "_report_spool_usage_for_slots", report),
        ):
            await spoolman_tracking._report_partial_usage(1, tracking, last_progress=50)

        report.assert_awaited()
        slot_to_tray = report.await_args.args[3]
        assert spoolman_tracking._resolve_global_tray_id(1, slot_to_tray, {254: {}, 255: {}}) == 255
