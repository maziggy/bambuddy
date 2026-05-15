"""Telegram command bot for interactive Bambuddy status requests."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.app.core.database import async_session
from backend.app.models.maintenance import PrinterMaintenance
from backend.app.models.notification import NotificationProvider
from backend.app.models.printer import Printer
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.spool import Spool
from backend.app.services.camera import capture_camera_frame_bytes
from backend.app.services.external_camera import capture_frame as capture_external_frame
from backend.app.services.hms_errors import get_error_description
from backend.app.services.printer_manager import get_derived_status_name, printer_manager, printer_state_to_dict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramBotProvider:
    id: int
    name: str
    bot_token: str
    chat_id: str
    control_commands_enabled: bool = False


def _telegram_html(text: str | None) -> str:
    return html.escape(str(text or "-"), quote=False)


def _normalize_lookup(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


class TelegramCommandBot:
    """Long-poll Telegram updates and answer read-only Bambuddy commands.

    The bot uses the existing Telegram notification providers. Only messages
    from the configured chat_id are accepted, so a leaked bot username alone
    does not expose printer status or camera snapshots.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._offsets: dict[str, int] = {}
        self._client: httpx.AsyncClient | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="telegram-command-bot")
        logger.info("Telegram command bot started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Telegram command bot stopped")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0))
        return self._client

    async def _load_providers(self) -> list[TelegramBotProvider]:
        async with async_session() as db:
            result = await db.execute(
                select(NotificationProvider)
                .where(NotificationProvider.provider_type == "telegram")
                .where(NotificationProvider.enabled.is_(True))
            )
            providers = []
            for provider in result.scalars().all():
                try:
                    config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
                except json.JSONDecodeError:
                    logger.warning("Skipping Telegram provider %s with invalid JSON config", provider.id)
                    continue

                bot_token = str(config.get("bot_token", "")).strip()
                chat_id = str(config.get("chat_id", "")).strip()
                if not bot_token or not chat_id:
                    continue
                commands_enabled = config.get("bot_commands_enabled", True)
                if str(commands_enabled).strip().lower() in ("false", "0", "no", "off", "disabled"):
                    continue
                control_commands_enabled = config.get("bot_control_commands_enabled", False)
                controls_enabled = str(control_commands_enabled).strip().lower() in ("true", "1", "yes", "on", "enabled")
                providers.append(TelegramBotProvider(provider.id, provider.name, bot_token, chat_id, controls_enabled))
            return providers

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                providers = await self._load_providers()
                if not providers:
                    await asyncio.sleep(30)
                    continue

                for provider in providers:
                    await self._poll_provider(provider)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram command bot loop failed")
                await asyncio.sleep(10)

    async def _poll_provider(self, provider: TelegramBotProvider) -> None:
        client = await self._get_client()
        if provider.bot_token not in self._offsets:
            # Mark existing backlog as consumed on startup so old chats with the
            # bot do not trigger a burst of stale Bambuddy replies.
            response = await client.get(
                f"https://api.telegram.org/bot{provider.bot_token}/getUpdates",
                params={"timeout": 0, "allowed_updates": json.dumps(["message"])},
            )
            if response.status_code == 200:
                data = response.json()
                updates = data.get("result", []) if data.get("ok") else []
                update_ids = [u.get("update_id") for u in updates if isinstance(u.get("update_id"), int)]
                self._offsets[provider.bot_token] = (max(update_ids) + 1) if update_ids else 0
            else:
                logger.warning(
                    "Telegram startup getUpdates failed for provider %s: HTTP %s",
                    provider.id,
                    response.status_code,
                )
                await asyncio.sleep(5)
                return

        params: dict[str, Any] = {
            "timeout": 25,
            "allowed_updates": json.dumps(["message"]),
        }
        offset = self._offsets.get(provider.bot_token)
        if offset is not None:
            params["offset"] = offset

        response = await client.get(f"https://api.telegram.org/bot{provider.bot_token}/getUpdates", params=params)
        if response.status_code != 200:
            logger.warning("Telegram getUpdates failed for provider %s: HTTP %s", provider.id, response.status_code)
            await asyncio.sleep(5)
            return

        data = response.json()
        if not data.get("ok"):
            logger.warning("Telegram getUpdates rejected for provider %s: %s", provider.id, data.get("description"))
            await asyncio.sleep(5)
            return

        for update in data.get("result", []):
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offsets[provider.bot_token] = update_id + 1
            message = update.get("message") or {}
            await self._handle_message(provider, message)

    async def _handle_message(self, provider: TelegramBotProvider, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        if chat_id != provider.chat_id:
            if chat_id:
                await self._send_message(
                    provider,
                    chat_id,
                    "Dieser Bambuddy-Bot ist auf einen anderen Chat beschraenkt.",
                )
            logger.warning("Rejected Telegram command from unauthorized chat %s for provider %s", chat_id, provider.id)
            return

        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        command, _, arg = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        arg = arg.strip()

        if command in ("/help", "/start"):
            await self._send_message(provider, provider.chat_id, self._help_text(provider))
        elif command in ("/printers", "/drucker"):
            await self._send_message(provider, provider.chat_id, await self._printers_text())
        elif command in ("/dashboard", "/overview", "/uebersicht"):
            await self._send_message(provider, provider.chat_id, await self._dashboard_text())
        elif command == "/status":
            await self._send_message(provider, provider.chat_id, await self._status_text(arg))
        elif command == "/eta":
            await self._send_message(provider, provider.chat_id, await self._eta_text(arg))
        elif command in ("/errors", "/fehler", "/warnungen"):
            await self._send_message(provider, provider.chat_id, await self._errors_text(arg))
        elif command == "/ams":
            await self._send_message(provider, provider.chat_id, await self._ams_text(arg))
        elif command in ("/filament", "/spools"):
            await self._send_message(provider, provider.chat_id, await self._filament_text(arg))
        elif command in ("/history", "/log", "/verlauf"):
            await self._send_message(provider, provider.chat_id, await self._history_text(arg))
        elif command in ("/maintenance", "/wartung"):
            await self._send_message(provider, provider.chat_id, await self._maintenance_text(arg))
        elif command in ("/photo", "/foto", "/bild", "/camera", "/kamera"):
            await self._send_photo_command(provider, arg)
        elif command == "/queue":
            await self._send_message(provider, provider.chat_id, await self._queue_text())
        elif command in ("/pause", "/resume", "/stop", "/light", "/clearplate", "/plateclear", "/startqueue"):
            await self._send_message(provider, provider.chat_id, await self._control_text(provider, command, arg))
        else:
            await self._send_message(
                provider,
                provider.chat_id,
                "Unbekannter Befehl. Sende /help fuer die verfuegbaren Befehle.",
            )

    def _help_text(self, provider: TelegramBotProvider) -> str:
        text = (
            "<b>Bambuddy Telegram-Bot</b>\n\n"
            "/printers - Drucker auflisten\n"
            "/dashboard - kompakte Uebersicht\n"
            "/status - Status aller Drucker\n"
            "/status &lt;drucker&gt; - Status eines Druckers\n"
            "/eta - Restzeiten laufender Drucke\n"
            "/errors - Warnungen und HMS-Fehler\n"
            "/ams &lt;drucker&gt; - AMS/Tray-Status\n"
            "/filament [gramm] - niedrige Spulenbestaende\n"
            "/history [anzahl] - letzte Drucke\n"
            "/maintenance - Wartungsuebersicht\n"
            "/photo &lt;drucker&gt; - aktuelles Kamerabild\n"
            "/queue - Warteschlange anzeigen\n"
            "/help - Hilfe"
        )
        if provider.control_commands_enabled:
            text += (
                "\n\n<b>Steuerung</b>\n"
                "/pause &lt;drucker&gt; - Druck pausieren\n"
                "/resume &lt;drucker&gt; - Druck fortsetzen\n"
                "/stop &lt;drucker&gt; confirm - Druck stoppen\n"
                "/light &lt;drucker&gt; on|off - Licht schalten\n"
                "/clearplate &lt;drucker&gt; - Platte als frei markieren\n"
                "/startqueue &lt;queue-id&gt; - manuellen Queue-Job freigeben"
            )
        else:
            text += "\n\nSteuerbefehle sind fuer diesen Telegram Provider deaktiviert."
        return text

    async def _get_printers(self) -> list[Printer]:
        async with async_session() as db:
            result = await db.execute(select(Printer).where(Printer.is_active.is_(True)).order_by(Printer.name))
            return list(result.scalars().all())

    async def _find_printer(self, query: str) -> Printer | None:
        printers = await self._get_printers()
        if not query and len(printers) == 1:
            return printers[0]
        wanted = _normalize_lookup(query)
        if not wanted:
            return None
        for printer in printers:
            candidates = [
                str(printer.id),
                printer.name or "",
                printer.model or "",
                f"{printer.name} {printer.model}",
            ]
            if any(wanted == _normalize_lookup(c) for c in candidates):
                return printer
        for printer in printers:
            candidates = [printer.name or "", printer.model or "", f"{printer.name} {printer.model}"]
            if any(wanted in _normalize_lookup(c) for c in candidates):
                return printer
        return None

    async def _printers_text(self) -> str:
        printers = await self._get_printers()
        if not printers:
            return "Keine aktiven Drucker in Bambuddy gefunden."
        lines = ["<b>Aktive Drucker</b>"]
        for printer in printers:
            lines.append(f"{printer.id}: {_telegram_html(printer.name)} ({_telegram_html(printer.model)})")
        return "\n".join(lines)

    async def _status_text(self, query: str) -> str:
        if query:
            printer = await self._find_printer(query)
            if not printer:
                return f"Keinen Drucker fuer '{_telegram_html(query)}' gefunden. Sende /printers fuer die Liste."
            return self._format_printer_status(printer)

        printers = await self._get_printers()
        if not printers:
            return "Keine aktiven Drucker in Bambuddy gefunden."
        return "\n\n".join(self._format_printer_status(printer) for printer in printers)

    async def _dashboard_text(self) -> str:
        printers = await self._get_printers()
        if not printers:
            return "Keine aktiven Drucker in Bambuddy gefunden."

        running = 0
        paused = 0
        offline = 0
        warning_count = 0
        lines = ["<b>Bambuddy Dashboard</b>"]
        for printer in printers:
            state = printer_manager.get_status(printer.id)
            if not state or not state.connected:
                offline += 1
                lines.append(f"{_telegram_html(printer.name)}: offline")
                continue

            status_name = get_derived_status_name(state, printer.model) or state.state or "-"
            if state.state == "RUNNING":
                running += 1
            elif state.state == "PAUSE":
                paused += 1
            warning_count += len(state.hms_errors or [])

            parts = [f"{_telegram_html(printer.name)}: {_telegram_html(status_name)}"]
            if state.progress is not None and state.state in ("RUNNING", "PAUSE"):
                parts.append(f"{state.progress:.0f}%")
            if state.remaining_time:
                parts.append(f"{state.remaining_time} min")
            if state.current_print or state.subtask_name:
                parts.append(_telegram_html(state.current_print or state.subtask_name))
            lines.append(" - ".join(parts))

        lines.insert(
            1,
            f"Aktiv: {len(printers) - offline}, laufend: {running}, pausiert: {paused}, offline: {offline}, Warnungen: {warning_count}",
        )
        return "\n".join(lines)

    async def _eta_text(self, query: str) -> str:
        printers = [await self._find_printer(query)] if query else await self._get_printers()
        printers = [printer for printer in printers if printer]
        if not printers:
            return f"Keinen Drucker fuer '{_telegram_html(query)}' gefunden. Sende /printers fuer die Liste."

        running_lines = []
        idle_lines = []
        for printer in printers:
            state = printer_manager.get_status(printer.id)
            if not state or not state.connected:
                idle_lines.append(f"{_telegram_html(printer.name)}: offline")
                continue
            if state.state not in ("RUNNING", "PAUSE"):
                idle_lines.append(f"{_telegram_html(printer.name)}: {_telegram_html(state.state)}")
                continue

            name = state.current_print or state.subtask_name or state.gcode_file or "Aktueller Druck"
            eta = f"{state.remaining_time} min" if state.remaining_time is not None else "unbekannt"
            progress = f"{state.progress:.0f}%" if state.progress is not None else "-"
            running_lines.append(f"{_telegram_html(printer.name)}: {progress}, Restzeit {eta} - {_telegram_html(name)}")

        lines = ["<b>ETA</b>"]
        lines.extend(running_lines or ["Keine laufenden Drucke."])
        if idle_lines and not query:
            lines.append("")
            lines.append("<b>Nicht laufend</b>")
            lines.extend(idle_lines)
        return "\n".join(lines)

    async def _errors_text(self, query: str) -> str:
        printers = [await self._find_printer(query)] if query else await self._get_printers()
        printers = [printer for printer in printers if printer]
        if not printers:
            return f"Keinen Drucker fuer '{_telegram_html(query)}' gefunden. Sende /printers fuer die Liste."

        lines = ["<b>Warnungen und Fehler</b>"]
        found = False
        for printer in printers:
            state = printer_manager.get_status(printer.id)
            errors = list(getattr(state, "hms_errors", []) or []) if state else []
            if not errors:
                continue
            found = True
            lines.append("")
            lines.append(f"<b>{_telegram_html(printer.name)}</b>")
            for error in errors[:8]:
                code = self._hms_short_code(error)
                raw_code = getattr(error, "code", "-")
                attr = getattr(error, "attr", 0) or 0
                severity = self._severity_name(getattr(error, "severity", 0))
                module = getattr(error, "module", "-")
                description = get_error_description(code) or getattr(error, "message", "") or "Keine Beschreibung verfuegbar"
                lines.append(f"{_telegram_html(severity)} M{_telegram_html(module)} {code}")
                lines.append(f"Raw: {_telegram_html(raw_code)}, attr: 0x{int(attr):08X}")
                lines.append(_telegram_html(self._shorten(description, 240)))
            if len(errors) > 8:
                lines.append(f"... und {len(errors) - 8} weitere")
        if not found:
            lines.append("Keine aktuellen HMS-Warnungen gefunden.")
        return "\n".join(lines)

    async def _ams_text(self, query: str) -> str:
        printer = await self._find_printer(query)
        if not printer:
            return f"Keinen Drucker fuer '{_telegram_html(query)}' gefunden. Sende /printers fuer die Liste."

        state = printer_manager.get_status(printer.id)
        if not state:
            return f"Kein aktueller Status fuer {_telegram_html(printer.name)} verfuegbar."

        status = printer_state_to_dict(state, printer.id, printer.model)
        ams_units = status.get("ams") or []
        vt_tray = status.get("vt_tray") or []
        if not ams_units and not vt_tray:
            return f"Keine AMS- oder Tray-Daten fuer {_telegram_html(printer.name)} verfuegbar."

        lines = [f"<b>AMS: {_telegram_html(printer.name)}</b>"]
        for ams in ams_units:
            header_parts = [f"AMS {ams.get('id', 0)}"]
            if ams.get("humidity") is not None:
                header_parts.append(f"Feuchte {ams.get('humidity')}%")
            if ams.get("temp") is not None:
                header_parts.append(f"{ams.get('temp')} deg C")
            if ams.get("dry_time"):
                header_parts.append(f"Trocknen {ams.get('dry_time')} min")
            lines.append("")
            lines.append("<b>" + _telegram_html(" - ".join(header_parts)) + "</b>")
            for tray in ams.get("tray") or []:
                tray_name = tray.get("tray_id_name") or tray.get("tray_sub_brands") or tray.get("tray_type") or "unbekannt"
                color = tray.get("tray_color") or ""
                remain = tray.get("remain")
                remain_text = f", {remain}%" if remain not in (None, "") else ""
                color_text = f", {color}" if color else ""
                lines.append(
                    f"Slot {tray.get('id', '-')}: {_telegram_html(tray_name)}"
                    f"{_telegram_html(color_text)}{remain_text}"
                )

        for tray in vt_tray:
            tray_name = tray.get("tray_id_name") or tray.get("tray_sub_brands") or tray.get("tray_type") or "externe Spule"
            lines.append(f"Extern: {_telegram_html(tray_name)}")
        return "\n".join(lines)

    async def _filament_text(self, arg: str) -> str:
        threshold = self._parse_int_arg(arg, default=200, minimum=1, maximum=5000)
        async with async_session() as db:
            result = await db.execute(
                select(Spool)
                .where(Spool.archived_at.is_(None))
                .order_by((Spool.label_weight - Spool.weight_used).asc())
                .limit(20)
            )
            spools = list(result.scalars().all())

        low_spools = []
        for spool in spools:
            remaining = max(float(spool.label_weight or 0) - float(spool.weight_used or 0), 0)
            if remaining <= threshold:
                low_spools.append((spool, remaining))

        lines = [f"<b>Filament unter {threshold} g</b>"]
        if not low_spools:
            lines.append("Keine aktiven Spulen unter diesem Grenzwert gefunden.")
            return "\n".join(lines)

        for spool, remaining in low_spools[:15]:
            label = " ".join(part for part in [spool.brand, spool.material, spool.subtype, spool.color_name] if part)
            lines.append(f"#{spool.id}: {_telegram_html(label or 'Spule')} - {remaining:.0f} g")
        return "\n".join(lines)

    async def _history_text(self, arg: str) -> str:
        limit = self._parse_int_arg(arg, default=5, minimum=1, maximum=15)
        async with async_session() as db:
            result = await db.execute(select(PrintLogEntry).order_by(PrintLogEntry.created_at.desc()).limit(limit))
            entries = list(result.scalars().all())

        lines = [f"<b>Letzte {limit} Drucke</b>"]
        if not entries:
            lines.append("Noch keine Druckhistorie gefunden.")
            return "\n".join(lines)

        for entry in entries:
            duration = self._format_duration(entry.duration_seconds)
            filament = f", {entry.filament_used_grams:.0f} g" if entry.filament_used_grams else ""
            completed = self._format_datetime(entry.completed_at or entry.created_at)
            lines.append(
                f"{completed}: {_telegram_html(entry.print_name or 'Druck')} "
                f"auf {_telegram_html(entry.printer_name or '-')}: {_telegram_html(entry.status)}"
                f"{duration}{filament}"
            )
        return "\n".join(lines)

    async def _maintenance_text(self, query: str) -> str:
        printer = await self._find_printer(query) if query else None
        if query and not printer:
            return f"Keinen Drucker fuer '{_telegram_html(query)}' gefunden. Sende /printers fuer die Liste."

        async with async_session() as db:
            stmt = (
                select(PrinterMaintenance)
                .options(selectinload(PrinterMaintenance.printer), selectinload(PrinterMaintenance.maintenance_type))
                .where(PrinterMaintenance.enabled.is_(True))
            )
            if printer:
                stmt = stmt.where(PrinterMaintenance.printer_id == printer.id)
            result = await db.execute(stmt)
            items = list(result.scalars().all())

        rows = []
        for item in items:
            printer_hours = ((item.printer.runtime_seconds or 0) / 3600.0) + float(item.printer.print_hours_offset or 0)
            interval = item.custom_interval_hours or item.maintenance_type.default_interval_hours or 0
            since = max(printer_hours - float(item.last_performed_hours or 0), 0)
            remaining = float(interval) - since
            rows.append((remaining, item, since, interval))

        rows.sort(key=lambda row: row[0])
        lines = ["<b>Wartung</b>"]
        if not rows:
            lines.append("Keine aktiven Wartungseintraege gefunden.")
            return "\n".join(lines)

        for remaining, item, since, interval in rows[:12]:
            status = "faellig" if remaining <= 0 else f"in {remaining:.0f} h"
            lines.append(
                f"{_telegram_html(item.printer.name)} - {_telegram_html(item.maintenance_type.name)}: "
                f"{status} ({since:.0f}/{interval:.0f} h)"
            )
        return "\n".join(lines)

    async def _control_text(self, provider: TelegramBotProvider, command: str, arg: str) -> str:
        if not provider.control_commands_enabled:
            return (
                "Steuerbefehle sind fuer diesen Telegram Provider deaktiviert. "
                "Aktiviere zuerst 'Telegram control commands' in den Benachrichtigungs-Einstellungen."
            )

        if command == "/startqueue":
            queue_id = self._parse_int_arg(arg, default=0, minimum=0, maximum=2_147_483_647)
            if not queue_id:
                return "Bitte Queue-ID angeben, z. B. /startqueue 12"
            return await self._start_queue_item(queue_id)

        if command == "/light":
            printer_query, action = self._split_last_token(arg)
            if action not in ("on", "off", "an", "aus"):
                return "Bitte Lichtbefehl so senden: /light <drucker> on oder /light <drucker> off"
            printer = await self._find_printer(printer_query)
            if not printer:
                return f"Keinen Drucker fuer '{_telegram_html(printer_query)}' gefunden. Sende /printers fuer die Liste."
            return await self._set_light(printer, action in ("on", "an"))

        confirm = False
        printer_query = arg
        if command == "/stop":
            printer_query, last = self._split_last_token(arg)
            confirm = last == "confirm"
            if not confirm:
                return (
                    "Stoppen ist absichtlich bestaetigungspflichtig. "
                    "Sende: /stop <drucker> confirm"
                )

        printer = await self._find_printer(printer_query)
        if not printer:
            return f"Keinen Drucker fuer '{_telegram_html(printer_query)}' gefunden. Sende /printers fuer die Liste."

        if command == "/pause":
            return await self._send_printer_control(printer, "pause")
        if command == "/resume":
            return await self._send_printer_control(printer, "resume")
        if command == "/stop" and confirm:
            return await self._send_printer_control(printer, "stop")
        if command in ("/clearplate", "/plateclear"):
            printer_manager.set_awaiting_plate_clear(printer.id, False)
            return f"Platte fuer {_telegram_html(printer.name)} als frei markiert."

        return "Unbekannter Steuerbefehl."

    async def _send_printer_control(self, printer: Printer, action: str) -> str:
        client = printer_manager.get_client(printer.id)
        if not client:
            return f"{_telegram_html(printer.name)} ist nicht verbunden."

        if action == "pause":
            success = client.pause_print()
            label = "Pause"
        elif action == "resume":
            success = client.resume_print()
            label = "Resume"
        elif action == "stop":
            success = client.stop_print()
            label = "Stop"
        else:
            return "Unbekannte Aktion."

        if success:
            return f"{label}-Befehl an {_telegram_html(printer.name)} gesendet."
        return f"{label}-Befehl fuer {_telegram_html(printer.name)} konnte nicht gesendet werden."

    async def _set_light(self, printer: Printer, on: bool) -> str:
        client = printer_manager.get_client(printer.id)
        if not client:
            return f"{_telegram_html(printer.name)} ist nicht verbunden."
        success = client.set_chamber_light(on)
        if success:
            return f"Licht bei {_telegram_html(printer.name)} {'eingeschaltet' if on else 'ausgeschaltet'}."
        return f"Licht bei {_telegram_html(printer.name)} konnte nicht geschaltet werden."

    async def _start_queue_item(self, queue_id: int) -> str:
        async with async_session() as db:
            result = await db.execute(
                select(PrintQueueItem)
                .options(
                    selectinload(PrintQueueItem.archive),
                    selectinload(PrintQueueItem.library_file),
                    selectinload(PrintQueueItem.printer),
                )
                .where(PrintQueueItem.id == queue_id)
            )
            item = result.scalar_one_or_none()
            if not item:
                return f"Queue-Job #{queue_id} nicht gefunden."
            if item.status != "pending":
                return f"Queue-Job #{queue_id} kann nicht gestartet werden, Status ist '{_telegram_html(item.status)}'."

            item.manual_start = False
            await db.commit()

            name = self._queue_item_name(item)
            target = self._queue_target_name(item)
            return f"Queue-Job #{queue_id} freigegeben: {_telegram_html(name)} -> {_telegram_html(target)}"

    def _format_printer_status(self, printer: Printer) -> str:
        state = printer_manager.get_status(printer.id)
        if not state:
            return f"<b>{_telegram_html(printer.name)}</b>\nOffline oder noch kein Status verfuegbar."

        status_name = get_derived_status_name(state, printer.model) or state.state or "-"
        lines = [
            f"<b>{_telegram_html(printer.name)}</b>",
            f"Status: {_telegram_html(status_name)}",
            f"Verbunden: {'ja' if state.connected else 'nein'}",
        ]
        if state.current_print or state.subtask_name or state.gcode_file:
            lines.append(f"Druck: {_telegram_html(state.current_print or state.subtask_name or state.gcode_file)}")
        if state.progress is not None:
            lines.append(f"Fortschritt: {state.progress:.0f}%")
        if state.remaining_time is not None:
            lines.append(f"Restzeit: {state.remaining_time} min")
        if state.layer_num and state.total_layers:
            lines.append(f"Layer: {state.layer_num}/{state.total_layers}")
        temperatures = state.temperatures or {}
        nozzle = temperatures.get("nozzle")
        bed = temperatures.get("bed")
        if nozzle is not None or bed is not None:
            temp_parts = []
            if nozzle is not None:
                temp_parts.append(f"Nozzle {float(nozzle):.0f} deg C")
            if bed is not None:
                temp_parts.append(f"Bett {float(bed):.0f} deg C")
            lines.append("Temperatur: " + ", ".join(temp_parts))
        if state.hms_errors:
            lines.append(f"Warnungen/Fehler: {len(state.hms_errors)}")
        return "\n".join(lines)

    async def _queue_text(self) -> str:
        async with async_session() as db:
            counts_result = await db.execute(select(PrintQueueItem.status, func.count()).group_by(PrintQueueItem.status))
            counts = {status: count for status, count in counts_result.all()}

            result = await db.execute(
                select(PrintQueueItem)
                .options(
                    selectinload(PrintQueueItem.archive),
                    selectinload(PrintQueueItem.library_file),
                    selectinload(PrintQueueItem.printer),
                )
                .where(PrintQueueItem.status.in_(["pending", "printing", "waiting"]))
                .order_by(PrintQueueItem.printer_id.nulls_first(), PrintQueueItem.position, PrintQueueItem.created_at)
                .limit(15)
            )
            items = list(result.scalars().all())

        lines = [
            "<b>Warteschlange</b>",
            f"Pending: {counts.get('pending', 0)}",
            f"Printing: {counts.get('printing', 0)}",
            f"Completed: {counts.get('completed', 0)}",
            f"Failed: {counts.get('failed', 0)}",
        ]
        if items:
            lines.append("")
            lines.append("<b>Jobs nach Drucker</b>")
            current_target = None
            for item in items:
                target = self._queue_target_name(item)
                if target != current_target:
                    lines.append("")
                    lines.append(f"<b>{_telegram_html(target)}</b>")
                    current_target = target

                name = self._queue_item_name(item)
                waiting_reason = f" - {_telegram_html(item.waiting_reason)}" if item.waiting_reason else ""
                manual = " manuell" if item.manual_start else ""
                lines.append(
                    f"#{item.id} pos {item.position}: {_telegram_html(name)} "
                    f"({item.status}{manual}){waiting_reason}"
                )
        return "\n".join(lines)

    def _queue_item_name(self, item: PrintQueueItem) -> str:
        if item.archive:
            return item.archive.print_name or item.archive.filename
        if item.library_file:
            return item.library_file.filename
        if item.external_order_number:
            return f"Bestellung {item.external_order_number}"
        return "Unbekannte Datei"

    def _queue_target_name(self, item: PrintQueueItem) -> str:
        if item.printer:
            return item.printer.name
        if item.target_model:
            if item.target_location:
                return f"Any {item.target_model} @ {item.target_location}"
            return f"Any {item.target_model}"
        return "Unzugeordnet"

    async def _send_photo_command(self, provider: TelegramBotProvider, query: str) -> None:
        printer = await self._find_printer(query)
        if not printer:
            await self._send_message(
                provider,
                provider.chat_id,
                f"Keinen Drucker fuer '{_telegram_html(query)}' gefunden. Sende /printers fuer die Liste.",
            )
            return

        await self._send_message(provider, provider.chat_id, f"Kamerabild von {_telegram_html(printer.name)} wird geladen...")
        image_data = await self._capture_snapshot(printer)
        if not image_data:
            await self._send_message(provider, provider.chat_id, f"Konnte kein Kamerabild von {_telegram_html(printer.name)} holen.")
            return
        caption = self._format_printer_status(printer)
        await self._send_photo(provider, provider.chat_id, image_data, caption)

    async def _capture_snapshot(self, printer: Printer) -> bytes | None:
        try:
            if printer.external_camera_enabled and printer.external_camera_url:
                return await capture_external_frame(
                    printer.external_camera_url, printer.external_camera_type or "mjpeg", timeout=15
                )
            return await capture_camera_frame_bytes(printer.ip_address, printer.access_code, printer.model, timeout=15)
        except Exception:
            logger.exception("Telegram photo capture failed for printer %s", printer.id)
            return None

    def _parse_int_arg(self, value: str, default: int, minimum: int, maximum: int) -> int:
        match = re.search(r"\d+", value or "")
        if not match:
            return default
        parsed = int(match.group(0))
        return max(minimum, min(maximum, parsed))

    def _format_duration(self, seconds: int | None) -> str:
        if not seconds:
            return ""
        minutes = max(int(seconds // 60), 1)
        hours, remainder = divmod(minutes, 60)
        if hours:
            return f", {hours}h {remainder}m"
        return f", {remainder}m"

    def _format_datetime(self, value: datetime | None) -> str:
        if not value:
            return "-"
        return value.strftime("%d.%m. %H:%M")

    def _split_last_token(self, value: str) -> tuple[str, str]:
        parts = (value or "").strip().rsplit(" ", 1)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0].strip(), parts[1].strip().lower()

    def _severity_name(self, severity: int | str | None) -> str:
        try:
            value = int(severity or 0)
        except (TypeError, ValueError):
            value = 0
        return {
            1: "Fatal",
            2: "Serious",
            3: "Warning",
            4: "Info",
        }.get(value, f"Severity {value}" if value else "Unknown")

    def _hms_short_code(self, error: Any) -> str:
        raw_code = str(getattr(error, "code", "") or "")
        if "_" in raw_code:
            return raw_code.upper()

        try:
            error_code = int(raw_code.replace("0x", ""), 16)
        except ValueError:
            error_code = 0

        attr = int(getattr(error, "attr", 0) or 0)
        if (attr & 0xFFFF) == error_code:
            module = (attr >> 16) & 0xFFFF
        else:
            module = (int(getattr(error, "module", 0) or 0) & 0xFF) << 8
        return f"{module:04X}_{error_code:04X}"

    def _shorten(self, value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    async def _send_message(self, provider: TelegramBotProvider, chat_id: str, text: str) -> None:
        client = await self._get_client()
        response = await client.post(
            f"https://api.telegram.org/bot{provider.bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if response.status_code != 200:
            logger.warning("Telegram sendMessage failed for provider %s: HTTP %s", provider.id, response.status_code)

    async def _send_photo(self, provider: TelegramBotProvider, chat_id: str, image_data: bytes, caption: str) -> None:
        client = await self._get_client()
        response = await client.post(
            f"https://api.telegram.org/bot{provider.bot_token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("snapshot.jpg", image_data, "image/jpeg")},
        )
        if response.status_code != 200:
            logger.warning("Telegram sendPhoto failed for provider %s: HTTP %s", provider.id, response.status_code)


telegram_command_bot = TelegramCommandBot()
