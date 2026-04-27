"""Macro execution engine.

Renders Jinja2 scripts with a sandboxed environment, then dispatches each
rendered line to the appropriate handler (G-code via MQTT or system commands).

Macros are stored as .jinja2 files on disk; the DB record holds metadata.
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
from backend.app.services.macro_cfg_parser import get_macro_body
from backend.app.services.macro_files import read as read_cfg_file

logger = logging.getLogger(__name__)

_jinja_env = SandboxedEnvironment(keep_trailing_newline=True)

# How long to wait after a command before sampling HMS for new errors
_HMS_POLL_DELAY = 0.5

# System command prefixes
_SYSTEM_COMMANDS = {
    "AMS_DRYING",
    "PRINTER_PAUSE",
    "PRINTER_RESUME",
    "PRINTER_STOP",
    "NOTIFY",
    "WAIT",
    "WAIT_FOR_TEMP",
}

# Commands that require physical printer interaction (blocked in gcode_embed mode)
_PRINTER_COMMANDS = {
    "AMS_DRYING",
    "PRINTER_PAUSE",
    "PRINTER_RESUME",
    "PRINTER_STOP",
    "WAIT_FOR_TEMP",
}


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


def _snapshot_hms(client) -> set[str]:
    """Return a frozenset of current HMS error codes for diffing."""
    return {e.code for e in (client.state.hms_errors or [])}


def _new_hms_errors(client, before: set[str]) -> list[HMSErrorInfo]:
    """Return HMS errors that appeared since the snapshot was taken."""
    result = []
    for e in client.state.hms_errors or []:
        if e.code not in before:
            result.append(HMSErrorInfo(code=e.code, severity=e.severity, message=getattr(e, "message", "")))
    return result


def _preflight(client, line: str) -> str | None:
    """Check whether the printer can accept a command right now.

    Returns an error string if blocked, None if ok to proceed.
    """
    if not client.state.connected:
        return "Printer is not connected"
    state = client.state.state
    tokens = line.upper().split() if line.strip() else []
    token = tokens[0] if tokens else ""

    # Bambu firmware ignores G91 for XY via gcode_line — G0/G1 with X or Y
    # coordinates treats them as absolute and causes toolhead crashes.
    if token in ("G0", "G1"):
        has_xy = any(t.startswith(("X", "Y")) for t in tokens[1:])
        if has_xy:
            return (
                "XY movement via gcode_line is not safe on Bambu firmware — "
                "use Z-only moves (e.g. G1 Z-5 F600) and the touchscreen for XY jogging"
            )

    # Destructive motion/temperature commands are unsafe while printing
    _unsafe_while_running = {"G28", "G29", "M84", "M104", "M109", "M140", "M190"}
    if state == "RUNNING" and token in _unsafe_while_running:
        return f"Command {token} is not safe while printer is RUNNING (state={state})"
    return None


def _parse_flags(tokens: list[str]) -> dict[str, str]:
    """Parse --key=value or --key value flag pairs from a token list."""
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
    """Read the parent .cfg file and extract this macro's body block."""
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


class MacroRunner:
    def __init__(self) -> None:
        self._scheduler_task: asyncio.Task | None = None
        self._running_tasks: dict[int, asyncio.Task] = {}  # run_id → task

    def cancel_run(self, run_id: int) -> bool:
        """Request cancellation of an in-progress run. Returns True if found."""
        task = self._running_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def exec_line(
        self,
        line: str,
        printer_id: int | None,
    ) -> CommandResult:
        """Execute a single command line from the terminal. Returns a CommandResult."""
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            return CommandResult(ok=True, log="")

        token = line.split()[0].upper()

        # System commands go through the normal handler
        if token in _SYSTEM_COMMANDS:
            log_lines: list[str] = []

            async def _capture(run_id: int | None, text: str) -> None:
                log_lines.append(text)

            orig_append = self._append_log
            self._append_log = _capture  # type: ignore[method-assign]
            try:
                result = await self._handle_system(token, line, printer_id, run_id=None)
            finally:
                self._append_log = orig_append  # type: ignore[method-assign]
            log = "".join(log_lines)
            return CommandResult(ok=result.ok, log=log or result.log, new_hms_errors=result.new_hms_errors)

        # G-code: single-line send with preflight + HMS poll
        return await self._send_gcode(line, printer_id, run_id=None)

    async def run_macro(
        self,
        macro_id: int,
        printer_id: int | None,
        trigger: str,
        allow_printer_commands: bool = True,
        run_id: int | None = None,
    ) -> int:
        """Execute a macro. Returns the MacroRun id.

        If run_id is provided the existing record is reused (caller already
        created it); otherwise a new MacroRun is created here.
        Opens its own DB sessions to avoid holding long transactions.
        """
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

        # Build context and render
        try:
            context = await self._build_context(printer_id, call_stack={macro_name})
            rendered = _jinja_env.from_string(script).render(**context)
        except Exception as exc:  # noqa: BLE001
            await self._finish_run(run_id, "error", f"[ERROR] Template render failed: {exc}\n")
            return run_id

        # Dispatch lines — register the current task so it can be cancelled
        current_task = asyncio.current_task()
        if current_task:
            self._running_tasks[run_id] = current_task

        error_occurred = False
        try:
            # Batch consecutive G-code lines into a single MQTT publish so the
            # printer receives them as one queued block (avoids inter-line delays
            # and prevents the firmware buffer from being starved by rapid sends).
            # System commands (NOTIFY, WAIT, etc.) flush the pending batch first.
            gcode_batch: list[str] = []

            async def _flush_batch() -> bool:
                """Send accumulated G-code lines as one MQTT message. Returns False on error."""
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
                    if token in _SYSTEM_COMMANDS:
                        # Flush pending G-code before running a system command
                        if not await _flush_batch():
                            break
                        result = await self._dispatch_system(line, printer_id, run_id, allow_printer_commands)
                        if result is not None and result.failed:
                            error_occurred = True
                            break
                    else:
                        # Check preflight before batching
                        if allow_printer_commands and printer_id is not None:
                            from backend.app.services.printer_manager import printer_manager

                            client = printer_manager.get_client(printer_id)
                            if client:
                                err = _preflight(client, line)
                                if err:
                                    msg = f"[PREFLIGHT] {err}\n"
                                    await self._append_log(run_id, msg)
                                    error_occurred = True
                                    break
                        if not allow_printer_commands:
                            msg = f"[SKIP] G-code blocked in gcode_embed mode: {line}\n"
                            await self._append_log(run_id, msg)
                        else:
                            await self._append_log(run_id, f"[GCODE] {line}\n")
                            gcode_batch.append(line)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    await self._append_log(run_id, f"[ERROR] {line}: {exc}\n")
                    error_occurred = True
                    break

            # Flush any remaining G-code
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

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def start_scheduler(self) -> None:
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()

    async def _scheduler_loop(self) -> None:
        from croniter import croniter

        while True:
            try:
                await asyncio.sleep(60)
                now = datetime.now(timezone.utc)
                async with async_session() as db:
                    result = await db.execute(
                        select(Macro).where(Macro.trigger_type == "schedule", Macro.status == "active")
                    )
                    macros = result.scalars().all()

                for macro in macros:
                    if macro.cron_expression and croniter.match(macro.cron_expression, now):
                        asyncio.create_task(self.run_macro(macro.id, macro.printer_id, "schedule"))
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Macro scheduler error: %s", exc)

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    async def _build_context(self, printer_id: int | None, call_stack: set[str]) -> dict:
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
            """Called from within a Jinja2 template to invoke another macro."""
            if name in call_stack:
                raise RecursionError(f"Macro cycle detected: {name} -> {' -> '.join(call_stack)}")
            # Schedule the sub-macro; return empty string so template rendering continues
            asyncio.create_task(self._run_sub_macro(name, printer_id, call_stack | {name}))
            return ""

        return {
            "printer": printer_ctx,
            "ams": ams_ctx,
            "queue": 0,
            "run_macro": _run_macro_fn,
        }

    async def _run_sub_macro(self, name: str, printer_id: int | None, call_stack: set[str]) -> None:
        """Resolve a macro by name and execute it inline."""
        async with async_session() as db:
            result = await db.execute(select(Macro).where(Macro.name == name, Macro.status == "active"))
            macro = result.scalar_one_or_none()
        if not macro:
            logger.warning("Sub-macro not found or not active: %s", name)
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
                if token in _SYSTEM_COMMANDS:
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

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_system(
        self,
        line: str,
        printer_id: int | None,
        run_id: int | None,
        allow_printer_commands: bool = True,
    ) -> CommandResult:
        """Dispatch a system command (NOTIFY, WAIT, AMS_DRYING, etc.)."""
        token = line.split()[0].upper()
        if not allow_printer_commands and token in _PRINTER_COMMANDS:
            msg = f"[SKIP] Command blocked in gcode_embed mode: {line}\n"
            await self._log(run_id, msg)
            return CommandResult(ok=True)
        return await self._handle_system(token, line, printer_id, run_id)

    async def _send_gcode(self, payload: str, printer_id: int | None, run_id: int | None) -> CommandResult:
        """Send one or more G-code lines as a single MQTT message.

        payload may be a single line or newline-joined batch.
        Runs preflight on each line before sending. One HMS poll after the whole batch.
        """
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

        # Preflight every line in the payload
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
        # Ensure payload ends with newline
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

        # One HMS poll for the whole payload
        await asyncio.sleep(_HMS_POLL_DELAY)
        new_hms = _new_hms_errors(client, hms_before)
        ok = True
        if new_hms:
            ok = False
            for e in new_hms:
                await self._log(run_id, f"[HMS ERROR] code={e.code} severity={e.severity} {e.message}\n")

        return CommandResult(ok=ok, new_hms_errors=new_hms, printer_state=client.state.state)

    async def _handle_system(
        self,
        token: str,
        line: str,
        printer_id: int | None,
        run_id: int | None,
    ) -> CommandResult:
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        flags = _parse_flags(tokens[1:])

        if token == "AMS_DRYING":
            return await self._handle_ams_drying(flags, printer_id, run_id)
        elif token == "PRINTER_PAUSE":
            return await self._handle_printer_pause(printer_id, run_id)
        elif token == "PRINTER_RESUME":
            return await self._handle_printer_resume(printer_id, run_id)
        elif token == "PRINTER_STOP":
            return await self._handle_printer_stop(printer_id, run_id)
        elif token == "NOTIFY":
            return await self._handle_notify(flags, run_id)
        elif token == "WAIT":
            return await self._handle_wait(flags, run_id)
        elif token == "WAIT_FOR_TEMP":
            return await self._handle_wait_for_temp(flags, printer_id, run_id)
        return CommandResult(ok=True)

    # ------------------------------------------------------------------
    # System command handlers
    # ------------------------------------------------------------------

    async def _handle_ams_drying(self, flags: dict, printer_id: int | None, run_id: int | None) -> CommandResult:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            msg = "[ERROR] AMS_DRYING requires a target printer\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg)
        ams_id = int(flags.get("ams", flags.get("a", "0")))
        temp = int(flags.get("temp", flags.get("t", "45")))
        duration = int(flags.get("duration", flags.get("d", "4")))
        ok = printer_manager.send_drying_command(
            printer_id, ams_id, temp, duration, mode=1, filament=None, rotate_tray=False
        )
        msg = f"[AMS_DRYING] ams={ams_id} temp={temp} duration={duration}: {'ok' if ok else 'failed'}\n"
        await self._log(run_id, msg)
        return CommandResult(ok=ok, log=msg)

    async def _handle_printer_pause(self, printer_id: int | None, run_id: int | None) -> CommandResult:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            msg = "[ERROR] PRINTER_PAUSE requires a target printer\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg)
        client = printer_manager.get_client(printer_id)
        ok = client.pause_print() if client else False
        msg = f"[PRINTER_PAUSE]: {'ok' if ok else 'failed/not connected'}\n"
        await self._log(run_id, msg)
        return CommandResult(ok=ok, log=msg)

    async def _handle_printer_resume(self, printer_id: int | None, run_id: int | None) -> CommandResult:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            msg = "[ERROR] PRINTER_RESUME requires a target printer\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg)
        client = printer_manager.get_client(printer_id)
        ok = client.resume_print() if client else False
        msg = f"[PRINTER_RESUME]: {'ok' if ok else 'failed/not connected'}\n"
        await self._log(run_id, msg)
        return CommandResult(ok=ok, log=msg)

    async def _handle_printer_stop(self, printer_id: int | None, run_id: int | None) -> CommandResult:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            msg = "[ERROR] PRINTER_STOP requires a target printer\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg)
        client = printer_manager.get_client(printer_id)
        ok = client.stop_print() if client else False
        msg = f"[PRINTER_STOP]: {'ok' if ok else 'failed/not connected'}\n"
        await self._log(run_id, msg)
        return CommandResult(ok=ok, log=msg)

    async def _handle_notify(self, flags: dict, run_id: int | None) -> CommandResult:
        from sqlalchemy import select as sa_select

        from backend.app.models.notification import NotificationProvider

        message = flags.get("message", flags.get("m", "Macro notification"))
        message = message.strip("'\"")
        await self._log(run_id, f"[NOTIFY] {message}\n")
        try:
            from backend.app.services.notification_service import notification_service

            async with async_session() as db:
                result = await db.execute(sa_select(NotificationProvider).where(NotificationProvider.enabled.is_(True)))
                providers = list(result.scalars().all())
                if providers:
                    await notification_service._send_to_providers(
                        providers,
                        title="Macro Notification",
                        message=message,
                        db=db,
                        event_type="macro_notify",
                    )
        except Exception as exc:  # noqa: BLE001
            await self._log(run_id, f"[WARN] Notification dispatch failed: {exc}\n")
        return CommandResult(ok=True)

    async def _handle_wait(self, flags: dict, run_id: int | None) -> CommandResult:
        seconds = float(flags.get("seconds", flags.get("s", "1")))
        seconds = min(seconds, 300)  # hard cap
        await self._log(run_id, f"[WAIT] {seconds}s\n")
        await asyncio.sleep(seconds)
        return CommandResult(ok=True)

    async def _handle_wait_for_temp(self, flags: dict, printer_id: int | None, run_id: int | None) -> CommandResult:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            msg = "[ERROR] WAIT_FOR_TEMP requires a target printer\n"
            await self._log(run_id, msg)
            return CommandResult(ok=False, log=msg)
        target = float(flags.get("target", "200"))
        tolerance = float(flags.get("tolerance", "5"))
        max_wait = float(flags.get("max_wait", "300"))
        await self._log(run_id, f"[WAIT_FOR_TEMP] target={target}°C ±{tolerance}\n")
        elapsed = 0.0
        while elapsed < max_wait:
            client = printer_manager.get_client(printer_id)
            if client:
                current = client.state.temperatures.get("nozzle", 0.0)
                if abs(current - target) <= tolerance:
                    await self._log(run_id, f"[WAIT_FOR_TEMP] reached {current}°C\n")
                    return CommandResult(ok=True)
            await asyncio.sleep(2)
            elapsed += 2
        msg = f"[WARN] WAIT_FOR_TEMP timed out after {max_wait}s\n"
        await self._log(run_id, msg)
        return CommandResult(ok=False, log=msg)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

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
