"""Shared test fixtures for BamBuddy backend tests."""

import asyncio
import atexit
import json
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# IMPORTANT: Set environment variables BEFORE any app imports
# This must happen before settings/config are loaded
os.environ["LOG_TO_FILE"] = "false"
os.environ["DEBUG"] = "false"

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

# Ensure settings use our env vars - import and override before database import
from backend.app.core.config import settings  # noqa: E402

settings.log_to_file = False

# Use a temp directory for plate calibration to avoid deleting real calibration files
_test_plate_cal_dir = Path(tempfile.mkdtemp(prefix="bambuddy_test_plate_cal_"))
settings.plate_calibration_dir = _test_plate_cal_dir


# Clean up temp directory when tests finish
def _cleanup_test_plate_cal_dir():
    if _test_plate_cal_dir.exists():
        shutil.rmtree(_test_plate_cal_dir, ignore_errors=True)


atexit.register(_cleanup_test_plate_cal_dir)

from backend.app.core.database import Base  # noqa: E402

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Import all models to register them
    from backend.app.models import (
        ams_history,
        api_key,
        archive,
        external_link,
        filament,
        group,
        kprofile_note,
        maintenance,
        notification,
        notification_template,
        print_queue,
        printer,
        project,
        settings,
        smart_plug,
        spool,
        spool_assignment,
        spool_usage_history,
        user,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_maker() as session:
        yield session


@pytest.fixture
async def async_client(test_engine, db_session) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    from backend.app.core.database import async_session, get_db
    from backend.app.main import app

    # Create a new session maker for the test engine
    test_async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with test_async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Mock init_printer_connections to prevent MQTT connection attempts during tests
    async def mock_init_printer_connections(db):
        pass  # No-op - don't connect to real printers

    # Also patch the module-level async_session used by services, auth, and middleware
    with (
        patch("backend.app.core.database.async_session", test_async_session),
        patch("backend.app.core.auth.async_session", test_async_session),
        patch("backend.app.main.async_session", test_async_session),
        patch("backend.app.main.init_printer_connections", mock_init_printer_connections),
    ):
        # Seed default groups for tests that need them
        from backend.app.core.database import seed_default_groups

        await seed_default_groups()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    app.dependency_overrides.clear()


# ============================================================================
# Mock External Services
# ============================================================================


@pytest.fixture
def mock_tasmota_service():
    """Mock the Tasmota service for smart plug tests."""
    # Patch both the module where it's defined and where it's imported
    with (
        patch("backend.app.services.tasmota.tasmota_service") as mock,
        patch("backend.app.api.routes.smart_plugs.tasmota_service") as mock2,
    ):
        mock.turn_on = AsyncMock(return_value=True)
        mock.turn_off = AsyncMock(return_value=True)
        mock.toggle = AsyncMock(return_value=True)
        mock.get_status = AsyncMock(return_value={"state": "ON", "reachable": True, "device_name": "Test Plug"})
        mock.get_energy = AsyncMock(
            return_value={
                "power": 150.5,
                "voltage": 120.0,
                "current": 1.25,
                "today": 2.5,
                "total": 100.0,
                "factor": 0.95,
            }
        )
        mock.test_connection = AsyncMock(return_value={"success": True, "state": "ON", "device_name": "Test Plug"})
        # Copy mocks to second patch target
        mock2.turn_on = mock.turn_on
        mock2.turn_off = mock.turn_off
        mock2.toggle = mock.toggle
        mock2.get_status = mock.get_status
        mock2.get_energy = mock.get_energy
        mock2.test_connection = mock.test_connection
        yield mock


@pytest.fixture
def mock_homeassistant_service():
    """Mock the Home Assistant service for smart plug tests."""
    # Patch both the module where it's defined and where it's imported
    with (
        patch("backend.app.services.homeassistant.homeassistant_service") as mock,
        patch("backend.app.api.routes.smart_plugs.homeassistant_service") as mock2,
    ):
        mock.turn_on = AsyncMock(return_value=True)
        mock.turn_off = AsyncMock(return_value=True)
        mock.toggle = AsyncMock(return_value=True)
        mock.get_status = AsyncMock(return_value={"state": "ON", "reachable": True, "device_name": "Test HA Entity"})
        mock.get_energy = AsyncMock(return_value=None)  # Most HA entities don't have power monitoring
        mock.test_connection = AsyncMock(return_value={"success": True, "message": "API running", "error": None})
        mock.list_entities = AsyncMock(
            return_value=[
                {
                    "entity_id": "switch.printer_plug",
                    "friendly_name": "Printer Plug",
                    "state": "on",
                    "domain": "switch",
                },
                {"entity_id": "switch.test", "friendly_name": "Test Switch", "state": "off", "domain": "switch"},
            ]
        )
        mock.configure = MagicMock()
        # Copy mocks to second patch target
        mock2.turn_on = mock.turn_on
        mock2.turn_off = mock.turn_off
        mock2.toggle = mock.toggle
        mock2.get_status = mock.get_status
        mock2.get_energy = mock.get_energy
        mock2.test_connection = mock.test_connection
        mock2.list_entities = mock.list_entities
        mock2.configure = mock.configure
        yield mock


@pytest.fixture
def mock_mqtt_client():
    """Mock the MQTT client for printer communication tests."""
    with patch("backend.app.services.bambu_mqtt.BambuMQTTClient") as mock:
        instance = MagicMock()
        instance.state = MagicMock(connected=True, state="IDLE", progress=0, temperatures={"nozzle": 25, "bed": 25})
        instance.connect = MagicMock()
        instance.disconnect = MagicMock()
        mock.return_value = instance
        yield mock


@pytest.fixture
def mock_mqtt_smart_plug_service():
    """Mock the MQTT smart plug service for MQTT plug tests."""
    with patch("backend.app.api.routes.smart_plugs.mqtt_relay") as mock:
        # Create a mock smart_plug_service
        mock_service = MagicMock()
        mock_service.is_configured = MagicMock(return_value=True)
        mock_service.has_broker_settings = MagicMock(return_value=True)
        mock_service.configure = AsyncMock(return_value=True)
        mock_service.subscribe = MagicMock()
        mock_service.unsubscribe = MagicMock()
        mock_service.get_plug_data = MagicMock(return_value=None)
        mock_service.is_reachable = MagicMock(return_value=False)

        mock.smart_plug_service = mock_service
        yield mock


@pytest.fixture
def mock_ftp_client():
    """Mock the FTP client for file transfer tests."""
    with (
        patch("backend.app.services.bambu_ftp.download_file_async") as download_mock,
        patch("backend.app.services.bambu_ftp.list_files_async") as list_mock,
    ):
        download_mock.return_value = True
        list_mock.return_value = []
        yield {"download": download_mock, "list": list_mock}


@pytest.fixture
def mock_httpx_client():
    """Mock httpx for webhook/notification HTTP calls."""
    with patch("httpx.AsyncClient") as mock_class:
        mock_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        mock_response.json.return_value = {}

        mock_instance.get = AsyncMock(return_value=mock_response)
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock()

        mock_class.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_printer_manager():
    """Mock the printer manager for status checks."""
    with patch("backend.app.services.printer_manager.printer_manager") as mock:
        mock.get_status = MagicMock(
            return_value=MagicMock(
                connected=True,
                state="IDLE",
                progress=0,
                temperatures={"nozzle": 25, "bed": 25, "chamber": 25},
                raw_data={},
            )
        )
        mock.mark_printer_offline = MagicMock()
        yield mock


# ============================================================================
# Factory Fixtures for Test Data
# ============================================================================


@pytest.fixture
def smart_plug_factory(db_session):
    """Factory to create test smart plugs."""

    async def _create_plug(**kwargs):
        from backend.app.models.smart_plug import SmartPlug

        # Determine defaults based on plug_type
        plug_type = kwargs.get("plug_type", "tasmota")

        defaults = {
            "name": "Test Plug",
            "plug_type": plug_type,
            "enabled": True,
            "auto_on": True,
            "auto_off": True,
            "off_delay_mode": "time",
            "off_delay_minutes": 5,
            "off_temp_threshold": 70,
            "schedule_enabled": False,
            "power_alert_enabled": False,
        }

        # Set required fields based on plug_type
        if plug_type == "homeassistant":
            defaults["ha_entity_id"] = "switch.test"
            defaults["ip_address"] = None
        elif plug_type == "mqtt":
            # Legacy fields (for backward compatibility tests)
            defaults["mqtt_topic"] = kwargs.get("mqtt_topic", "test/topic")
            defaults["mqtt_multiplier"] = kwargs.get("mqtt_multiplier", 1.0)
            # New separate topic/path/multiplier fields
            defaults["mqtt_power_topic"] = kwargs.get("mqtt_power_topic")
            defaults["mqtt_power_path"] = kwargs.get("mqtt_power_path", "power")
            defaults["mqtt_power_multiplier"] = kwargs.get("mqtt_power_multiplier", 1.0)
            defaults["mqtt_energy_topic"] = kwargs.get("mqtt_energy_topic")
            defaults["mqtt_energy_path"] = kwargs.get("mqtt_energy_path")
            defaults["mqtt_energy_multiplier"] = kwargs.get("mqtt_energy_multiplier", 1.0)
            defaults["mqtt_state_topic"] = kwargs.get("mqtt_state_topic")
            defaults["mqtt_state_path"] = kwargs.get("mqtt_state_path")
            defaults["mqtt_state_on_value"] = kwargs.get("mqtt_state_on_value")
            defaults["ip_address"] = None
            defaults["ha_entity_id"] = None
        else:
            defaults["ip_address"] = "192.168.1.100"
            defaults["ha_entity_id"] = None

        defaults.update(kwargs)

        plug = SmartPlug(**defaults)
        db_session.add(plug)
        await db_session.commit()
        await db_session.refresh(plug)
        return plug

    return _create_plug


@pytest.fixture
def printer_factory(db_session):
    """Factory to create test printers."""
    _counter = [0]  # Use list to allow mutation in nested function

    async def _create_printer(**kwargs):
        from backend.app.models.printer import Printer

        _counter[0] += 1
        counter = _counter[0]

        defaults = {
            "name": "Test Printer",
            "serial_number": f"00M09A{counter:09d}",  # Unique serial per printer
            "ip_address": f"192.168.1.{100 + counter}",  # Unique IP per printer
            "access_code": "12345678",
            "is_active": True,
            "auto_archive": True,
            "model": "X1C",
        }
        defaults.update(kwargs)

        printer = Printer(**defaults)
        db_session.add(printer)
        await db_session.commit()
        await db_session.refresh(printer)
        return printer

    return _create_printer


@pytest.fixture
def notification_provider_factory(db_session):
    """Factory to create test notification providers."""

    async def _create_provider(**kwargs):
        from backend.app.models.notification import NotificationProvider

        config = kwargs.pop("config", {"server": "https://ntfy.sh", "topic": "test-topic"})
        if isinstance(config, dict):
            config = json.dumps(config)

        defaults = {
            "name": "Test Provider",
            "provider_type": "ntfy",
            "enabled": True,
            "config": config,
            "on_print_start": True,
            "on_print_complete": True,
            "on_print_failed": True,
            "on_print_stopped": True,
            "on_print_progress": False,
            "on_printer_offline": False,
            "on_printer_error": False,
            "on_filament_low": False,
            "on_maintenance_due": False,
            "on_ams_humidity_high": False,
            "on_ams_temperature_high": False,
            "on_bed_cooled": False,
            "quiet_hours_enabled": False,
            "daily_digest_enabled": False,
        }
        defaults.update(kwargs)

        provider = NotificationProvider(**defaults)
        db_session.add(provider)
        await db_session.commit()
        await db_session.refresh(provider)
        return provider

    return _create_provider


@pytest.fixture
def archive_factory(db_session):
    """Factory to create test archives."""

    async def _create_archive(printer_id: int, **kwargs):
        from backend.app.models.archive import PrintArchive

        defaults = {
            "printer_id": printer_id,
            "filename": "test_print.gcode.3mf",
            "print_name": "Test Print",
            "file_path": "archives/test/test_print.gcode.3mf",
            "file_size": 1024000,
            "status": "completed",
            "filament_type": "PLA",
            "filament_used_grams": 50.0,
            "print_time_seconds": 3600,
        }
        defaults.update(kwargs)

        archive = PrintArchive(**defaults)
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)
        return archive

    return _create_archive


# ============================================================================
# Sample Data Fixtures
# ============================================================================


@pytest.fixture
def sample_mqtt_print_start():
    """Sample MQTT message for print start."""
    return {
        "print": {
            "command": "project_file",
            "param": "/sdcard/test.gcode.3mf",
            "subtask_name": "test_print",
            "gcode_state": "RUNNING",
            "mc_percent": 0,
        }
    }


@pytest.fixture
def sample_mqtt_print_complete():
    """Sample MQTT message for print complete."""
    return {
        "print": {
            "gcode_state": "FINISH",
            "mc_percent": 100,
            "subtask_name": "test_print",
        }
    }


@pytest.fixture
def sample_printer_status():
    """Sample printer status data."""
    return {
        "connected": True,
        "state": "IDLE",
        "progress": 0,
        "layer_num": 0,
        "total_layers": 0,
        "temperatures": {
            "nozzle": 25.0,
            "bed": 25.0,
            "chamber": 25.0,
        },
        "remaining_time": 0,
        "filename": None,
    }


# ============================================================================
# Log Capture Fixtures for Error Detection
# ============================================================================


class LogCapture(logging.Handler):
    """Handler that captures log records for testing."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self.records.append(record)

    def clear(self):
        self.records.clear()

    def get_errors(self) -> list[logging.LogRecord]:
        """Get all ERROR and CRITICAL level records."""
        return [r for r in self.records if r.levelno >= logging.ERROR]

    def get_warnings(self) -> list[logging.LogRecord]:
        """Get all WARNING level records."""
        return [r for r in self.records if r.levelno == logging.WARNING]

    def has_errors(self) -> bool:
        """Check if any errors were logged."""
        return len(self.get_errors()) > 0

    def format_errors(self) -> str:
        """Format all errors as a string for assertion messages."""
        errors = self.get_errors()
        if not errors:
            return "No errors"
        formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
        return "\n".join(formatter.format(r) for r in errors)


@pytest.fixture
def capture_logs():
    """Fixture that captures log output during a test.

    Usage:
        def test_something(capture_logs):
            # Do something that might log errors
            some_function()

            # Check no errors were logged
            assert not capture_logs.has_errors(), capture_logs.format_errors()
    """
    handler = LogCapture()
    handler.setLevel(logging.DEBUG)

    # Attach to root logger to capture all logs
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    yield handler

    root_logger.removeHandler(handler)


@pytest.fixture
def assert_no_log_errors(capture_logs):
    """Fixture that automatically asserts no errors were logged.

    Usage:
        def test_something(assert_no_log_errors):
            # If any ERROR logs occur during this test, it will fail
            some_function()
    """
    yield capture_logs

    errors = capture_logs.get_errors()
    if errors:
        pytest.fail(f"Unexpected log errors:\n{capture_logs.format_errors()}")
