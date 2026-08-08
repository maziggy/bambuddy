"""Slot-to-tray mapping fallbacks on the Spoolman path (#2768).

Bambuddy only learns a print's slot-to-tray mapping at print start when it can
intercept the command on the printer's local MQTT request topic, or when the
print came from its own queue. A print dispatched from Bambu Studio while the
printer is cloud-bound satisfies neither: the command travels through Bambu's
broker, so ``ActivePrintSpoolman.slot_to_tray`` is NULL and every slot falls
through to a positional guess (slicer slot 1 to the first loaded tray, and so
on). The reporter's X1C was loaded out of slicer order, so all four slots were
charged to the wrong spool and the archive's filament was rewritten to match.

The internal-inventory writer never had this problem because it resolves the
mapping at completion, where it can read the printer's own ``mapping`` field or
colour-match the 3MF slots against the loaded trays. These tests cover giving
the Spoolman writer the same two fallbacks.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.spoolman_tracking import _resolve_slot_to_tray_fallback


class _AsyncCtx:
    """Minimal async context manager yielding a stub db session."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _state(**raw):
    return SimpleNamespace(raw_data=raw, layer_num=0, total_layers=0, tray_change_log=[])


def _patched_pm(state):
    pm = MagicMock()
    pm.get_status.return_value = state
    return pm


class TestResolveSlotToTrayFallback:
    def test_decodes_the_printers_own_mapping_field(self):
        """The reporter's X1C published mapping=[1, 3, 0, 32768] while their
        AMS was loaded out of slicer order. Snow-encoded, that is AMS 0 slot 2,
        AMS 0 slot 4, AMS 0 slot 1, and the AMS-HT — nothing like the
        positional [0, 1, 2, 3] the fallback-free path assumed."""
        pm = _patched_pm(_state(mapping=[1, 3, 0, 32768]))

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, [{"slot_id": 1, "color": "#FF0000"}])

        assert mapping == [1, 3, 0, 128]
        assert source == "mqtt"

    def test_colour_matches_when_the_printer_publishes_no_mapping(self):
        """A1/P1S/P2S never publish the mapping field. The 3MF's per-slot
        colours still identify the trays when each one is unambiguous."""
        pm = _patched_pm(
            _state(
                ams=[
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_color": "00FF00FF", "tray_type": "PLA"},
                            {"id": 1, "tray_color": "FF0000FF", "tray_type": "PLA"},
                        ],
                    }
                ]
            )
        )
        usage = [{"slot_id": 1, "color": "#FF0000"}, {"slot_id": 2, "color": "#00FF00"}]

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, usage)

        assert mapping == [1, 0]
        assert source == "color_match"

    def test_mapping_field_wins_over_colour_matching(self):
        """The printer's own field is direct evidence; colour matching is
        inference. When both are available the field decides."""
        pm = _patched_pm(
            _state(
                mapping=[3],
                ams=[{"id": 0, "tray": [{"id": 0, "tray_color": "FF0000FF", "tray_type": "PLA"}]}],
            )
        )

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, [{"slot_id": 1, "color": "#FF0000"}])

        assert mapping == [3]
        assert source == "mqtt"

    def test_reports_none_when_neither_fallback_answers(self):
        """Ambiguous colours and no mapping field: say so rather than invent
        one. The caller keeps the positional default, which is no worse than
        before, and the log names the reason."""
        pm = _patched_pm(
            _state(
                ams=[
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_color": "FF0000FF", "tray_type": "PLA"},
                            {"id": 1, "tray_color": "FF0000FF", "tray_type": "PLA"},
                        ],
                    }
                ]
            )
        )

        with patch("backend.app.services.printer_manager.printer_manager", pm):
            mapping, source = _resolve_slot_to_tray_fallback(1, [{"slot_id": 1, "color": "#FF0000"}])

        assert mapping is None
        assert source == "none"

    def test_reports_none_when_the_printer_is_offline(self):
        """No live state at completion — the printer dropped off after the
        print. Nothing to read, and no crash."""
        with patch("backend.app.services.printer_manager.printer_manager", _patched_pm(None)):
            mapping, source = _resolve_slot_to_tray_fallback(1, [{"slot_id": 1, "color": "#FF0000"}])

        assert mapping is None
        assert source == "none"


class TestReportUsageUsesTheFallback:
    """End-to-end through report_usage: the fallback has to reach
    ``_resolve_global_tray_id`` and change which spool is charged."""

    @staticmethod
    def _run(tracking, state, spool_by_tag, archive):
        # The first SELECT fetches the tracking row; every later one fetches the
        # archive for the colour / type rewrites (#1494, #2563).
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
                await report_usage(printer_id=1, archive_id=42)

        return _go, client

    @pytest.mark.asyncio
    async def test_mqtt_mapping_charges_the_tray_the_printer_named(self):
        """One-slot print whose filament actually came from AMS slot 4
        (global tray 3). With no stored mapping the positional default charges
        global tray 0 — the wrong spool, and the archive is then rewritten to
        that spool's colour. The printer's mapping field says otherwise."""
        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 25.0, "type": "PLA", "color": "#FF0000"}],
            ams_trays={
                "0": {"tray_uuid": "TRAY0UUID", "tag_uid": "", "tray_type": "PLA"},
                "3": {"tray_uuid": "TRAY3UUID", "tag_uid": "", "tray_type": "PLA"},
            },
            slot_to_tray=None,
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
        )
        state = _state(mapping=[3])
        spools = {
            "TRAY0UUID": {"id": 100, "filament": {"color_hex": "FFFFFF", "material": "PLA"}},
            "TRAY3UUID": {"id": 300, "filament": {"color_hex": "FF0000", "material": "PLA"}},
        }
        archive = SimpleNamespace(filament_color="#FF0000", filament_type="PLA")

        run, client = self._run(tracking, state, spools, archive)
        await run()

        client.use_spool.assert_awaited_once_with(300, 25.0)
        # And the visible half of the bug: the archive keeps the red it was
        # printed in instead of being rewritten to the wrong spool's white.
        assert archive.filament_color == "#FF0000"

    @pytest.mark.asyncio
    async def test_a_stored_mapping_is_never_second_guessed(self):
        """Print start captured the real ams_mapping (LAN print, or a Bambuddy
        queue job). That is the slicer's own instruction and outranks anything
        read back off the printer, whose mapping field may still describe an
        earlier job."""
        tracking = SimpleNamespace(
            filament_usage=[{"slot_id": 1, "used_g": 25.0, "type": "PLA", "color": "#FF0000"}],
            ams_trays={
                "0": {"tray_uuid": "TRAY0UUID", "tag_uid": "", "tray_type": "PLA"},
                "3": {"tray_uuid": "TRAY3UUID", "tag_uid": "", "tray_type": "PLA"},
            },
            slot_to_tray=[0],
            tray_remain_start=None,
            layer_usage=None,
            filament_properties=None,
        )
        state = _state(mapping=[3])
        spools = {
            "TRAY0UUID": {"id": 100, "filament": {"color_hex": "FFFFFF", "material": "PLA"}},
            "TRAY3UUID": {"id": 300, "filament": {"color_hex": "FF0000", "material": "PLA"}},
        }
        archive = SimpleNamespace(filament_color="#FF0000", filament_type="PLA")

        run, client = self._run(tracking, state, spools, archive)
        await run()

        client.use_spool.assert_awaited_once_with(100, 25.0)
