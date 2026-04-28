"""HTTP client for an OrcaSlicer / BambuStudio API sidecar.

Bambuddy stores user printer/process/filament profiles itself (cloud-synced
or locally imported), so the slice flow always sends the model file plus an
explicit JSON profile triplet to the sidecar's `/slice` endpoint. The sidecar
shape mirrors `AFKFelix/orca-slicer-api` (multipart upload, `--load-settings`
under the hood, response body is raw G-code or 3MF with metadata in the
`X-Print-Time-Seconds` / `X-Filament-Used-G` / `X-Filament-Used-Mm` headers).
"""

import asyncio
import logging
from collections.abc import Callable
from typing import NamedTuple

import httpx

logger = logging.getLogger(__name__)


class SlicerApiError(Exception):
    """Base error from the slicer API sidecar."""


class SlicerApiUnavailableError(SlicerApiError):
    """Sidecar is unreachable (connection error, no response)."""


class SlicerApiServerError(SlicerApiError):
    """Sidecar responded with a 5xx — usually the wrapped slicer CLI exited
    non-zero (range-validation reject, segfault on complex models, etc.).
    Distinguished from `SlicerApiUnavailableError` so the caller can decide
    whether to retry with a different request shape (e.g. a 3MF embedded-
    settings fallback)."""


class SlicerInputError(SlicerApiError):
    """Sidecar rejected the input as invalid (4xx)."""


class SliceResult(NamedTuple):
    """Result of a slice operation."""

    content: bytes
    print_time_seconds: int
    filament_used_g: float
    filament_used_mm: float


_shared_http_client: httpx.AsyncClient | None = None


def _format_sidecar_error(response: httpx.Response) -> str:
    """Build a human-readable error string from a sidecar 4xx/5xx response.

    The sidecar's `AppError` middleware emits a JSON body of the shape
    ``{"message": "...", "details": "..."}``. Earlier versions of this
    client only read ``message``, which left every CLI failure surfaced
    as the generic ``Failed to slice the model`` because the *actual*
    CLI stderr / `error_string` lives in ``details``. Including both
    means ``bambuddy.log`` carries the real reason a slice rejected
    the supplied profiles instead of an unhelpful generic line.
    """
    try:
        payload = response.json()
    except Exception:
        return response.text[:500]
    if not isinstance(payload, dict):
        return str(payload)[:500]
    message = payload.get("message") or ""
    details = payload.get("details") or ""
    if message and details:
        return f"{message}: {details}"[:500]
    return (message or details or response.text)[:500]


def set_shared_http_client(client: httpx.AsyncClient | None) -> None:
    """Register an app-scoped client so per-request services can pool transport."""
    global _shared_http_client
    _shared_http_client = client


def _guess_model_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".stl"):
        return "model/stl"
    if lower.endswith(".3mf") or lower.endswith(".gcode.3mf"):
        return "model/3mf"
    if lower.endswith(".step") or lower.endswith(".stp"):
        return "model/step"
    return "application/octet-stream"


class SlicerApiService:
    """Talks to an OrcaSlicer / BambuStudio API sidecar."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        if client is not None:
            self._client = client
            self._owns_client = False
        elif _shared_http_client is not None:
            self._client = _shared_http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=timeout_seconds)
            self._owns_client = True

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "SlicerApiService":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def health(self) -> dict:
        """GET /health — used to surface a clear "sidecar offline" error before
        accepting a slice request from the user."""
        try:
            response = await self._client.get(f"{self.base_url}/health", timeout=10.0)
        except httpx.RequestError as exc:
            raise SlicerApiUnavailableError(f"Slicer sidecar unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise SlicerApiUnavailableError(f"Slicer sidecar /health returned {response.status_code}")
        return response.json()

    async def list_bundled_profiles(self) -> dict:
        """GET /profiles/bundled — return the slicer's stock profiles by slot.

        Powers the "Standard" tier of Bambuddy's SliceModal preset dropdowns.
        The sidecar walks the slicer's read-only `resources/profiles/BBL/`
        tree and returns ``{printer, process, filament}`` arrays of
        ``{name, base_id}`` (alphabetised, instantiable presets only — abstract
        bases like `fdm_filament_pla` are filtered out by the sidecar).

        Returns an empty-shaped dict when the sidecar is unreachable so the
        unified-presets endpoint can degrade to "no standard tier" without
        crashing the modal — cloud + local-imported profiles still render.
        """
        try:
            response = await self._client.get(f"{self.base_url}/profiles/bundled", timeout=10.0)
        except httpx.RequestError as exc:
            raise SlicerApiUnavailableError(f"Slicer sidecar unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise SlicerApiUnavailableError(f"Slicer sidecar /profiles/bundled returned {response.status_code}")
        return response.json()

    async def _poll_progress(
        self,
        request_id: str,
        on_progress: Callable[[dict], None],
    ) -> None:
        """Poll the sidecar's progress endpoint at ~1Hz and forward each
        snapshot to ``on_progress``. Runs until cancelled.

        4xx is NOT treated as terminal: the FIRST poll fires the moment
        the slice POST is sent, which can be milliseconds before the
        request actually lands on the sidecar and `progressStore.start()`
        runs — so a fresh request legitimately returns 404 for the first
        tick or two. Bailing on the first 404 (the original implementation)
        meant we'd quit before progress could ever arrive. The polling
        task is cancelled by the outer slice request anyway, so a
        sustained 404 (older sidecar without progress support, or post-
        slice grace expiry) just costs a few wasted GETs that the cancel
        will stop. Network errors and non-JSON 5xx are swallowed; the
        next tick retries.
        """
        url = f"{self.base_url}/slice/progress/{request_id}"
        while True:
            try:
                response = await self._client.get(url, timeout=5.0)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict):
                        on_progress(payload)
                # 404 / other 4xx = no progress available (yet, or ever
                # for older sidecars). Keep polling — the outer slice
                # request will cancel this task on completion.
            except (httpx.RequestError, ValueError):
                # ValueError covers JSONDecodeError when the sidecar
                # returns a non-JSON 5xx. Don't crash the poller.
                pass
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return

    async def slice_with_profiles(
        self,
        *,
        model_bytes: bytes,
        model_filename: str,
        printer_profile_json: str,
        process_profile_json: str,
        filament_profile_jsons: list[str],
        plate: int | None = None,
        export_3mf: bool = False,
        request_id: str | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> SliceResult:
        """POST /slice with model + printer/process/filament profiles.

        ``filament_profile_jsons`` is plate-slot-ordered: index 0 is the
        profile for slot 1, etc. Single-color callers pass a one-element
        list. Multiple ``filamentProfile`` parts are sent as a repeated form
        field — the sidecar's route declares ``maxCount: 16`` and the
        slicing service joins them as semicolon-separated
        ``--load-filaments`` for the OrcaSlicer / BambuStudio CLI.

        ``request_id``: when supplied, the sidecar wires --pipe to a
        per-request FIFO and publishes structured JSON progress events to
        its in-memory ProgressStore under this id. Bambuddy's slice
        dispatch polls ``GET /slice/progress/{request_id}`` in parallel
        to drive the live-progress toast.

        Raises:
            SlicerInputError: 4xx from sidecar (caller-supplied input is bad).
            SlicerApiUnavailableError: connection error or 5xx from sidecar.
        """
        # httpx supports repeated multipart fields when files is a list of
        # tuples — using the dict form would silently overwrite duplicate
        # keys and ship only the last filament profile.
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("file", (model_filename, model_bytes, _guess_model_content_type(model_filename))),
            ("printerProfile", ("printer.json", printer_profile_json.encode("utf-8"), "application/json")),
            ("presetProfile", ("preset.json", process_profile_json.encode("utf-8"), "application/json")),
        ]
        for idx, fjson in enumerate(filament_profile_jsons):
            files.append(
                (
                    "filamentProfile",
                    (f"filament_{idx + 1}.json", fjson.encode("utf-8"), "application/json"),
                )
            )

        data: dict[str, str] = {}
        if plate is not None:
            data["plate"] = str(plate)
        if export_3mf:
            data["exportType"] = "3mf"
        if request_id is not None:
            data["requestId"] = request_id

        # When the caller supplied a request_id, kick off a parallel
        # poller that reads the sidecar's --pipe-fed progress endpoint
        # and surfaces structured updates via on_progress. Uses a
        # short-tick poll (1s) since the slicer emits stage changes
        # several times per minute on complex models.
        progress_task: asyncio.Task | None = None
        if request_id is not None and on_progress is not None:
            progress_task = asyncio.create_task(
                self._poll_progress(request_id, on_progress),
                name=f"slicer-progress-{request_id}",
            )

        try:
            response = await self._client.post(
                f"{self.base_url}/slice",
                files=files,
                data=data,
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise SlicerApiUnavailableError(f"Slicer sidecar unreachable: {exc}") from exc
        finally:
            if progress_task is not None:
                progress_task.cancel()
                try:
                    await progress_task
                except (asyncio.CancelledError, Exception):
                    pass  # Polling errors must not fail the slice.

        if response.status_code >= 500:
            raise SlicerApiServerError(f"Slicer CLI failed ({response.status_code}): {_format_sidecar_error(response)}")
        if response.status_code >= 400:
            raise SlicerInputError(f"Slicer rejected input ({response.status_code}): {_format_sidecar_error(response)}")

        return SliceResult(
            content=response.content,
            print_time_seconds=_safe_int(response.headers.get("x-print-time-seconds")),
            filament_used_g=_safe_float(response.headers.get("x-filament-used-g")),
            filament_used_mm=_safe_float(response.headers.get("x-filament-used-mm")),
        )

    async def slice_without_profiles(
        self,
        *,
        model_bytes: bytes,
        model_filename: str,
        plate: int | None = None,
        export_3mf: bool = False,
        request_id: str | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> SliceResult:
        """POST /slice with only the model file and no profile triplet.

        For 3MF inputs this lets the slicer fall back on the file's embedded
        `Metadata/project_settings.config`. Used as a fallback when
        `slice_with_profiles` triggers a CLI segfault or other 5xx —
        complex H2D / multi-extruder models hit upstream bugs in both the
        OrcaSlicer and BambuStudio CLIs when invoked via `--load-settings`.

        Also used by the SliceModal's per-plate filament discovery path:
        for an unsliced project file we run a real preview slice via the
        sidecar to find which AMS slots the picked plate consumes. The
        ``request_id`` parameter routes the sidecar's --pipe progress
        events to the ProgressStore so the modal's inline spinner +
        toast can show "Generating G-code (75%)" for that preview as
        well.
        """
        files = {
            "file": (model_filename, model_bytes, _guess_model_content_type(model_filename)),
        }
        data: dict[str, str] = {}
        if plate is not None:
            data["plate"] = str(plate)
        if export_3mf:
            data["exportType"] = "3mf"
        if request_id is not None:
            data["requestId"] = request_id

        # Same progress-poller wiring as slice_with_profiles. Used by the
        # SliceModal's preview slice (for filament discovery) AND the
        # embedded-settings fallback path triggered by an Orca/Bambu CLI
        # segfault on complex H2D models — both want to keep updating
        # the user's toast through the slow operation.
        progress_task: asyncio.Task | None = None
        if request_id is not None and on_progress is not None:
            progress_task = asyncio.create_task(
                self._poll_progress(request_id, on_progress),
                name=f"slicer-progress-{request_id}",
            )

        try:
            response = await self._client.post(
                f"{self.base_url}/slice",
                files=files,
                data=data,
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise SlicerApiUnavailableError(f"Slicer sidecar unreachable: {exc}") from exc
        finally:
            if progress_task is not None:
                progress_task.cancel()
                try:
                    await progress_task
                except (asyncio.CancelledError, Exception):
                    pass

        if response.status_code >= 500:
            raise SlicerApiServerError(f"Slicer CLI failed ({response.status_code}): {_format_sidecar_error(response)}")
        if response.status_code >= 400:
            raise SlicerInputError(f"Slicer rejected input ({response.status_code}): {_format_sidecar_error(response)}")

        return SliceResult(
            content=response.content,
            print_time_seconds=_safe_int(response.headers.get("x-print-time-seconds")),
            filament_used_g=_safe_float(response.headers.get("x-filament-used-g")),
            filament_used_mm=_safe_float(response.headers.get("x-filament-used-mm")),
        )


def _safe_int(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
