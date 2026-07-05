"""Macro execution engine.

Renders Jinja2 scripts with a sandboxed environment, then dispatches each
rendered line to the appropriate handler (G-code via MQTT or system commands).

System commands are looked up from the macro_functions registry — adding a new
command requires only a decorated function in macro_functions.py.
"""

import asyncio
import logging
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import func as sa_func, select

from backend.app.core.database import async_session
from backend.app.models.macro import Macro, MacroCfgFile, MacroRun
from backend.app.services import macro_functions as mf
from backend.app.services.gcode_whitelist import is_whitelisted
from backend.app.services.macro_cfg_parser import get_macro_body
from backend.app.services.macro_files import read as read_cfg_file

logger = logging.getLogger(__name__)

_jinja_env = SandboxedEnvironment(keep_trailing_newline=True, undefined=StrictUndefined)

# How long to wait after a G-code command before sampling HMS for new errors
_HMS_POLL_DELAY = 0.5

# Flush log buffer to DB every N lines to reduce round-trips
_LOG_FLUSH_EVERY = 10


# ── Result types (kept here for API route compatibility) ───────────────────────


@dataclass
class HMSErrorInfo:
    code: str
    severity: int
    message: str = ""


@dataclass
class CommandResult:
    ok: bool
    log: str = ""
    new_hms_errors: list[HMSErrorInfo] = field(default_factory=list)
    printer_state: str = ""

    @property
    def failed(self) -> bool:
        return not self.ok


# ── Log buffer ─────────────────────────────────────────────────────────────────


class _LogBuffer:
    """Batches log writes to reduce DB round-trips during macro runs."""

    def __init__(self, run_id: int, flush_every: int = _LOG_FLUSH_EVERY) -> None:
        self._run_id = run_id
        self._flush_every = flush_every
        self._lines: list[str] = []
        self._count = 0

    async def write(self, text: str) -> None:
        self._lines.append(text)
        self._count += 1
        if self._count >= self._flush_every:
            await self.flush()

    async def flush(self) -> None:
        if not self._lines:
            return
        blob = "".join(self._lines)
        self._lines.clear()
        self._count = 0
        from sqlalchemy import update as sa_update

        async with async_session() as db:
            await db.execute(
                sa_update(MacroRun)
                .where(MacroRun.id == self._run_id)
                .values(log=sa_func.coalesce(MacroRun.log, "") + blob)
            )
            await db.commit()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _snapshot_hms(client) -> set[str]:
    return {e.code for e in (client.state.hms_errors or [])}


def _new_hms_errors(client, before: set[str]) -> list[HMSErrorInfo]:
    result = []
    for e in client.state.hms_errors or []:
        if e.code not in before:
            result.append(HMSErrorInfo(code=e.code, severity=e.severity, message=getattr(e, "message", "")))
    return result


def _preflight(client, line: str) -> str | None:
    """Return an error string if the command is unsafe to send, else None."""
    if not client.state.connected:
        return "Printer is not connected"
    state = client.state.state
    tokens = line.upper().split() if line.strip() else []
    token = tokens[0] if tokens else ""

    if not is_whitelisted(line):
        return f"G-code '{token}' is not in the allowed whitelist"

    if token in ("G0", "G1"):
        if any(t.startswith(("X", "Y")) for t in tokens[1:]):
            return (
                "XY movement via gcode_line is not safe on Bambu firmware — "
                "use Z-only moves and the touchscreen for XY jogging"
            )

    _unsafe_while_running = {"G28", "G29", "M84", "M104", "M109", "M140", "M190"}
    if state == "RUNNING" and token in _unsafe_while_running:
        return f"Command {token} is not safe while printer is RUNNING (state={state})"
    return None


def _parse_flags(tokens: list[str]) -> dict[str, str]:
    """Parse --key=value and --key value pairs from a tokenized command line.

    shlex.split() is called upstream so quoted values arrive already unquoted.
    A bare --flag with no value (i.e. followed by another --flag or at end) is
    stored as an empty string so callers can distinguish presence from absence.
    """
    flags: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            if "=" in tok:
                k, v = tok[2:].split("=", 1)
                flags[k] = v
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                # next token is the value (may itself contain -- inside quotes,
                # already unwrapped by shlex, so we just take it as-is)
                flags[tok[2:]] = tokens[i + 1]
                i += 1
            else:
                # bare flag — store empty string so presence is detectable
                flags[tok[2:]] = ""
        i += 1
    return flags


async def _load_macro_body(macro: Macro) -> str | None:
    if macro.cfg_file_id is None:
        return None
    async with async_session() as db:
        cfg_file = await db.get(MacroCfgFile, macro.cfg_file_id)
    if cfg_file is None:
        return None
    try:
        text = read_cfg_file(cfg_file.file_path)
    except FileNotFoundError:
        return None
    return get_macro_body(text, macro.name)


async def _get_queue_count() -> int:
    """Return number of pending/queued print queue items."""
    try:
        from backend.app.models.print_queue import PrintQueueItem

        async with async_session() as db:
            result = await db.execute(
                select(sa_func.count()).select_from(PrintQueueItem).where(PrintQueueItem.status == "pending")
            )
            return result.scalar_one() or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("_get_queue_count failed: %s", exc)
        return 0


# ── Runner ─────────────────────────────────────────────────────────────────────


class MacroRunner:
    def __init__(self) -> None:
        self._scheduler_task: asyncio.Task | None = None
        self._running_tasks: dict[int, asyncio.Task] = {}

    def cancel_run(self, run_id: int) -> bool:
        task = self._running_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    # ── Public API ─────────────────────────────────────────────────────────────

    async def exec_line(self, line: str, printer_id: int | None) -> CommandResult:
        """Execute a single command line from the terminal."""
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            return CommandResult(ok=True, log="")

        token = line.split()[0].upper()

        if token in mf.command_names():
            log_lines: list[str] = []

            async def _capture(run_id, text: str) -> None:
                log_lines.append(text)

            result = await self._dispatch_system(line, printer_id, run_id=None, log_fn=_capture)
            return CommandResult(ok=result.ok, log="".join(log_lines))

        return await self._send_gcode(line, printer_id, run_id=None, log_fn=_default_log)

    async def run_macro(
        self,
        macro_id: int,
        printer_id: int | None,
        trigger: str,
        allow_printer_commands: bool = True,
        run_id: int | None = None,
    ) -> int:
        """Execute a macro. Returns the MacroRun id."""
        async with async_session() as db:
            macro = await db.get(Macro, macro_id)
            if not macro:
                raise ValueError(f"Macro {macro_id} not found")
            if run_id is None:
                run = MacroRun(
                    macro_id=macro_id,
                    printer_id=printer_id,
                    status="running",
                    trigger=trigger,
                )
                db.add(run)
                await db.flush()
                run_id = run.id
            else:
                run = await db.get(MacroRun, run_id)
                if run:
                    run.status = "running"
            macro_name = macro.name
            await db.commit()

        buf = _LogBuffer(run_id)

        async def log_fn(rid, text: str) -> None:
            await buf.write(text)

        script = await _load_macro_body(macro)
        if script is None:
            await buf.write(f"[ERROR] Macro body not found for '{macro_name}'\n")
            await buf.flush()
            await self._finish_run(run_id, "error")
            return run_id

        call_stack = frozenset({macro_name})
        try:
            context = await self._build_context(printer_id, call_stack=call_stack, run_id=run_id, log_fn=log_fn)
            rendered = _jinja_env.from_string(script).render(**context)
        except Exception as exc:  # noqa: BLE001
            await buf.write(f"[ERROR] Template render failed: {exc}\n")
            await buf.flush()
            await self._finish_run(run_id, "error")
            return run_id

        current_task = asyncio.current_task()
        if current_task:
            self._running_tasks[run_id] = current_task

        error_occurred = False
        try:
            gcode_batch: list[str] = []

            async def _flush_batch() -> bool:
                nonlocal error_occurred
                if not gcode_batch:
                    return True
                combined = "\n".join(gcode_batch) + "\n"
                gcode_batch.clear()
                result = await self._send_gcode(combined, printer_id, run_id, log_fn)
                if result.failed:
                    error_occurred = True
                    return False
                return True

            for raw_line in rendered.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue

                try:
                    token = line.split()[0].upper()
                    if token in mf.command_names():
                        if not await _flush_batch():
                            break
                        result = await self._dispatch_system(
                            line,
                            printer_id,
                            run_id,
                            allow_printer_commands,
                            log_fn,
                            call_stack=call_stack,
                        )
                        if result is not None and result.failed:
                            error_occurred = True
                            break
                    else:
                        if allow_printer_commands and printer_id is not None:
                            from backend.app.services.printer_manager import printer_manager

                            client = printer_manager.get_client(printer_id)
                            if client:
                                err = _preflight(client, line)
                                if err:
                                    await log_fn(run_id, f"[PREFLIGHT] {err}\n")
                                    error_occurred = True
                                    break
                        if not allow_printer_commands:
                            await log_fn(run_id, f"[SKIP] G-code blocked in gcode_embed mode: {line}\n")
                        else:
                            gcode_batch.append(line)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    await log_fn(run_id, f"[ERROR] {line}: {exc}\n")
                    error_occurred = True
                    break

            if not error_occurred:
                await _flush_batch()

            status = "error" if error_occurred else "success"
        except asyncio.CancelledError:
            await buf.write("[CANCELLED] Run was cancelled by user\n")
            await buf.flush()
            await self._finish_run(run_id, "error")
            return run_id
        finally:
            self._running_tasks.pop(run_id, None)

        await buf.flush()
        await self._finish_run(run_id, status)
        return run_id

    # ── Scheduling ─────────────────────────────────────────────────────────────

    def start_scheduler(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            return
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None

    async def _scheduler_loop(self) -> None:
        from croniter import croniter

        tick = 0
        last_fired: dict[int, datetime] = {}

        while True:
            try:
                await asyncio.sleep(60)
                tick += 1
                now = datetime.now(timezone.utc)
                async with async_session() as db:
                    result = await db.execute(select(Macro).where(Macro.trigger_type == "schedule"))
                    macros = result.scalars().all()

                for macro in macros:
                    if not macro.cron_expression:
                        continue
                    prev = last_fired.get(macro.id)
                    try:
                        should_fire = croniter.match(macro.cron_expression, now)
                        # Also check we haven't already fired this minute
                        already_fired = prev and prev.replace(second=0, microsecond=0) == now.replace(
                            second=0, microsecond=0
                        )
                        if should_fire and not already_fired:
                            last_fired[macro.id] = now
                            asyncio.create_task(self.run_macro(macro.id, macro.printer_id, "schedule"))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Cron match error for macro %d (%r): %s", macro.id, macro.cron_expression, exc)

                # Prune expired macro vars every 60 minutes
                if tick % 60 == 0:
                    await self._prune_expired_vars()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Macro scheduler error: %s", exc)

    async def _prune_expired_vars(self) -> None:
        from sqlalchemy import delete as sa_delete

        from backend.app.models.macro_var import MacroVar

        try:
            async with async_session() as db:
                now = datetime.now(timezone.utc)
                await db.execute(
                    sa_delete(MacroVar).where(
                        MacroVar.expires_at.is_not(None),
                        MacroVar.expires_at <= now,
                    )
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to prune expired macro vars: %s", exc)

    # ── Context ────────────────────────────────────────────────────────────────

    async def _build_context(
        self,
        printer_id: int | None,
        call_stack: frozenset[str],
        run_id: int | None = None,
        log_fn=None,
    ) -> dict:
        from backend.app.services.printer_manager import printer_manager

        if log_fn is None:
            log_fn = _default_log

        printer_ctx: dict = {}
        ams_ctx: list = []

        if printer_id is not None:
            client = printer_manager.get_client(printer_id)
            if client:
                s = client.state
                temps = s.temperatures
                printer_ctx = {
                    "state": s.state,
                    "connected": s.connected,
                    "nozzle_temp": temps.get("nozzle", 0.0),
                    "bed_temp": temps.get("bed", 0.0),
                    "progress": s.progress,
                    "layer": s.layer_num,
                    "total_layers": s.total_layers,
                    "current_print": s.current_print,
                }
                ams_ctx = s.raw_data.get("ams", [])

        queue_count = await _get_queue_count()

        extra = await mf.build_context_values(printer_id, log_fn, run_id=run_id)

        return {
            "printer": printer_ctx,
            "ams": ams_ctx,
            "queue": queue_count,
            **extra,
        }

    async def _run_sub_macro(
        self,
        name: str,
        printer_id: int | None,
        call_stack: frozenset[str],
        run_id: int | None = None,
        log_fn=None,
        allow_printer_commands: bool = True,
    ) -> None:
        if log_fn is None:
            log_fn = _default_log

        async with async_session() as db:
            result = await db.execute(select(Macro).where(Macro.name == name))
            macro = result.scalar_one_or_none()
        if not macro:
            await log_fn(run_id, f"[WARN] Sub-macro '{name}' not found\n")
            return
        try:
            script = await _load_macro_body(macro)
            if script is None:
                await log_fn(run_id, f"[WARN] Sub-macro '{name}' body not found\n")
                return
            context = await self._build_context(printer_id, call_stack, run_id=run_id, log_fn=log_fn)
            rendered = _jinja_env.from_string(script).render(**context)
        except Exception as exc:  # noqa: BLE001
            await log_fn(run_id, f"[ERROR] Sub-macro '{name}' render failed: {exc}\n")
            return
        gcode_batch: list[str] = []
        for raw_line in rendered.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            try:
                token = line.split()[0].upper()
                if token in mf.command_names():
                    if gcode_batch:
                        if allow_printer_commands:
                            await self._send_gcode("\n".join(gcode_batch), printer_id, run_id, log_fn)
                        gcode_batch.clear()
                    await self._dispatch_system(
                        line,
                        printer_id,
                        run_id,
                        allow_printer_commands=allow_printer_commands,
                        log_fn=log_fn,
                        call_stack=call_stack,
                    )
                else:
                    if not allow_printer_commands:
                        await log_fn(run_id, f"[SKIP] G-code blocked in gcode_embed mode: {line}\n")
                    else:
                        gcode_batch.append(line)
            except Exception as exc:  # noqa: BLE001
                await log_fn(run_id, f"[ERROR] Sub-macro '{name}' dispatch error: {exc}\n")
                break
        if gcode_batch and allow_printer_commands:
            await self._send_gcode("\n".join(gcode_batch), printer_id, run_id, log_fn)

    # ── Dispatch ───────────────────────────────────────────────────────────────

    async def _dispatch_system(
        self,
        line: str,
        printer_id: int | None,
        run_id: int | None,
        allow_printer_commands: bool = True,
        log_fn=None,
        call_stack: frozenset[str] | None = None,
    ) -> CommandResult:
        if log_fn is None:
            log_fn = _default_log

        token = line.split()[0].upper()

        if not allow_printer_commands and token in mf.embed_blocked_names():
            msg = f"[SKIP] Command blocked in gcode_embed mode: {line}\n"
            await log_fn(run_id, msg)
            return CommandResult(ok=True)

        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        flags = _parse_flags(tokens[1:])

        ctx = mf.FunctionContext(
            flags=flags,
            printer_id=printer_id,
            run_id=run_id,
            log=log_fn,
            allow_printer_commands=allow_printer_commands,
            _runner=self,
            _call_stack=call_stack if call_stack is not None else frozenset(),
        )
        fn_result = await mf.execute(token, ctx)
        return CommandResult(ok=fn_result.ok, log=fn_result.message)

    # ── G-code ─────────────────────────────────────────────────────────────────

    async def _send_gcode(
        self,
        payload: str,
        printer_id: int | None,
        run_id: int | None,
        log_fn=None,
    ) -> CommandResult:
        from backend.app.services.printer_manager import printer_manager

        if log_fn is None:
            log_fn = _default_log

        if printer_id is None:
            msg = "[WARN] No printer selected for G-code\n"
            await log_fn(run_id, msg)
            return CommandResult(ok=False, log=msg)

        client = printer_manager.get_client(printer_id)
        if not client:
            msg = f"[ERROR] Printer {printer_id} not connected\n"
            await log_fn(run_id, msg)
            return CommandResult(ok=False, log=msg)

        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            err = _preflight(client, line)
            if err:
                msg = f"[PREFLIGHT] {err}\n"
                await log_fn(run_id, msg)
                return CommandResult(ok=False, log=msg, printer_state=client.state.state)

        hms_before = _snapshot_hms(client)
        if not payload.endswith("\n"):
            payload += "\n"
        sent = client.send_gcode(payload)
        if not sent:
            msg = "[ERROR] Failed to send G-code (MQTT publish failed)\n"
            await log_fn(run_id, msg)
            return CommandResult(ok=False, log=msg, printer_state=client.state.state)

        for line in payload.splitlines():
            if line.strip():
                await log_fn(run_id, f"[GCODE] {line.strip()}\n")

        await asyncio.sleep(_HMS_POLL_DELAY)
        new_hms = _new_hms_errors(client, hms_before)
        ok = True
        if new_hms:
            ok = False
            for e in new_hms:
                await log_fn(run_id, f"[HMS ERROR] code={e.code} severity={e.severity} {e.message}\n")

        return CommandResult(ok=ok, new_hms_errors=new_hms, printer_state=client.state.state)

    # ── DB helpers ─────────────────────────────────────────────────────────────

    async def _finish_run(self, run_id: int, status: str) -> None:
        async with async_session() as db:
            run = await db.get(MacroRun, run_id)
            if run:
                run.status = status
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()


async def _default_log(run_id, text: str) -> None:
    logger.info(text.strip())


macro_runner = MacroRunner()
