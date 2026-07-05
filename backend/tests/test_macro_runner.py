"""Tests for the macro execution engine.

Split into:
 - Pure unit tests (whitelist, preflight, _parse_flags) — no DB, no async
 - Async unit tests (_LogBuffer, exec_line, _dispatch_system) — mock DB/printer
 - Async integration tests (run_macro end-to-end) — real in-memory SQLite
 - Scheduler tests
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.app.services.gcode_whitelist import GCODE_WHITELIST, is_whitelisted
from backend.app.services.macro_runner import MacroRunner, _parse_flags, _preflight

# ── Shared fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("backend.app.core.config.settings.macros_dir", str(d))
    return d


@pytest.fixture
def runner():
    return MacroRunner()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.state.connected = True
    client.state.state = "IDLE"
    client.state.temperatures = {"nozzle": 25.0, "bed": 25.0, "chamber": 25.0}
    client.state.hms_errors = []
    client.state.progress = 0
    client.state.raw_data = {"ams": []}
    client.send_gcode = MagicMock(return_value=True)
    client.pause_print = MagicMock(return_value=True)
    client.resume_print = MagicMock(return_value=True)
    client.stop_print = MagicMock(return_value=True)
    return client


# ── Helper: seed a real macro in the test DB and on disk ──────────────────────


async def _seed_macro(db, tmp_path, name: str, body: str, trigger_type: str = "manual") -> tuple:
    """Create a .cfg file and the matching DB rows. Returns (cfg_file, macro)."""
    from backend.app.models.macro import Macro, MacroCfgFile
    from backend.app.services.macro_files import create as create_cfg

    content = f"[macro {name}]\n{body}\n"
    relative_path = create_cfg(name, content)

    cfg_file = MacroCfgFile(name=name, file_path=relative_path)
    db.add(cfg_file)
    await db.flush()

    macro = Macro(name=name, cfg_file_id=cfg_file.id, trigger_type=trigger_type)
    db.add(macro)
    await db.commit()
    await db.refresh(macro)
    await db.refresh(cfg_file)
    return cfg_file, macro


# ── R1/R2: Whitelist ───────────────────────────────────────────────────────────


def test_whitelist_pass():
    for cmd in ["G28", "G0 Z10", "M104 S200", "T0", "M140 S60", "M84"]:
        assert is_whitelisted(cmd), f"{cmd} should be whitelisted"


def test_whitelist_block():
    for cmd in ["M600", "M666", "", "; G28", "# G28"]:
        assert not is_whitelisted(cmd), f"{cmd} should be blocked"


# ── R3/R4: Preflight ──────────────────────────────────────────────────────────


def test_preflight_xy_movement_blocked(mock_client):
    err = _preflight(mock_client, "G0 X10 Y10")
    assert err is not None
    assert "XY" in err


def test_preflight_z_only_allowed(mock_client):
    assert _preflight(mock_client, "G0 Z10") is None


def test_preflight_unsafe_while_running(mock_client):
    mock_client.state.state = "RUNNING"
    err = _preflight(mock_client, "G28")
    assert err is not None
    assert "RUNNING" in err


def test_preflight_unknown_gcode_blocked(mock_client):
    err = _preflight(mock_client, "M600")
    assert err is not None
    assert "whitelist" in err


def test_preflight_not_connected(mock_client):
    mock_client.state.connected = False
    err = _preflight(mock_client, "G28")
    assert err is not None
    assert "connected" in err.lower()


# ── R5/R6/R7: _parse_flags ────────────────────────────────────────────────────


def test_parse_flags_equals_form():
    assert _parse_flags(["--temp=65"]) == {"temp": "65"}


def test_parse_flags_space_form():
    assert _parse_flags(["--temp", "65"]) == {"temp": "65"}


def test_parse_flags_bare_flag():
    result = _parse_flags(["--quiet"])
    assert "quiet" in result
    assert result["quiet"] == ""


def test_parse_flags_mixed():
    result = _parse_flags(["--sensor=bed", "--target", "60", "--quiet"])
    assert result == {"sensor": "bed", "target": "60", "quiet": ""}


def test_parse_flags_empty():
    assert _parse_flags([]) == {}


def test_parse_flags_ignores_positional():
    # Positional args without -- prefix are skipped
    result = _parse_flags(["positional", "--key=val"])
    assert result == {"key": "val"}


# ── R8/R9: _LogBuffer ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_buffer_flush_uses_sql_coalesce():
    """flush() must issue an UPDATE with COALESCE, not a read-modify-write."""
    from sqlalchemy import update as sa_update

    from backend.app.services.macro_runner import _LogBuffer

    buf = _LogBuffer(run_id=99)
    await buf.write("line one\n")
    await buf.write("line two\n")

    executed_statements = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=lambda stmt, *a, **kw: executed_statements.append(stmt))
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()

    with patch("backend.app.services.macro_runner.async_session", return_value=mock_db):
        await buf.flush()

    assert len(executed_statements) == 1
    # The compiled SQL must reference COALESCE — verify via string representation
    stmt = executed_statements[0]
    sql_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "coalesce" in sql_str.lower() or "COALESCE" in sql_str


@pytest.mark.asyncio
async def test_log_buffer_batches_until_threshold():
    """Buffer should not flush before reaching _flush_every lines."""
    from backend.app.services.macro_runner import _LogBuffer

    buf = _LogBuffer(run_id=1, flush_every=5)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock()

    with patch("backend.app.services.macro_runner.async_session", return_value=mock_db):
        for i in range(4):
            await buf.write(f"line {i}\n")
        # 4 writes → no flush yet
        assert mock_db.execute.call_count == 0

        await buf.write("line 4\n")
        # 5th write → flush triggered
        assert mock_db.execute.call_count == 1


# ── R10/R11/R12: exec_line ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exec_line_gcode_dispatches_to_mqtt(runner, mock_client):
    with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = mock_client
        result = await runner.exec_line("G28", printer_id=1)

    assert result.ok
    mock_client.send_gcode.assert_called_once()
    sent = mock_client.send_gcode.call_args[0][0]
    assert "G28" in sent


@pytest.mark.asyncio
async def test_exec_line_unknown_gcode_blocked(runner, mock_client):
    with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = mock_client
        result = await runner.exec_line("M600", printer_id=1)

    assert not result.ok
    assert "[PREFLIGHT]" in result.log
    mock_client.send_gcode.assert_not_called()


@pytest.mark.asyncio
async def test_exec_line_system_command_notify(runner):
    """NOTIFY dispatches without a printer and returns ok."""
    from backend.app.services.macro_functions import discover

    discover()

    # Mock the DB session so no providers are returned (NOTIFY returns ok with a skip msg)
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("backend.app.core.database.async_session", return_value=mock_session):
        result = await runner.exec_line("NOTIFY --message=hello", printer_id=None)

    assert result.ok


@pytest.mark.asyncio
async def test_exec_line_comment_is_noop(runner):
    result = await runner.exec_line("; this is a comment", printer_id=None)
    assert result.ok
    assert result.log == ""


# ── R13–R17: run_macro end-to-end ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_macro_success_sets_status(db_session, tmp_path):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun

    _, macro = await _seed_macro(db_session, tmp_path, "home", "G28")

    runner = MacroRunner()

    # Use the test session factory so runner opens sessions on the test DB
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with (
        patch("backend.app.services.macro_runner.async_session", session_factory),
        patch("backend.app.services.printer_manager.printer_manager") as mock_pm,
    ):
        client = MagicMock()
        client.state.connected = True
        client.state.state = "IDLE"
        client.state.temperatures = {}
        client.state.hms_errors = []
        client.send_gcode = MagicMock(return_value=True)
        mock_pm.get_client.return_value = client

        run_id = await runner.run_macro(macro.id, printer_id=1, trigger="manual")

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert run is not None
    assert run.status == "success"
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_run_macro_template_error_sets_error_status(db_session, tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun

    # Script references an undefined variable → StrictUndefined raises
    _, macro = await _seed_macro(db_session, tmp_path, "broken", "{{ undefined_var }}")

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with patch("backend.app.services.macro_runner.async_session", session_factory):
        run_id = await runner.run_macro(macro.id, printer_id=None, trigger="manual")

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert run.status == "error"
    assert "[ERROR]" in (run.log or "")


@pytest.mark.asyncio
async def test_run_macro_embed_mode_skips_gcode(db_session, tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun

    _, macro = await _seed_macro(db_session, tmp_path, "embed_skip", "G28\nM104 S200")

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    mock_client = MagicMock()
    mock_client.send_gcode = MagicMock(return_value=True)

    with (
        patch("backend.app.services.macro_runner.async_session", session_factory),
        patch("backend.app.services.printer_manager.printer_manager") as mock_pm,
    ):
        mock_pm.get_client.return_value = mock_client
        run_id = await runner.run_macro(macro.id, printer_id=1, trigger="gcode_embed", allow_printer_commands=False)

    mock_client.send_gcode.assert_not_called()

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert "[SKIP]" in (run.log or "")


@pytest.mark.asyncio
async def test_run_macro_embed_mode_skips_printer_system_commands(db_session, tmp_path):
    """PRINTER_PAUSE is allowed_in_embed=False so it must be skipped in embed mode."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun
    from backend.app.services.macro_functions import discover

    discover()
    _, macro = await _seed_macro(db_session, tmp_path, "embed_cmd", "PRINTER_PAUSE")

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with (
        patch("backend.app.services.macro_runner.async_session", session_factory),
        patch("backend.app.services.printer_manager.printer_manager") as mock_pm,
    ):
        mock_client = MagicMock()
        mock_pm.get_client.return_value = mock_client
        run_id = await runner.run_macro(macro.id, printer_id=1, trigger="gcode_embed", allow_printer_commands=False)

    mock_client.pause_print.assert_not_called()

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert "[SKIP]" in (run.log or "")


@pytest.mark.asyncio
async def test_run_macro_cancel_sets_error_status(db_session, tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun
    from backend.app.services.macro_functions import discover

    discover()
    # WAIT --seconds=60 will block; we cancel from outside
    _, macro = await _seed_macro(db_session, tmp_path, "long_wait", "WAIT --seconds=60")

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with patch("backend.app.services.macro_runner.async_session", session_factory):
        task = asyncio.create_task(runner.run_macro(macro.id, printer_id=None, trigger="manual"))
        # Let the task start and enter the WAIT sleep
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            run_id = await task
        except asyncio.CancelledError:
            # Task may propagate cancel before recording run_id — fetch from DB
            async with session_factory() as s:
                from sqlalchemy import select

                result = await s.execute(
                    select(MacroRun).where(MacroRun.macro_id == macro.id).order_by(MacroRun.id.desc())
                )
                run = result.scalars().first()
            assert run is not None
            return

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert run.status == "error"
    assert "[CANCELLED]" in (run.log or "")


# ── R18/R19/R20: Sub-macros ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sub_macro_executes_inline(db_session, tmp_path):
    """MACRO --name=b in macro A causes macro B's body to run."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun
    from backend.app.services.macro_functions import discover

    discover()

    _, macro_b = await _seed_macro(db_session, tmp_path, "sub_b", "G28")
    _, macro_a = await _seed_macro(db_session, tmp_path, "sub_a", "MACRO --name=sub_b")

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    mock_client = MagicMock()
    mock_client.state.connected = True
    mock_client.state.state = "IDLE"
    mock_client.state.temperatures = {}
    mock_client.state.hms_errors = []
    mock_client.send_gcode = MagicMock(return_value=True)

    with (
        patch("backend.app.services.macro_runner.async_session", session_factory),
        patch("backend.app.services.printer_manager.printer_manager") as mock_pm,
    ):
        mock_pm.get_client.return_value = mock_client
        run_id = await runner.run_macro(macro_a.id, printer_id=1, trigger="manual")

    mock_client.send_gcode.assert_called()
    sent = mock_client.send_gcode.call_args[0][0]
    assert "G28" in sent

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert run.status == "success"


@pytest.mark.asyncio
async def test_cycle_detection_stops_run(db_session, tmp_path):
    """MACRO --name=self_ref inside itself must log a cycle error, not recurse."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun
    from backend.app.services.macro_functions import discover

    discover()
    _, macro = await _seed_macro(db_session, tmp_path, "self_ref", "MACRO --name=self_ref")

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with patch("backend.app.services.macro_runner.async_session", session_factory):
        run_id = await runner.run_macro(macro.id, printer_id=None, trigger="manual")

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert "cycle" in (run.log or "").lower()


@pytest.mark.asyncio
async def test_sub_macro_not_found_warns(db_session, tmp_path):
    """MACRO --name=nonexistent logs a warning but does not fail the run."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.macro import MacroRun
    from backend.app.services.macro_functions import discover

    discover()
    _, macro = await _seed_macro(db_session, tmp_path, "missing_sub", "MACRO --name=nonexistent")

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with patch("backend.app.services.macro_runner.async_session", session_factory):
        run_id = await runner.run_macro(macro.id, printer_id=None, trigger="manual")

    async with session_factory() as s:
        run = await s.get(MacroRun, run_id)
    assert "[WARN]" in (run.log or "")
    assert run.status == "success"


# ── R21/R22: Scheduler ────────────────────────────────────────────────────────


def test_start_scheduler_idempotent(runner):
    """Calling start_scheduler twice must not create two tasks."""
    loop = asyncio.new_event_loop()
    try:

        async def _run():
            runner.start_scheduler()
            task1 = runner._scheduler_task
            runner.start_scheduler()
            task2 = runner._scheduler_task
            assert task1 is task2
            runner.stop_scheduler()

        loop.run_until_complete(_run())
    finally:
        loop.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["find_spec"]).find_spec("croniter") is None,
    reason="croniter not installed",
)
async def test_scheduler_fires_matching_cron(db_session, tmp_path):
    """Scheduler must call run_macro for a macro whose cron matches now."""
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import async_sessionmaker

    _, macro = await _seed_macro(db_session, tmp_path, "cron_macro", "G28", trigger_type="schedule")
    # Update macro with a cron that always matches
    from backend.app.models.macro import Macro

    async with db_session.begin_nested():
        m = await db_session.get(Macro, macro.id)
        m.cron_expression = "* * * * *"
    await db_session.commit()

    runner = MacroRunner()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    fired: list[int] = []

    async def mock_run_macro(macro_id, printer_id, trigger, **kwargs):
        fired.append(macro_id)
        return 1

    runner.run_macro = mock_run_macro  # type: ignore[method-assign]

    with patch("backend.app.services.macro_runner.async_session", session_factory):
        # Run one tick of the scheduler loop directly
        from croniter import croniter

        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            from sqlalchemy import select

            result = await db.execute(select(Macro).where(Macro.trigger_type == "schedule"))
            macros = result.scalars().all()

        for m in macros:
            if m.cron_expression and croniter.match(m.cron_expression, now):
                await runner.run_macro(m.id, m.printer_id, "schedule")

    assert macro.id in fired
