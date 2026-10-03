"""Optional per-printer WLED preset integration."""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from backend.app.core.tasks import spawn_background_task
from backend.app.schemas.printer import WLEDConfig
from backend.app.services.bambu_mqtt import PrinterState

logger = logging.getLogger(__name__)


class WLEDResponseError(ValueError):
    """WLED returned JSON that does not match the expected API shape."""


def effective_wled_status(state: PrinterState, *, awaiting_plate_clear: bool = False) -> str | None:
    """Map Bambuddy's existing printer state to a WLED mapping key."""
    if not state.connected:
        return "offline"

    printer_state = (state.state or "").upper()
    if printer_state == "PAUSE" and (state.ams_status_main == 1 or state.mc_print_sub_stage not in (None, 0)):
        return "filament_problem"
    if state.hms_errors:
        return "hms_error"
    if printer_state == "FAILED":
        return "error"
    if printer_state == "PAUSE":
        return "paused"
    if printer_state == "FINISH":
        return "finished"
    if awaiting_plate_clear and printer_state == "IDLE":
        return "queue_waiting"
    if printer_state in {"RUNNING", "PRINTING"}:
        return "printing"
    if printer_state in {"PREPARE", "SLICING"}:
        return "prepare"
    if printer_state == "IDLE":
        return "idle"
    return None


@dataclass
class _WLEDRuntime:
    status: str | None = None
    preset_id: int | None = None
    generation: int = 0
    send_task: asyncio.Task | None = None
    finished_task: asyncio.Task | None = None


class WLEDManager:
    """Own WLED HTTP calls, per-printer deduplication, and finished timers."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None
        self._configs: dict[int, WLEDConfig] = {}
        self._runtime: dict[int, _WLEDRuntime] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(3.0, connect=2.0),
                follow_redirects=False,
            )
        return self._client

    def configure_printer(self, printer_id: int, config: dict | WLEDConfig | None) -> None:
        self._cancel_runtime(printer_id, remove=True)
        try:
            self._configs[printer_id] = WLEDConfig.model_validate(config or {})
        except ValueError:
            self._configs[printer_id] = WLEDConfig()
            logger.warning("Ignoring invalid WLED configuration for printer %d", printer_id)

    def remove_printer(self, printer_id: int) -> None:
        self._configs.pop(printer_id, None)
        self._cancel_runtime(printer_id, remove=True)

    def handle_status(
        self,
        printer_id: int,
        state: PrinterState,
        *,
        awaiting_plate_clear: bool = False,
    ) -> None:
        """Schedule any needed WLED work without delaying printer handling."""
        config = self._configs.get(printer_id)
        if not config or not config.enabled or not config.base_url:
            self._cancel_runtime(printer_id)
            return

        status = effective_wled_status(state, awaiting_plate_clear=awaiting_plate_clear)
        preset_id = getattr(config.presets, status, None) if status else None
        runtime = self._runtime.setdefault(printer_id, _WLEDRuntime())
        if runtime.status == status and runtime.preset_id == preset_id:
            return

        runtime.generation += 1
        generation = runtime.generation
        self._cancel_task(runtime.finished_task)
        runtime.finished_task = None
        runtime.status = status
        runtime.preset_id = preset_id

        if preset_id is not None:
            self._cancel_task(runtime.send_task)
            runtime.send_task = spawn_background_task(
                self.send_preset(printer_id, config.base_url, preset_id),
                name=f"wled-preset-{printer_id}",
            )

        if status == "finished" and config.finished_timeout_seconds and config.presets.idle is not None:
            runtime.finished_task = spawn_background_task(
                self._finish_timeout(
                    printer_id,
                    generation,
                    config.base_url,
                    config.presets.idle,
                    config.finished_timeout_seconds,
                ),
                name=f"wled-finished-timeout-{printer_id}",
            )

    async def send_preset(self, printer_id: int, base_url: str, preset_id: int) -> bool:
        """Activate a preset. Failures are isolated from all printer work."""
        try:
            response = await self.client.post(f"{base_url}/json/state", json={"ps": preset_id})
            response.raise_for_status()
            if not isinstance(response.json(), dict):
                raise WLEDResponseError("state response must be a JSON object")
            return True
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "WLED preset request failed for printer %d with HTTP %d",
                printer_id,
                exc.response.status_code,
            )
        except (httpx.HTTPError, ValueError):
            logger.warning("WLED preset request failed for printer %d", printer_id)
        return False

    async def list_presets(self, base_url: str) -> list[dict[str, int | str]]:
        """Return WLED preset names and IDs without modifying WLED state."""
        response = await self.client.get(f"{base_url}/presets.json")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise WLEDResponseError("presets response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise WLEDResponseError("presets response must be a JSON object")

        presets = []
        for raw_id, value in payload.items():
            try:
                preset_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if not 1 <= preset_id <= 250 or not isinstance(value, dict):
                continue
            name = value.get("n")
            presets.append(
                {"id": preset_id, "name": name.strip() if isinstance(name, str) and name.strip() else str(preset_id)}
            )
        return sorted(presets, key=lambda item: int(item["id"]))

    async def get_info(self, base_url: str) -> dict[str, str | None]:
        """Return the optional name and version reported by WLED."""
        response = await self.client.get(f"{base_url}/json/info")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise WLEDResponseError("info response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise WLEDResponseError("info response must be a JSON object")

        def clean(value) -> str | None:
            return value.strip() if isinstance(value, str) and value.strip() else None

        return {"name": clean(payload.get("name")), "version": clean(payload.get("ver"))}

    async def shutdown(self) -> None:
        tasks: list[asyncio.Task] = []
        for runtime in self._runtime.values():
            for task in (runtime.send_task, runtime.finished_task):
                if task and not task.done():
                    task.cancel()
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runtime.clear()
        self._configs.clear()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    async def _finish_timeout(
        self,
        printer_id: int,
        generation: int,
        base_url: str,
        idle_preset_id: int,
        timeout_seconds: int,
    ) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
            runtime = self._runtime.get(printer_id)
            if not runtime or runtime.generation != generation or runtime.status != "finished":
                return
            await self.send_preset(printer_id, base_url, idle_preset_id)
        except asyncio.CancelledError:
            return

    def _cancel_runtime(self, printer_id: int, *, remove: bool = False) -> None:
        runtime = self._runtime.get(printer_id)
        if runtime:
            runtime.generation += 1
            self._cancel_task(runtime.send_task)
            self._cancel_task(runtime.finished_task)
        if remove:
            self._runtime.pop(printer_id, None)

    @staticmethod
    def _cancel_task(task: asyncio.Task | None) -> None:
        if task and not task.done():
            task.cancel()


wled_manager = WLEDManager()
