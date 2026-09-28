"""The 1-5 humidity index is never shown or stored as a percentage (#3140).

Bambu sends ``humidity_raw`` (relative humidity, percent) and ``humidity`` (a
1-5 drop index). The index runs the other way -- OpenBambuAPI's push_info
sample pairs ``humidity:30%`` with ``humidity_idx:4`` -- so substituting one
for the other inverts the reading rather than approximating it. A unit sending
only the index used to render as "2%" in the good/green band while being the
second-wettest of the five steps, chart an average of index values as a
percentage, and sit under every alarm threshold forever.

The reporting install ran X1Plus, which Bambuddy does not support, and no
stock-firmware printer is on record as sending the index alone. The guard is
kept regardless because it is about what we do when the field is missing for
any reason, and showing a number we cannot interpret is worse than showing
none: every consumer of these serializers already handles ``None`` by hiding
the indicator, skipping the unit or leaving a gap in the chart.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import backend.app.main as main
from backend.app.api.routes.printers import get_printer_status
from backend.app.models.ams_history import AMSSensorHistory
from backend.app.models.printer import Printer
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_manager import printer_state_to_dict
from backend.app.utils.ams_humidity import ams_humidity_percent

# --- the reading itself ---


def test_a_reported_percentage_is_the_reading():
    assert ams_humidity_percent({"humidity_raw": 45}) == 45.0
    assert ams_humidity_percent({"humidity_raw": "45"}) == 45.0


def test_a_fractional_percentage_survives():
    """``int("16.5")`` raised, which sent the old code to the index fallback --
    so a sensor reporting one decimal place read as a drop index."""
    assert ams_humidity_percent({"humidity_raw": "16.5"}) == 16.5


def test_the_index_alone_is_not_a_reading():
    """The guard. Index 2 is the second-wettest step, and 2% is as dry as a
    unit can read -- the two are not interchangeable in either direction."""
    assert ams_humidity_percent({"humidity": 2}) is None
    assert ams_humidity_percent({"humidity": "2"}) is None


def test_the_percentage_wins_when_both_are_present():
    assert ams_humidity_percent({"humidity": 4, "humidity_raw": "62"}) == 62.0


def test_a_genuine_zero_is_a_reading():
    """Not ``None``: the history writer used to test truthiness, so a unit
    reading 0% stored NULL while the same pass wrote 0.0 to the other column."""
    assert ams_humidity_percent({"humidity_raw": 0}) == 0.0
    assert ams_humidity_percent({"humidity_raw": "0"}) == 0.0


def test_an_unparseable_percentage_is_not_a_licence_to_use_the_index():
    assert ams_humidity_percent({"humidity_raw": "n/a", "humidity": 3}) is None
    assert ams_humidity_percent({"humidity_raw": None, "humidity": 3}) is None


def test_a_unit_that_is_not_a_mapping_reads_as_no_unit():
    assert ams_humidity_percent(None) is None
    assert ams_humidity_percent("ams0") is None


# --- what the two serializers of the same card report ---


def _index_only_unit() -> dict:
    return {
        "ams": [
            {
                "id": 0,
                "humidity": "2",  # index, no humidity_raw
                "temp": "24.0",
                "tray": [{"id": 0, "tray_type": "PLA"}],
            }
        ]
    }


def test_the_websocket_serializer_reports_no_humidity_for_an_index_only_unit():
    result = printer_state_to_dict(PrinterState(connected=True, state="IDLE", raw_data=_index_only_unit()))

    assert result["ams"][0]["humidity"] is None
    assert result["ams"][0]["temp"] == "24.0"  # the other sensor is unaffected


@pytest.mark.asyncio
async def test_the_rest_serializer_reports_no_humidity_for_an_index_only_unit(db_session):
    """The two serializers feed the same card and must not answer differently."""
    printer = Printer(name="X1C", serial_number="S-3140", ip_address="1.1.1.1", access_code="c", model="X1C")
    db_session.add(printer)
    await db_session.commit()

    state = PrinterState(connected=True, state="IDLE", raw_data=_index_only_unit())

    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = state
        mock_pm.get_drying_targets.return_value = {}
        status = await get_printer_status(printer.id, db=db_session)

    assert status.ams[0].humidity is None


@pytest.mark.asyncio
async def test_the_rest_serializer_still_reports_a_percentage(db_session):
    """The guard must not cost the supported case its reading."""
    printer = Printer(name="H2D", serial_number="S-3140b", ip_address="1.1.1.2", access_code="c", model="H2D")
    db_session.add(printer)
    await db_session.commit()

    raw = _index_only_unit()
    raw["ams"][0]["humidity_raw"] = "38"
    state = PrinterState(connected=True, state="IDLE", raw_data=raw)

    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = state
        mock_pm.get_drying_targets.return_value = {}
        status = await get_printer_status(printer.id, db=db_session)

    assert status.ams[0].humidity == 38


# --- what the recorder writes, and what it alarms on ---


def _state_with(unit: dict) -> PrinterState:
    return PrinterState(connected=True, state="IDLE", raw_data={"ams": [unit]})


async def _run_one_pass(test_engine, unit: dict):
    """One pass of record_ams_history against a single AMS unit.

    Same shape as test_ams_temp_alarm_dispatch_2905: the loop is a no-arg
    infinite task, so a fake sleep that recognises its own intervals runs
    exactly one pass and then cancels it.
    """
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        if seconds == 10:  # startup wait before the first pass
            return
        if seconds == main.AMS_HISTORY_INTERVAL:  # pass finished cleanly
            raise asyncio.CancelledError
        if seconds == 60:  # the loop's own except-Exception backoff
            raise AssertionError("record_ams_history raised; check the warning log")
        await real_sleep(seconds)

    service = MagicMock()
    service.on_ams_humidity_high = AsyncMock()
    service.on_ams_ht_humidity_high = AsyncMock()
    service.on_ams_temperature_high = AsyncMock()
    service.on_ams_ht_temperature_high = AsyncMock()

    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    cooldown_before = dict(main._ams_alarm_cooldown)
    counter_before = main._ams_cleanup_counter
    main._ams_alarm_cooldown.clear()
    try:
        with (
            patch.object(main, "async_session", maker),
            patch.object(main, "notification_service", service),
            patch.object(main.printer_manager, "get_status", return_value=_state_with(unit)),
            patch.object(main.asyncio, "sleep", fake_sleep),
        ):
            await main.record_ams_history()
    finally:
        main._ams_alarm_cooldown.clear()
        main._ams_alarm_cooldown.update(cooldown_before)
        main._ams_cleanup_counter = counter_before
    return service


async def _rows(db_session, printer_id: int) -> list[AMSSensorHistory]:
    result = await db_session.execute(select(AMSSensorHistory).where(AMSSensorHistory.printer_id == printer_id))
    return list(result.scalars().all())


async def _printer(db_session, serial: str) -> Printer:
    printer = Printer(name="X1C", serial_number=serial, ip_address="1.1.1.1", access_code="c", model="X1C")
    db_session.add(printer)
    await db_session.commit()
    return printer


@pytest.mark.asyncio
async def test_an_index_only_unit_charts_a_gap_not_a_percentage(db_session, test_engine):
    """The temperature is still worth recording, so the row is written -- but
    with no humidity, which the chart draws as a gap rather than as a flat 2%
    line in the good band."""
    printer = await _printer(db_session, "S-3140c")

    service = await _run_one_pass(
        test_engine,
        {"id": 0, "humidity": "2", "temp": "24.0", "tray_exist_bits": "1", "tray": [{"tray_type": "PLA"}]},
    )

    rows = await _rows(db_session, printer.id)
    assert len(rows) == 1
    assert rows[0].humidity is None
    assert rows[0].humidity_raw is None
    assert rows[0].temperature == 24.0
    assert service.on_ams_humidity_high.await_count == 0


@pytest.mark.asyncio
async def test_an_index_only_unit_says_so_in_the_log_once(db_session, test_engine, caplog):
    """A blank humidity field on a supported printer would otherwise be silent.

    Nothing on record says a supported printer sends the index alone, and the
    guard makes such a unit stop reporting humidity entirely -- so it names
    itself in the log, once per unit, rather than leaving the card blank with
    no explanation anywhere.
    """
    printer = await _printer(db_session, "S-3140f")
    main._ams_index_only_logged.clear()
    unit = {"id": 0, "humidity": "2", "temp": "24.0", "tray_exist_bits": "1", "tray": [{"tray_type": "PLA"}]}

    try:
        with caplog.at_level(logging.INFO, logger="backend.app.main"):
            await _run_one_pass(test_engine, unit)
            await _run_one_pass(test_engine, unit)
    finally:
        main._ams_index_only_logged.clear()

    lines = [r.getMessage() for r in caplog.records if "humidity index" in r.getMessage()]
    assert len(lines) == 1
    assert printer.name in lines[0]
    assert "#3140" in lines[0]


@pytest.mark.asyncio
async def test_a_unit_reporting_no_humidity_at_all_is_not_logged(db_session, test_engine, caplog):
    """The note is about a unit whose index we are declining to use. A unit
    that sends neither field is not new and has nothing to report."""
    await _printer(db_session, "S-3140g")
    main._ams_index_only_logged.clear()

    try:
        with caplog.at_level(logging.INFO, logger="backend.app.main"):
            await _run_one_pass(
                test_engine,
                {"id": 0, "temp": "24.0", "tray_exist_bits": "1", "tray": [{"tray_type": "PLA"}]},
            )
    finally:
        main._ams_index_only_logged.clear()

    assert not [r for r in caplog.records if "humidity index" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_zero_percent_reading_is_stored_in_both_columns(db_session, test_engine):
    """``float(raw) if raw else None`` stored NULL for a genuine 0%, while the
    same pass wrote 0.0 to ``humidity`` -- one row disagreeing with itself.

    Numeric 0, not "0": the truthiness test only swallowed the reading when the
    firmware sent the value as a number, which is how the defect survived the
    string-valued samples every other test here uses."""
    printer = await _printer(db_session, "S-3140d")

    await _run_one_pass(
        test_engine,
        {
            "id": 0,
            "humidity": "5",
            "humidity_raw": 0,
            "temp": "24.0",
            "tray_exist_bits": "1",
            "tray": [{"tray_type": "PLA"}],
        },
    )

    rows = await _rows(db_session, printer.id)
    assert len(rows) == 1
    assert rows[0].humidity == 0.0
    assert rows[0].humidity_raw == 0.0


@pytest.mark.asyncio
async def test_a_reported_percentage_still_alarms(db_session, test_engine):
    """The supported path, asserted alongside the guard so a regression that
    silenced every humidity alarm could not pass as the fix."""
    printer = await _printer(db_session, "S-3140e")

    service = await _run_one_pass(
        test_engine,
        {
            "id": 0,
            "humidity": "1",
            "humidity_raw": "80",
            "temp": "24.0",
            "tray_exist_bits": "1",
            "tray": [{"tray_type": "PLA"}],
        },
    )

    service.on_ams_humidity_high.assert_awaited_once()
    assert service.on_ams_humidity_high.await_args.args[3] == 80.0
    rows = await _rows(db_session, printer.id)
    assert rows[0].humidity == 80.0
