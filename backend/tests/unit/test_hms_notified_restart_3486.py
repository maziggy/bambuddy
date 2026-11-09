"""HMS notifications survive a restart without being sent again (#3486).

Printers keep faults in ``hms[]`` long after they happened. The set of faults
already notified lived only in memory, so every Bambuddy restart sent each
still-listed fault once more. And the title's category came from a module table
that called system faults "Nozzle/Extruder" and AMS Lite faults "Chamber".
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.main import (
    _hms_error_title,
    _load_notified_hms_faults,
    _take_new_hms_faults_and_save,
)
from backend.app.models.hms_notified_fault import HmsNotifiedFault
from backend.app.models.printer import Printer


def _fault(full_code: str):
    return SimpleNamespace(
        full_code=full_code,
        attr=int(full_code[:8], 16),
        code=f"0x{full_code[8:]}",
        severity=2,
        description="Described.",
        actions=[],
    )


TOOLHEAD = _fault("0300010000010008")
POWER_LOSS = _fault("0500020000020001")


@pytest.fixture
async def printer_id(db_session):
    printer = Printer(name="P", serial_number="00M09A000000001", ip_address="192.168.1.100", access_code="12345678")
    db_session.add(printer)
    await db_session.commit()
    pid = printer.id
    main_module._notified_hms_errors.pop(pid, None)
    main_module._hms_print_state.pop(pid, None)
    main_module._hms_forget_when_gone.pop(pid, None)
    yield pid
    main_module._notified_hms_errors.pop(pid, None)
    main_module._hms_print_state.pop(pid, None)
    main_module._hms_forget_when_gone.pop(pid, None)


@pytest.fixture(autouse=True)
def app_session(test_engine):
    """The service opens its own sessions; point them at the test database."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("backend.app.services.hms_notified.async_session", maker):
        yield


def _restart(printer_id: int) -> None:
    """What a restart does to the in-memory tracking."""
    main_module._notified_hms_errors.pop(printer_id, None)
    main_module._hms_print_state.pop(printer_id, None)
    main_module._hms_forget_when_gone.pop(printer_id, None)


async def _stored(db_session, printer_id: int) -> set[str]:
    rows = await db_session.execute(select(HmsNotifiedFault.fault_key).where(HmsNotifiedFault.printer_id == printer_id))
    return set(rows.scalars())


@pytest.mark.asyncio
class TestRestart:
    async def test_a_fault_still_listed_is_not_sent_again(self, printer_id):
        assert await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD, POWER_LOSS], "RUNNING") == [
            TOOLHEAD,
            POWER_LOSS,
        ]

        _restart(printer_id)
        await _load_notified_hms_faults()

        assert await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD, POWER_LOSS], "RUNNING") == []

    async def test_a_new_fault_after_the_restart_is_still_sent(self, printer_id):
        await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD], "RUNNING")

        _restart(printer_id)
        await _load_notified_hms_faults()

        assert await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD, POWER_LOSS], "RUNNING") == [POWER_LOSS]

    async def test_without_the_stored_set_a_restart_sends_again(self, printer_id):
        # The failure itself: nothing loaded, everything listed is new.
        await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD], "RUNNING")

        _restart(printer_id)

        assert await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD], "RUNNING") == [TOOLHEAD]

    async def test_a_forgotten_fault_leaves_the_stored_set(self, printer_id, db_session):
        await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD, POWER_LOSS], "RUNNING")
        assert await _stored(db_session, printer_id) == {"0300010000010008", "0500020000020001"}

        # A print state change forgets a fault as soon as it is gone.
        await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD], "FINISH")

        db_session.expire_all()
        assert await _stored(db_session, printer_id) == {"0300010000010008"}

        _restart(printer_id)
        await _load_notified_hms_faults()
        assert await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD, POWER_LOSS], "FINISH") == [POWER_LOSS]

    async def test_a_failed_write_does_not_hold_back_the_notification(self, printer_id):
        with patch(
            "backend.app.services.hms_notified.save_notified_hms_faults",
            AsyncMock(side_effect=RuntimeError("database is locked")),
        ):
            assert await _take_new_hms_faults_and_save(printer_id, [TOOLHEAD], "RUNNING") == [TOOLHEAD]

    async def test_a_failed_load_starts_empty(self, printer_id):
        with patch(
            "backend.app.services.hms_notified.load_notified_hms_faults",
            AsyncMock(side_effect=RuntimeError("no such table")),
        ):
            await _load_notified_hms_faults()

        assert printer_id not in main_module._notified_hms_errors


class TestTitle:
    @pytest.mark.parametrize(
        ("module", "title"),
        [
            # USB, MQTT and file errors were titled "Nozzle/Extruder Error".
            (0x05, "System Error"),
            # Cameras and lidar, not motion.
            (0x0C, "Camera Error"),
            # An A1 mini's AMS Lite runouts were titled "Chamber Error".
            (0x12, "AMS Lite Error"),
            (0x03, "Print/Task Error"),
            (0x07, "AMS/Filament Error"),
            (0x18, "AMS-HT Error"),
            (0x2A, "AMS 3H Error"),
            (0x1A, "Tool Changer Error"),
            # Unknown modules get a plain title rather than a hex number.
            (0x7F, "Printer Error"),
        ],
    )
    def test_the_title_names_the_right_subsystem(self, module, title):
        assert _hms_error_title(module) == title
