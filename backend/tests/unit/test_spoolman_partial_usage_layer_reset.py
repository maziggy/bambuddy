"""Spoolman partial usage of an aborted print whose printer reset its layer to 0.

The firmware zeroes ``layer_num`` on cancel. The layer captured during the
print then decides how far the print got; without it the abort reports no
usage at all (the progress estimate needs a total layer count, which an
aborted print may not have either).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import spoolman_tracking


@pytest.mark.asyncio
@pytest.mark.parametrize(("last_layer_num", "charged_at"), [(12, 12), (None, None)])
async def test_the_captured_layer_stands_in_for_the_reset_one(last_layer_num, charged_at):
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    tracking = SimpleNamespace(
        layer_usage={"12": {"0": 500.0}},
        filament_properties={},
        filament_usage=[{"slot_id": 1, "used_g": 10.0}],
        ams_trays={},
        slot_to_tray=[0],
        tray_remain_start={},
        archive_id=1,
        tray_now_at_start=0,
    )
    cumulative = MagicMock(return_value={})
    status = SimpleNamespace(layer_num=0, total_layers=None, raw_data={}, tray_change_log=[])

    with (
        patch.object(spoolman_tracking, "async_session", session),
        patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=status)),
        patch.object(spoolman_tracking, "_get_printer_serial", AsyncMock(return_value="SERIAL")),
        patch.object(spoolman_tracking, "_get_spoolman_client_with_fallback", AsyncMock(return_value=MagicMock())),
        patch("backend.app.utils.threemf_tools.get_cumulative_usage_at_layer", cumulative),
    ):
        await spoolman_tracking._report_partial_usage(1, tracking, last_layer_num=last_layer_num)

    if charged_at is None:
        cumulative.assert_not_called()
    else:
        assert cumulative.call_args.args[1] == charged_at
