"""HTTP client for an OrcaSlicer / BambuStudio API sidecar.

Bambuddy stores user printer/process/filament profiles itself (cloud-synced
or locally imported), so the slice flow always sends the model file plus an
explicit JSON profile triplet to the sidecar's `/slice` endpoint. The sidecar
shape mirrors `AFKFelix/orca-slicer-api` (multipart upload, `--load-settings`
under the hood, response body is raw G-code or 3MF with metadata in the
`X-Print-Time-Seconds` / `X-Filament-Used-G` / `X-Filament-Used-Mm` headers).
"""

import logging
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

    async def slice_with_profiles(
        self,
        *,
        model_bytes: bytes,
        model_filename: str,
        printer_profile_json: str,
        process_profile_json: str,
        filament_profile_json: str,
        plate: int | None = None,
        export_3mf: bool = False,
    ) -> SliceResult:
        """POST /slice with model + printer/process/filament profile triplet.

        Raises:
            SlicerInputError: 4xx from sidecar (caller-supplied input is bad).
            SlicerApiUnavailableError: connection error or 5xx from sidecar.
        """
        files = {
            "file": (model_filename, model_bytes, _guess_model_content_type(model_filename)),
            "printerProfile": ("printer.json", printer_profile_json.encode("utf-8"), "application/json"),
            "presetProfile": ("preset.json", process_profile_json.encode("utf-8"), "application/json"),
            "filamentProfile": ("filament.json", filament_profile_json.encode("utf-8"), "application/json"),
        }
        data: dict[str, str] = {}
        if plate is not None:
            data["plate"] = str(plate)
        if export_3mf:
            data["exportType"] = "3mf"

        try:
            response = await self._client.post(
                f"{self.base_url}/slice",
                files=files,
                data=data,
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise SlicerApiUnavailableError(f"Slicer sidecar unreachable: {exc}") from exc

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
    ) -> SliceResult:
        """POST /slice with only the model file and no profile triplet.

        For 3MF inputs this lets the slicer fall back on the file's embedded
        `Metadata/project_settings.config`. Used as a fallback when
        `slice_with_profiles` triggers a CLI segfault or other 5xx —
        complex H2D / multi-extruder models hit upstream bugs in both the
        OrcaSlicer and BambuStudio CLIs when invoked via `--load-settings`.
        """
        files = {
            "file": (model_filename, model_bytes, _guess_model_content_type(model_filename)),
        }
        data: dict[str, str] = {}
        if plate is not None:
            data["plate"] = str(plate)
        if export_3mf:
            data["exportType"] = "3mf"

        try:
            response = await self._client.post(
                f"{self.base_url}/slice",
                files=files,
                data=data,
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise SlicerApiUnavailableError(f"Slicer sidecar unreachable: {exc}") from exc

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
