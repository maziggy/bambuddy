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

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.macro import Macro, MacroCfgFile, MacroRun
from backend.app.services import macro_functions as mf
from backend.app.services.macro_cfg_parser import get_macro_body
from backend.app.services.macro_files import read as read_cfg_file

logger = logging.getLogger(__name__)

_jinja_env = SandboxedEnvironment(keep_trailing_newline=True)

# How long to wait after a G-code command before sampling HMS for new errors
_HMS_POLL_DELAY = 0.5


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
    flags: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            if "=" in tok:
                k, v = tok[2:].split("=", 1)
                flags[k] = v
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                flags[tok[2:]] = tokens[i + 1]
                i += 1
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

            orig_log = self._log
            self._log = _capture  # type: ignore[method-assign]
            try:
                result = await self._dispatch_system(line, printer_id, run_id=None)
            finally:
                self._log = orig_log  # type: ignore[method-assign]

            log = "".join(log_lines)
            return CommandResult(ok=result.ok, log=log)

        return await self._send_gcode(line, printer_id, run_id=None)

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

        script = await _load_macro_body(macro)
        if script is None:
            await self._finish_run(run_id, "error", f"[ERROR] Macro body not found for '{macro_name}'\n")
            return run_id

        try:
            context = await self._build_context(printer_id, call_stack={macro_name}, run_id=run_id)
            rendered = _jinja_env.from_string(script).render(**context)
        except Exception as exc:  # noqa: BLE001
            await self._finish_run(run_id, "error", f"[ERROR] Template render failed: {exc}\n")
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
                result = await self._send_gcode(combined, printer_id, run_id)
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
                        result = await self._dispatch_system(line, printer_id, run_id, allow_printer_commands)
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
                                    await self._append_log(run_id, f"[PREFLIGHT] {err}\n")
                                    error_occurred = True
                                    break
                        if not allow_printer_commands:
                            await self._append_log(run_id, f"[SKIP] G-code blocked in gcode_embed mode: {line}\n")
                        else:
                            await self._append_log(run_id, f"[GCODE] {line}\n")
                            gcode_batch.append(line)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    await self._append_log(run_id, f"[ERROR] {line}: {exc}\n")
                    error_occurred = True
                    break

            if not error_occurred:
                await _flush_batch()

            status = "error" if error_occurred else "success"
        except asyncio.CancelledError:
            await self._finish_run(run_id, "error", "[CANCELLED] Run was cancelled by user\n")
            return run_id
        finally:
            self._running_tasks.pop(run_id, None)

        await self._finish_run(run_id, status)
        return run_id

    # ── Scheduling ─────────────────────────────────────────────────────────────

    def start_scheduler(self) -> None:
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()

    async def _scheduler_loop(self) -> None:
        from croniter import croniter

        tick = 0
        while True:
            try:
                await asyncio.sleep(60)
                tick += 1
                now = datetime.now(timezone.utc)
                async with async_session() as db:
                    result = await db.execute(select(Macro).where(Macro.trigger_type == "schedule"))
                    macros = result.scalars().all()

                for macro in macros:
                    if macro.cron_expression and croniter.match(macro.cron_expression, now):
                        asyncio.create_task(self.run_macro(macro.id, macro.printer_id, "schedule"))

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
        call_stack: set[str],
        run_id: int | None = None,
    ) -> dict:
        from backend.app.services.printer_manager import printer_manager

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

        def _run_macro_fn(name: str) -> str:
            if name in call_stack:
                raise RecursionError(f"Macro cycle detected: {name} -> {' -> '.join(call_stack)}")
            asyncio.create_task(self._run_sub_macro(name, printer_id, call_stack | {name}))
            return ""

        # Eager context values from the function registry
        extra = await mf.build_context_values(printer_id, self._log)

        return {
            "printer": printer_ctx,
            "ams": ams_ctx,
            "queue": 0,
            "run_macro": _run_macro_fn,
            **extra,
        }

    async def _run_sub_macro(self, name: str, printer_id: int | None, call_stack: set[str]) -> None:
        async with async_session() as db:
            result = await db.execute(select(Macro).where(Macro.name == name))
            macro = result.scalar_one_or_none()
        if not macro:
            logger.warning("Sub-macro not found: %s", name)
            return
        try:
            script = await _load_macro_body(macro)
            if script is None:
                logger.warning("Sub-macro body not found: %s", name)
                return
            context = await self._build_context(printer_id, call_stack)
            rendered = _jinja_env.from_string(script).render(**context)
        except Exception as exc:  # noqa: BLE001
            logger.error("Sub-macro %s render failed: %s", name, exc)
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
                        await self._send_gcode("\n".join(gcode_batch), printer_id, run_id=None)
                        gcode_batch.clear()
                    await self._dispatch_system(line, printer_id, run_id=None)
                else:
                    gcode_batch.append(line)
            except Exception as exc:  # noqa: BLE001
                logger.error("Sub-macro %s dispatch error: %s", name, exc)
                break
        if gcode_batch:
            await self._send_gcode("\n".join(gcode_batch), printer_id, run_id=None)

    # ── Dispatch ───────────────────────────────────────────────────────────────

    async def _dispatch_system(
        self,
        line: str,
        printer_id: int | None,
        run_id: int | None,
        allow_printer_commands: bool = True,
    ) -> CommandResult:
        token = line.split()[0].upper()

        if not allow_printer_commands and token in mf.embed_blocked_names():
            msg = f"[SKIP] Command blocked in gcode_embed mode: {line}\n"
            await self._log(run_id, msg)
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
            log=self._log,
            allow_printer_commands=allow_printer_commands,
        )
        fn_result = await mf.execute(token, ctx)
        return CommandResult(ok=fn_result.ok, log=fn_result.message)

    # ── G-code ─────────────────────────────────────────────────────────────────

    async def _send_gcode(self, payload: str, printer_id: int | None, run_id: int | None) -> CommandResult:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            msg = "[WARN] No printer selected for G-code\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg)

        client = printer_manager.get_client(printer_id)
        if not client:
            msg = f"[ERROR] Printer {printer_id} not connected\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg)

        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            err = _preflight(client, line)
            if err:
                msg = f"[PREFLIGHT] {err}\n"
                await self._log(run_id, msg)
                return CommandResult(ok=False, log=msg, printer_state=client.state.state)

        hms_before = _snapshot_hms(client)
        if not payload.endswith("\n"):
            payload += "\n"
        sent = client.send_gcode(payload)
        if not sent:
            msg = "[ERROR] Failed to send G-code (MQTT publish failed)\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg, printer_state=client.state.state)

        for line in payload.splitlines():
            if line.strip():
                await self._log(run_id, f"[GCODE] {line.strip()}\n")

        await asyncio.sleep(_HMS_POLL_DELAY)
        new_hms = _new_hms_errors(client, hms_before)
        ok = True
        if new_hms:
            ok = False
            for e in new_hms:
                await self._log(run_id, f"[HMS ERROR] code={e.code} severity={e.severity} {e.message}\n")

        return CommandResult(ok=ok, new_hms_errors=new_hms, printer_state=client.state.state)

    # ── DB helpers ─────────────────────────────────────────────────────────────

    async def _log(self, run_id: int | None, text: str) -> None:
        if run_id:
            await self._append_log(run_id, text)
        else:
            logger.info(text.strip())

    async def _append_log(self, run_id: int, text: str) -> None:
        async with async_session() as db:
            run = await db.get(MacroRun, run_id)
            if run:
                run.log = (run.log or "") + text
                await db.commit()

    async def _finish_run(self, run_id: int, status: str, extra_log: str = "") -> None:
        async with async_session() as db:
            run = await db.get(MacroRun, run_id)
            if run:
                if extra_log:
                    run.log = (run.log or "") + extra_log
                run.status = status
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()


macro_runner = MacroRunner()
