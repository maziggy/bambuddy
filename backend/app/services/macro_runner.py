"""Macro execution engine.

Renders Jinja2 scripts with a sandboxed environment, then dispatches each
rendered line to the appropriate handler (G-code via MQTT or system commands).

Macros are stored as .jinja2 files on disk; the DB record holds metadata.
"""

import asyncio
import logging
import shlex
from datetime import datetime, timezone

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.macro import Macro, MacroRun
from backend.app.services import macro_files
from backend.app.services.gcode_whitelist import is_whitelisted

logger = logging.getLogger(__name__)

_jinja_env = SandboxedEnvironment(keep_trailing_newline=True)

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
            file_path = macro.file_path
            macro_name = macro.name
            await db.commit()

        try:
            script = macro_files.read(file_path)
        except FileNotFoundError:
            await self._finish_run(run_id, "error", f"[ERROR] Macro file not found: {file_path}\n")
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
            for raw_line in rendered.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                try:
                    await self._dispatch_line(line, printer_id, run_id, allow_printer_commands)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    await self._append_log(run_id, f"[ERROR] {line}: {exc}\n")
                    error_occurred = True
                    break

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
                    result = await db.execute(select(Macro).where(Macro.trigger_type == "schedule"))
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
            result = await db.execute(select(Macro).where(Macro.name == name))
            macro = result.scalar_one_or_none()
        if not macro:
            logger.warning("Sub-macro not found: %s", name)
            return
        try:
            script = macro_files.read(macro.file_path)
            context = await self._build_context(printer_id, call_stack)
            rendered = _jinja_env.from_string(script).render(**context)
        except Exception as exc:  # noqa: BLE001
            logger.error("Sub-macro %s render failed: %s", name, exc)
            return
        for raw_line in rendered.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            try:
                await self._dispatch_line(line, printer_id, run_id=None, allow_printer_commands=True)
            except Exception as exc:  # noqa: BLE001
                logger.error("Sub-macro %s dispatch error: %s", name, exc)
                break

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_line(
        self,
        line: str,
        printer_id: int | None,
        run_id: int | None,
        allow_printer_commands: bool = True,
    ) -> None:
        token = line.split()[0].upper()

        # G-code commands
        if is_whitelisted(line):
            if not allow_printer_commands:
                msg = f"[SKIP] G-code blocked in gcode_embed mode: {line}\n"
                if run_id:
                    await self._append_log(run_id, msg)
                else:
                    logger.warning(msg.strip())
                return
            await self._send_gcode(line, printer_id, run_id)
            return

        # System commands
        if token in _SYSTEM_COMMANDS:
            if not allow_printer_commands and token in _PRINTER_COMMANDS:
                msg = f"[SKIP] Command blocked in gcode_embed mode: {line}\n"
                if run_id:
                    await self._append_log(run_id, msg)
                else:
                    logger.warning(msg.strip())
                return
            await self._handle_system(token, line, printer_id, run_id)
            return

        # Unknown
        msg = f"[WARN] Unknown command (ignored): {line}\n"
        if run_id:
            await self._append_log(run_id, msg)
        else:
            logger.warning(msg.strip())

    async def _send_gcode(self, line: str, printer_id: int | None, run_id: int | None) -> None:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            msg = f"[WARN] No printer target for G-code: {line}\n"
        else:
            client = printer_manager.get_client(printer_id)
            if client:
                ok = client.send_gcode(line + "\n")
                msg = f"[GCODE] {line}\n" if ok else f"[ERROR] Failed to send G-code: {line}\n"
            else:
                msg = f"[ERROR] Printer {printer_id} not connected\n"

        if run_id:
            await self._append_log(run_id, msg)
        else:
            logger.info(msg.strip())

    async def _handle_system(
        self,
        token: str,
        line: str,
        printer_id: int | None,
        run_id: int | None,
    ) -> None:
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        flags = _parse_flags(tokens[1:])

        if token == "AMS_DRYING":
            await self._handle_ams_drying(flags, printer_id, run_id)
        elif token == "PRINTER_PAUSE":
            await self._handle_printer_pause(printer_id, run_id)
        elif token == "PRINTER_RESUME":
            await self._handle_printer_resume(printer_id, run_id)
        elif token == "PRINTER_STOP":
            await self._handle_printer_stop(printer_id, run_id)
        elif token == "NOTIFY":
            await self._handle_notify(flags, run_id)
        elif token == "WAIT":
            await self._handle_wait(flags, run_id)
        elif token == "WAIT_FOR_TEMP":
            await self._handle_wait_for_temp(flags, printer_id, run_id)

    # ------------------------------------------------------------------
    # System command handlers
    # ------------------------------------------------------------------

    async def _handle_ams_drying(self, flags: dict, printer_id: int | None, run_id: int | None) -> None:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            await self._log(run_id, "[ERROR] AMS_DRYING requires a target printer\n")
            return
        ams_id = int(flags.get("ams", flags.get("a", "0")))
        temp = int(flags.get("temp", flags.get("t", "45")))
        duration = int(flags.get("duration", flags.get("d", "4")))
        ok = printer_manager.send_drying_command(
            printer_id, ams_id, temp, duration, mode=1, filament=None, rotate_tray=False
        )
        msg = f"[AMS_DRYING] ams={ams_id} temp={temp} duration={duration}: {'ok' if ok else 'failed'}\n"
        await self._log(run_id, msg)

    async def _handle_printer_pause(self, printer_id: int | None, run_id: int | None) -> None:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            await self._log(run_id, "[ERROR] PRINTER_PAUSE requires a target printer\n")
            return
        client = printer_manager.get_client(printer_id)
        ok = client.pause_print() if client else False
        await self._log(run_id, f"[PRINTER_PAUSE]: {'ok' if ok else 'failed/not connected'}\n")

    async def _handle_printer_resume(self, printer_id: int | None, run_id: int | None) -> None:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            await self._log(run_id, "[ERROR] PRINTER_RESUME requires a target printer\n")
            return
        client = printer_manager.get_client(printer_id)
        ok = client.resume_print() if client else False
        await self._log(run_id, f"[PRINTER_RESUME]: {'ok' if ok else 'failed/not connected'}\n")

    async def _handle_printer_stop(self, printer_id: int | None, run_id: int | None) -> None:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            await self._log(run_id, "[ERROR] PRINTER_STOP requires a target printer\n")
            return
        client = printer_manager.get_client(printer_id)
        ok = client.stop_print() if client else False
        await self._log(run_id, f"[PRINTER_STOP]: {'ok' if ok else 'failed/not connected'}\n")

    async def _handle_notify(self, flags: dict, run_id: int | None) -> None:
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

    async def _handle_wait(self, flags: dict, run_id: int | None) -> None:
        seconds = float(flags.get("seconds", flags.get("s", "1")))
        seconds = min(seconds, 300)  # hard cap
        await self._log(run_id, f"[WAIT] {seconds}s\n")
        await asyncio.sleep(seconds)

    async def _handle_wait_for_temp(self, flags: dict, printer_id: int | None, run_id: int | None) -> None:
        from backend.app.services.printer_manager import printer_manager

        if printer_id is None:
            await self._log(run_id, "[ERROR] WAIT_FOR_TEMP requires a target printer\n")
            return
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
                    return
            await asyncio.sleep(2)
            elapsed += 2
        await self._log(run_id, f"[WAIT_FOR_TEMP] timed out after {max_wait}s\n")

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
