"""Unit tests for the macro execution engine."""

import asyncio
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from backend.app.services.gcode_whitelist import GCODE_WHITELIST, is_whitelisted

# ============================================================================
# G-code whitelist tests
# ============================================================================


def test_gcode_whitelist_pass():
    assert is_whitelisted("G28") is True
    assert is_whitelisted("G0 X10 Y10") is True
    assert is_whitelisted("M104 S200") is True
    assert is_whitelisted("T0") is True


def test_gcode_whitelist_block():
    assert is_whitelisted("M600") is False
    assert is_whitelisted("G29") is False
    assert is_whitelisted("M666") is False
    assert is_whitelisted("") is False


def test_gcode_whitelist_comment_is_not_gcode():
    assert is_whitelisted("; G28") is False
    assert is_whitelisted("# G28") is False


def test_gcode_whitelist_case_insensitive():
    assert is_whitelisted("g28") is True
    assert is_whitelisted("m104 S200") is True


# ============================================================================
# MacroFileService tests
# ============================================================================


def test_macro_file_write_read_delete(tmp_path):
    from backend.app.core.config import settings

    settings.macros_dir = tmp_path / "macros"

    from backend.app.services import macro_files

    path = macro_files.write("test macro", "G28\nG0 X0 Y0")
    assert path.endswith(".jinja2")
    assert (tmp_path / "macros" / path).exists()

    content = macro_files.read(path)
    assert "G28" in content

    macro_files.delete(path)
    assert not (tmp_path / "macros" / path).exists()


def test_macro_file_slug_collision(tmp_path):
    from backend.app.core.config import settings

    settings.macros_dir = tmp_path / "macros"

    from backend.app.services import macro_files

    p1 = macro_files.write("heat bed", "M140 S60")
    p2 = macro_files.write("heat bed", "M140 S70")
    assert p1 != p2


# ============================================================================
# Macro runner execution tests
# ============================================================================


@pytest.fixture
def mock_printer_client():
    client = MagicMock()
    client.state = MagicMock()
    client.state.connected = True
    client.state.state = "IDLE"
    client.state.temperatures = {"nozzle": 25.0, "bed": 25.0}
    client.state.raw_data = {"ams": []}
    client.send_gcode = MagicMock(return_value=True)
    client.pause_print = MagicMock(return_value=True)
    client.resume_print = MagicMock(return_value=True)
    client.stop_print = MagicMock(return_value=True)
    return client


@pytest.fixture
def macro_runner_with_tmp(tmp_path):
    from backend.app.core.config import settings

    settings.macros_dir = tmp_path / "macros"
    from backend.app.services.macro_runner import MacroRunner

    return MacroRunner()


@pytest.mark.asyncio
async def test_gcode_dispatches_to_mqtt(tmp_path, macro_runner_with_tmp, mock_printer_client, db_session):
    from backend.app.core.config import settings
    from backend.app.services import macro_files

    settings.macros_dir = tmp_path / "macros"

    file_path = macro_files.write("home", "G28")
    from backend.app.models.macro import Macro

    macro = Macro(name="home", file_path=file_path, trigger_type="manual")
    db_session.add(macro)
    await db_session.commit()
    await db_session.refresh(macro)

    with (
        patch("backend.app.services.macro_runner.async_session") as mock_session_cm,
        patch("backend.app.services.printer_manager.printer_manager") as mock_pm,
    ):
        mock_pm.get_client.return_value = mock_printer_client
        # Make async_session work as async context manager
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=lambda model, id_: macro if id_ == macro.id else None)
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_session_cm.return_value = mock_session

        runner = macro_runner_with_tmp
        # Inject a pre-created run so we don't need full DB
        await runner.run_macro(macro.id, printer_id=1, trigger="manual")

    mock_printer_client.send_gcode.assert_called()


@pytest.mark.asyncio
async def test_unknown_gcode_blocked(tmp_path, macro_runner_with_tmp, mock_printer_client):
    from backend.app.core.config import settings
    from backend.app.services import macro_files
    from backend.app.services.macro_runner import MacroRunner

    settings.macros_dir = tmp_path / "macros"

    runner = MacroRunner()
    log_lines: list[str] = []

    async def fake_log(run_id, text):
        log_lines.append(text)

    runner._append_log = fake_log  # type: ignore

    with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = mock_printer_client
        await runner._dispatch_line("M666 S1", printer_id=1, run_id=1, allow_printer_commands=True)

    mock_printer_client.send_gcode.assert_not_called()
    assert any("[WARN]" in line for line in log_lines)


@pytest.mark.asyncio
async def test_ams_drying_command(macro_runner_with_tmp, mock_printer_client):
    runner = macro_runner_with_tmp
    log_lines: list[str] = []

    async def fake_log(run_id, text):
        log_lines.append(text)

    runner._append_log = fake_log  # type: ignore

    with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
        mock_pm.send_drying_command.return_value = True
        await runner._dispatch_line(
            "AMS_DRYING --ams=0 --temp=65 --duration=30",
            printer_id=1,
            run_id=1,
            allow_printer_commands=True,
        )

    mock_pm.send_drying_command.assert_called_once_with(1, 0, 65, 30, mode=1, filament=None, rotate_tray=False)
    assert any("[AMS_DRYING]" in line for line in log_lines)


@pytest.mark.asyncio
async def test_wait_command(macro_runner_with_tmp):
    import time

    runner = macro_runner_with_tmp
    log_lines: list[str] = []

    async def fake_log(run_id, text):
        log_lines.append(text)

    runner._append_log = fake_log  # type: ignore

    start = time.monotonic()
    await runner._dispatch_line("WAIT --seconds=0.1", printer_id=None, run_id=1, allow_printer_commands=True)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.05  # at least half the wait (timing tolerance)
    assert any("[WAIT]" in line for line in log_lines)


@pytest.mark.asyncio
async def test_jinja2_conditional_renders(tmp_path):
    from backend.app.core.config import settings
    from backend.app.services import macro_files
    from backend.app.services.macro_runner import MacroRunner

    settings.macros_dir = tmp_path / "macros"
    from backend.app.services.gcode_whitelist import is_whitelisted

    script = "{% if printer.nozzle_temp > 50 %}G28{% else %}M84{% endif %}"

    from jinja2.sandbox import SandboxedEnvironment

    env = SandboxedEnvironment()
    context = {"printer": {"nozzle_temp": 100.0}, "ams": [], "queue": 0, "run_macro": lambda n: ""}
    rendered = env.from_string(script).render(**context)
    assert "G28" in rendered
    assert "M84" not in rendered

    context2 = {"printer": {"nozzle_temp": 25.0}, "ams": [], "queue": 0, "run_macro": lambda n: ""}
    rendered2 = env.from_string(script).render(**context2)
    assert "M84" in rendered2


@pytest.mark.asyncio
async def test_allow_printer_commands_false_blocks_gcode(macro_runner_with_tmp, mock_printer_client):
    runner = macro_runner_with_tmp
    log_lines: list[str] = []

    async def fake_log(run_id, text):
        log_lines.append(text)

    runner._append_log = fake_log  # type: ignore

    with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = mock_printer_client
        await runner._dispatch_line("G28", printer_id=1, run_id=1, allow_printer_commands=False)

    mock_printer_client.send_gcode.assert_not_called()
    assert any("[SKIP]" in line for line in log_lines)


@pytest.mark.asyncio
async def test_allow_printer_commands_false_blocks_ams_drying(macro_runner_with_tmp):
    runner = macro_runner_with_tmp
    log_lines: list[str] = []

    async def fake_log(run_id, text):
        log_lines.append(text)

    runner._append_log = fake_log  # type: ignore

    with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
        mock_pm.send_drying_command.return_value = True
        await runner._dispatch_line(
            "AMS_DRYING --ams=0 --temp=65 --duration=30",
            printer_id=1,
            run_id=1,
            allow_printer_commands=False,
        )

    mock_pm.send_drying_command.assert_not_called()
    assert any("[SKIP]" in line for line in log_lines)


# ============================================================================
# Embedded macro parsing tests
# ============================================================================


def test_gcode_embed_parse(tmp_path):
    """ThreeMFParser extracts '; MACRO: name' lines from G-code inside a 3MF."""
    gcode_content = b"""; Bambu Studio
; total layer number: 10
; printer_model = X1C
G28 ; home
; MACRO: notify_done
G0 X0 Y0
; MACRO: log_layer_start --layer=1
M84
"""
    # Build a minimal fake .3mf (zip file)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", gcode_content)

    buf.seek(0)
    threemf_path = tmp_path / "test.gcode.3mf"
    threemf_path.write_bytes(buf.read())

    from backend.app.services.archive import ThreeMFParser

    parser = ThreeMFParser(threemf_path, plate_number=1)
    metadata = parser.parse()

    assert "embedded_macros" in metadata
    assert "notify_done" in metadata["embedded_macros"]
    assert "log_layer_start --layer=1" in metadata["embedded_macros"]
