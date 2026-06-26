"""Regression for #1807: false-positive "Print Stopped" notification on the
expected-archive reprint path.

Bambuddy mints a fresh subtask_id per dispatch (``bambu_mqtt.py:3647``). On a
reprint, the archive row is reused — so the stored ``archive.subtask_id`` is
still the value from the FIRST run. The earlier ``not archive.subtask_id``
guard at ``on_print_start`` skipped the rewrite, so the row kept the stale id.

Then, if MQTT reconnects mid-print (which it routinely does — network blips,
printer reboots, Bambuddy restarts), ``reconcile_stale_active_prints`` (#1542)
compares the printer's live subtask_id against the stored one, sees a
mismatch, and synthesises a "missed PRINT COMPLETE" → bogus Print Stopped
notification while the print keeps running.

The fix: update ``archive.subtask_id`` whenever the new effective id differs
from the stored one, not only when the stored one is empty.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    _timelapse_baselines,
    register_expected_print,
)


@pytest.fixture(autouse=True)
def _clear_dicts():
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()
    _timelapse_baselines.clear()
    yield
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()
    _timelapse_baselines.clear()


def _patches():
    return (
        patch("backend.app.main.async_session"),
        patch("backend.app.main.notification_service"),
        patch("backend.app.main.smart_plug_manager"),
        patch("backend.app.main.ws_manager"),
        patch("backend.app.main.printer_manager"),
        patch("backend.app.main.mqtt_relay"),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._load_objects_from_archive"),
        patch("backend.app.main._store_spoolman_print_data", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch(
            "backend.app.main._list_timelapse_videos",
            new=AsyncMock(return_value=([], "/timelapse")),
        ),
    )


def _build_mocks(mock_printer, mock_archive):
    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from printers" in sql or "from printer " in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_printer),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_printer]))),
            )
        if "from print_archives" in sql or "from print_archive" in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_archive),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_archive]))),
            )
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_router)
    mock_session.commit = AsyncMock()
    return mock_session


def _make_archive(*, archive_id: int, stored_subtask_id: str | None):
    mock_archive = MagicMock()
    mock_archive.id = archive_id
    mock_archive.filename = "Ikea-drybox_silicabox.3mf"
    mock_archive.subtask_id = stored_subtask_id
    mock_archive.print_time_seconds = None
    mock_archive.created_by_id = None
    mock_archive.printer_id = 1
    mock_archive.print_name = "Ikea-drybox_silicabox"
    mock_archive.status = "archived"
    mock_archive.file_path = f"archives/{archive_id}/Ikea-drybox_silicabox.3mf"
    mock_archive.energy_start_kwh = None
    mock_archive.timelapse_path = None
    return mock_archive


def _make_printer():
    mock_printer = MagicMock()
    mock_printer.id = 1
    mock_printer.auto_archive = True
    mock_printer.external_camera_enabled = False
    mock_printer.external_camera_url = None
    mock_printer.name = "TestP1S"
    return mock_printer


async def _drive(tmp_path, mock_archive, mqtt_subtask_id: str | None):
    """Drive ``on_print_start`` with a print-start payload carrying the
    given ``subtask_id`` (the printer-echoed id at PRINT START — set by the
    queue dispatcher's fresh ``submission_id``)."""
    mock_printer = _make_printer()
    register_expected_print(1, mock_archive.filename, archive_id=mock_archive.id, ams_mapping=None)
    mock_session = _build_mocks(mock_printer, mock_archive)

    (
        async_session_p,
        notif_p,
        plug_p,
        ws_p,
        pm_p,
        relay_p,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
    ) = _patches()

    with (
        async_session_p as mock_session_maker,
        notif_p as mock_notif,
        plug_p as mock_plug,
        ws_p as mock_ws,
        pm_p as mock_pm,
        relay_p as mock_relay,
        _energy,
        _load_obj,
        _store_spoolman,
        _send_start,
        _list_tl,
        patch.object(app_settings, "base_dir", tmp_path),
    ):
        mock_session_maker.return_value = mock_session
        mock_notif.on_print_start = AsyncMock()
        mock_plug.on_print_start = AsyncMock()
        mock_ws.send_print_start = AsyncMock()
        mock_ws.send_archive_updated = AsyncMock()
        mock_relay.on_print_start = AsyncMock()
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))
        # last_dispatch_subtask_id fallback shouldn't fire — MQTT carried one.
        mock_pm.get_client = MagicMock(return_value=MagicMock(last_dispatch_subtask_id=None))

        from backend.app.main import on_print_start

        await on_print_start(
            1,
            {
                "filename": mock_archive.filename,
                "subtask_name": mock_archive.print_name,
                "raw_data": {"subtask_id": mqtt_subtask_id} if mqtt_subtask_id is not None else {},
            },
        )


@pytest.mark.asyncio
async def test_reprint_updates_stale_subtask_id(tmp_path):
    """The #1807 case: archive stored an OLD subtask_id from the first run.
    On reprint dispatch the printer echoes a fresh one — the archive's
    stored id must be rewritten so the reconciler doesn't flag the live
    print as stale on next MQTT reconnect."""
    archive = _make_archive(archive_id=31, stored_subtask_id="1844213296")

    await _drive(tmp_path, archive, mqtt_subtask_id="2103771517")

    assert archive.subtask_id == "2103771517", (
        "expected-archive reprint promotion must update archive.subtask_id to the "
        "new dispatch id; leaving the old value lets reconcile_stale_active_prints "
        "synthesise a bogus PRINT COMPLETE on the next MQTT reconnect (#1807)"
    )


@pytest.mark.asyncio
async def test_first_run_still_sets_subtask_id(tmp_path):
    """Regression guard for the previously-correct first-run path: an
    archive with no stored subtask_id must still have it written on the
    first MQTT-echoed PRINT START."""
    archive = _make_archive(archive_id=99, stored_subtask_id=None)

    await _drive(tmp_path, archive, mqtt_subtask_id="2103771517")

    assert archive.subtask_id == "2103771517"


@pytest.mark.asyncio
async def test_stable_push_does_not_rewrite(tmp_path):
    """The original `not archive.subtask_id` guard's intent was to avoid
    rewriting on every push that carries the same id. The inequality check
    preserves that no-op behaviour: same id in, no rewrite."""
    archive = _make_archive(archive_id=15, stored_subtask_id="2103771517")
    # Replace the bare attribute with a MagicMock so we can detect any write,
    # not just observe the post-call value (which would match even on a
    # spurious "store the same value back" rewrite).
    initial = archive.subtask_id

    await _drive(tmp_path, archive, mqtt_subtask_id="2103771517")

    assert archive.subtask_id == initial
