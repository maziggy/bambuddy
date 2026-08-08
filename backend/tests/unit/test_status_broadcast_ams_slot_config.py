"""Configuring an AMS slot must reach the printer card without a page reload.

`on_printer_status_change` deduplicates WebSocket broadcasts against a
`status_key`. Its AMS component used to carry only id / tray_type / state, so
re-configuring a slot to a different brand or colour of the SAME material
produced an identical key: the printer's pushall arrived with the new values,
the handler compared, found no change, and returned without broadcasting. The
card then showed the old filament until the 30s fallback poll or an F5.

Reset never had the bug — it clears tray_type, which was always in the key.
That asymmetry is what these tests pin: every field Configure Slot writes has
to move the key, and the fields that churn every second still must not.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app import main as main_module


def _spawn_patch():
    """Close the reconcile coroutine the handler builds as a call argument.

    Same reason as test_printer_offline_notification.py: a bare MagicMock keeps
    it alive in call_args and it finalises unawaited during a later test's GC.
    """
    return patch(
        "backend.app.main.spawn_background_task",
        side_effect=lambda coro, **kwargs: coro.close(),
    )


def _tray(**overrides) -> dict:
    """One AMS tray as the firmware reports it, mid-way through a print job.

    Defaults describe a configured slot: Bambu PLA Basic in black, bound to
    calibration slot 3.
    """
    tray = {
        "id": "0",
        "tray_type": "PLA",
        "state": 10,
        "tray_color": "000000FF",
        "tray_info_idx": "GFA00",
        "tray_sub_brands": "PLA Basic",
        "cali_idx": 3,
        "remain": 42,
    }
    tray.update(overrides)
    return tray


def _state(trays: list[dict]) -> SimpleNamespace:
    """Minimal PrinterState stub carrying one AMS unit.

    Idle and unheated, so the handler runs straight from the dedup check to the
    broadcast without touching progress milestones, HMS notifications or the DB.
    """
    return SimpleNamespace(
        connected=True,
        state="IDLE",
        progress=0,
        layer_num=0,
        temperatures={},
        raw_data={"ams": [{"id": "0", "dry_time": 0, "tray": trays}]},
        stg_cur=0,
        cooling_fan_speed=0,
        big_fan1_speed=0,
        big_fan2_speed=0,
        chamber_light="",
        active_extruder=0,
        tray_now=0,
        door_open=False,
        subtask_name="",
        gcode_file="",
        remaining_time=None,
        hms_errors=[],
        ams_filament_backup=None,
    )


@pytest.fixture(autouse=True)
def _reset_edge_state():
    main_module._last_status_broadcast.clear()
    main_module._printer_last_connected.clear()
    main_module._printer_reconciled_since_connect.clear()
    yield
    main_module._last_status_broadcast.clear()
    main_module._printer_last_connected.clear()
    main_module._printer_reconciled_since_connect.clear()


async def _push(ws_mgr, trays: list[dict]) -> None:
    """Deliver one status push to the handler."""
    relay = MagicMock()
    relay.on_printer_status = AsyncMock()
    pm = MagicMock()
    pm.get_printer.return_value = None  # Skip the relay payload branch.
    pm.get_model.return_value = ""

    with (
        patch("backend.app.main.ws_manager", ws_mgr),
        patch("backend.app.main.mqtt_relay", relay),
        patch("backend.app.main.printer_manager", pm),
        _spawn_patch(),
        patch("backend.app.main.printer_state_to_dict", return_value={}),
    ):
        await main_module.on_printer_status_change(1, _state(trays))


@pytest.fixture
def ws_mgr():
    mgr = MagicMock()
    mgr.send_printer_status = AsyncMock()
    return mgr


class TestConfigureSlotBroadcasts:
    """Each field Configure Slot writes must break the dedup on its own —
    the user may change only the colour, or only the K-profile."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field,new_value",
        [
            ("tray_color", "FF0000FF"),
            ("tray_info_idx", "GFA01"),
            ("tray_sub_brands", "PLA Matte"),
            ("cali_idx", 7),
        ],
    )
    async def test_a_changed_filament_field_broadcasts(self, ws_mgr, field, new_value):
        await _push(ws_mgr, [_tray()])
        assert ws_mgr.send_printer_status.await_count == 1

        await _push(ws_mgr, [_tray(**{field: new_value})])

        assert ws_mgr.send_printer_status.await_count == 2, (
            f"changing {field} did not reach the frontend — the card would keep "
            "showing the old filament until the fallback poll"
        )

    @pytest.mark.asyncio
    async def test_the_realistic_reconfigure_broadcasts(self, ws_mgr):
        """Black Bambu PLA Basic → red eSUN PLA+ with its own K-profile.

        The whole point of the report: same material, so every field the old key
        looked at is unchanged.
        """
        await _push(ws_mgr, [_tray()])

        await _push(
            ws_mgr,
            [
                _tray(
                    tray_color="C1121FFF",
                    tray_info_idx="GFL99",
                    tray_sub_brands="eSUN PLA+",
                    cali_idx=5,
                )
            ],
        )

        assert ws_mgr.send_printer_status.await_count == 2

    @pytest.mark.asyncio
    async def test_a_second_slot_is_watched_too(self, ws_mgr):
        """The key spans every tray, so configuring slot 2 must broadcast even
        though slot 1 is untouched."""
        trays = [_tray(id="0"), _tray(id="1", tray_type="PETG", tray_info_idx="GFG00")]
        await _push(ws_mgr, trays)

        changed = [_tray(id="0"), _tray(id="1", tray_type="PETG", tray_info_idx="GFG01")]
        await _push(ws_mgr, changed)

        assert ws_mgr.send_printer_status.await_count == 2


class TestDedupStillHolds:
    """The dedup exists to keep a printing machine from flooding the socket.
    Widening the key must not have cost that."""

    @pytest.mark.asyncio
    async def test_an_identical_push_is_still_suppressed(self, ws_mgr):
        await _push(ws_mgr, [_tray()])
        await _push(ws_mgr, [_tray()])

        assert ws_mgr.send_printer_status.await_count == 1

    @pytest.mark.asyncio
    async def test_remaining_filament_does_not_broadcast(self, ws_mgr):
        """`remain` ticks down throughout a print and is deliberately absent
        from the key. It sits in the same tray dict as the fields we added, so
        this pins that we widened the key rather than hashing the whole tray."""
        await _push(ws_mgr, [_tray(remain=42)])
        await _push(ws_mgr, [_tray(remain=41)])

        assert ws_mgr.send_printer_status.await_count == 1


class TestExistingBehaviourUnchanged:
    """The cases that already worked, kept working."""

    @pytest.mark.asyncio
    async def test_a_load_unload_transition_still_broadcasts(self, ws_mgr):
        """#784 — tray state 11→10."""
        await _push(ws_mgr, [_tray(state=11)])
        await _push(ws_mgr, [_tray(state=10)])

        assert ws_mgr.send_printer_status.await_count == 2

    @pytest.mark.asyncio
    async def test_resetting_a_slot_still_broadcasts(self, ws_mgr):
        """Reset clears the filament identity outright."""
        await _push(ws_mgr, [_tray()])
        await _push(
            ws_mgr,
            [_tray(tray_type="", tray_color="", tray_info_idx="", tray_sub_brands="", cali_idx=-1)],
        )

        assert ws_mgr.send_printer_status.await_count == 2

    @pytest.mark.asyncio
    async def test_a_printer_with_no_ams_still_broadcasts_once(self, ws_mgr):
        """The `else ()` branch — an AMS-less printer must not crash or
        double-broadcast."""
        relay = MagicMock()
        relay.on_printer_status = AsyncMock()
        pm = MagicMock()
        pm.get_printer.return_value = None
        pm.get_model.return_value = ""
        state = _state([])
        state.raw_data = {}

        for _ in range(2):
            with (
                patch("backend.app.main.ws_manager", ws_mgr),
                patch("backend.app.main.mqtt_relay", relay),
                patch("backend.app.main.printer_manager", pm),
                _spawn_patch(),
                patch("backend.app.main.printer_state_to_dict", return_value={}),
            ):
                await main_module.on_printer_status_change(1, state)

        assert ws_mgr.send_printer_status.await_count == 1
