"""The temperature alarm reads ams_temp_alarm, end to end (issue #2905).

``test_ams_alarm_gating`` covers the pieces -- the resolution, the latch's
release check -- but not the thing that consumes them. ``record_ams_history``
is a no-arg infinite task, which is why the gates already there are tested
through their extracted helpers instead, and why #2943 said the wiring itself
was verified by reading rather than by a test.

It can be driven. The loop exits cleanly on ``asyncio.CancelledError``, so a
fake ``asyncio.sleep`` that recognises the loop's own intervals runs exactly
one pass and stops: skip the 10 s startup wait, raise at the 300 s
end-of-pass wait. The 60 s wait belongs to the loop's ``except Exception``
handler, so intercepting that too turns a swallowed error into a failure with
a message rather than a test that quietly asserts nothing.

What that buys is the only coverage of the three call sites together: which
number decides that the alarm fires, and which number the notification quotes.
Those were separate values before this change and a regression that reverted
either one would leave every test in the other file passing.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import backend.app.main as main
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings


def _ams_state_at(temperature: float):
    """A connected printer with one loaded AMS reading *temperature*.

    ``tray_exist_bits`` matters: the #1619 empty-unit skip drops the alarm
    before any threshold is consulted, so a unit with no filament would make
    every assertion below vacuously pass.
    """
    state = MagicMock()
    state.connected = True
    state.raw_data = {
        "ams": [
            {
                "id": 0,
                "temp": str(temperature),
                "humidity": "5",
                "humidity_raw": "39",  # inside the good band — no humidity alarm
                "tray_exist_bits": "1",
                "tray": [{"tray_type": "PLA"}],
            }
        ]
    }
    return state


async def _run_one_pass(test_engine, temperature: float):
    """Run record_ams_history exactly once and return the mocked service."""
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
    service.on_ams_temperature_high = AsyncMock()
    service.on_ams_ht_temperature_high = AsyncMock()
    service.on_ams_humidity_high = AsyncMock()

    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    cooldown_before = dict(main._ams_alarm_cooldown)
    counter_before = main._ams_cleanup_counter
    main._ams_alarm_cooldown.clear()
    try:
        with (
            patch.object(main, "async_session", maker),
            patch.object(main, "notification_service", service),
            patch.object(main.printer_manager, "get_status", return_value=_ams_state_at(temperature)),
            patch.object(main.asyncio, "sleep", fake_sleep),
        ):
            await main.record_ams_history()
    finally:
        main._ams_alarm_cooldown.clear()
        main._ams_alarm_cooldown.update(cooldown_before)
        main._ams_cleanup_counter = counter_before
    return service


async def _printer(db) -> Printer:
    printer = Printer(name="X2D", serial_number="S-2905", ip_address="1.1.1.1", access_code="c", model="X2D")
    db.add(printer)
    await db.commit()
    return printer


async def _set_alarm(db, value: str) -> None:
    db.add(Settings(key="ams_temp_alarm", value=value))
    await db.commit()


@pytest.mark.asyncio
async def test_an_install_that_never_set_one_alarms_at_the_display_band(db_session, test_engine):
    """The upgrade path, which is what makes this shippable without a migration.

    No ams_temp_alarm row: the alarm has to fire where it always did, and quote
    the number it always quoted.
    """
    await _printer(db_session)

    service = await _run_one_pass(test_engine, temperature=50.0)

    service.on_ams_temperature_high.assert_awaited_once()
    assert service.on_ams_temperature_high.await_args.args[4] == 35.0


@pytest.mark.asyncio
async def test_a_set_threshold_is_both_what_fires_and_what_the_message_quotes(db_session, test_engine):
    """The two call sites, asserted together.

    A change that fixed the comparison and left the reported value behind would
    send "50 °C > 35 °C" while the user had asked for 45 — which reads as the
    old bug rather than as a working alarm.
    """
    await _printer(db_session)
    await _set_alarm(db_session, "45")

    service = await _run_one_pass(test_engine, temperature=50.0)

    service.on_ams_temperature_high.assert_awaited_once()
    temperature, threshold = service.on_ams_temperature_high.await_args.args[3:5]
    assert (temperature, threshold) == (50.0, 45.0)


@pytest.mark.asyncio
async def test_ambient_room_heat_below_the_alarm_threshold_sends_nothing(db_session, test_engine):
    """#2905 itself: 37.7 C in a room without air conditioning, alarm at 45.

    That reading is the one from the report, an hour apart, twice. Nothing is
    heating and nothing is wrong, so nothing should be sent.
    """
    await _printer(db_session)
    await _set_alarm(db_session, "45")

    service = await _run_one_pass(test_engine, temperature=37.7)

    assert service.on_ams_temperature_high.await_count == 0


@pytest.mark.asyncio
async def test_clearing_the_field_restores_the_old_behaviour(db_session, test_engine):
    """What the settings page writes when the input is emptied is the literal
    string "None", not a missing row. It has to land back on the fair value."""
    await _printer(db_session)
    await _set_alarm(db_session, "None")

    service = await _run_one_pass(test_engine, temperature=50.0)

    service.on_ams_temperature_high.assert_awaited_once()
    assert service.on_ams_temperature_high.await_args.args[4] == 35.0


@pytest.mark.asyncio
async def test_an_unusable_value_falls_back_instead_of_silencing_the_alarm(db_session, test_engine):
    """A stored "nan" parses as a float, and nothing is ever greater than NaN.

    Without the isfinite guard the alarm would go permanently quiet — the
    failure mode that looks exactly like a working configuration.
    """
    await _printer(db_session)
    await _set_alarm(db_session, "nan")

    service = await _run_one_pass(test_engine, temperature=50.0)

    service.on_ams_temperature_high.assert_awaited_once()
    assert service.on_ams_temperature_high.await_args.args[4] == 35.0
