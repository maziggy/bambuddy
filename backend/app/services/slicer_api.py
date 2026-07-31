"""HTTP client for an OrcaSlicer / BambuStudio API sidecar.

Bambuddy stores user printer/process/filament profiles itself (cloud-synced
or locally imported), so the slice flow always sends the model file plus an
explicit JSON profile triplet to the sidecar's `/slice` endpoint. The sidecar
shape mirrors `AFKFelix/orca-slicer-api` (multipart upload, `--load-settings`
under the hood, response body is raw G-code or 3MF with metadata in the
`X-Print-Time-Seconds` / `X-Filament-Used-G` / `X-Filament-Used-Mm` headers).
"""

import asyncio
import io
import logging
import time
import zipfile
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


class SlicerTimeoutError(SlicerApiError):
    """We gave up waiting on a slice that never finished.

    Kept apart from ``SlicerApiUnavailableError`` because they call for
    opposite reactions and used to be reported as the same thing: an
    ``httpx.ReadTimeout`` is a subclass of ``RequestError``, so a slice that
    simply took a long time surfaced as "Slicer sidecar unreachable" — sending
    the reporter of #2730 off to check a sidecar that was reachable throughout
    and still slicing when we hung up on it.
    """


class SliceResult(NamedTuple):
    """Result of a slice operation."""

    content: bytes
    print_time_seconds: int
    filament_used_g: float
    filament_used_mm: float


_shared_http_client: httpx.AsyncClient | None = None

# Fallback for callers that don't pass one (tests, and any path that runs
# without a DB session to read the setting from). The user-facing value is
# ``slicer_stall_timeout_minutes`` under Settings -> Workflow -> Slicer.
DEFAULT_SLICE_STALL_TIMEOUT_SECONDS = 15 * 60.0

# How often the progress poller ticks. Also the granularity of the stall check,
# since a missed tick is what the stall clock is counting.
_PROGRESS_POLL_INTERVAL = 1.0


async def get_stall_timeout_seconds(db) -> float:
    """Read ``slicer_stall_timeout_minutes`` (Settings -> Workflow -> Slicer).

    Falls back to the default on anything unparseable rather than failing the
    slice — a bad settings row must not be the reason a print doesn't happen.
    """
    from backend.app.api.routes.settings import get_setting

    try:
        raw = await get_setting(db, "slicer_stall_timeout_minutes")
    except Exception:
        return DEFAULT_SLICE_STALL_TIMEOUT_SECONDS
    try:
        minutes = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_SLICE_STALL_TIMEOUT_SECONDS
    if minutes < 1:
        return DEFAULT_SLICE_STALL_TIMEOUT_SECONDS
    return float(minutes) * 60.0


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


def _handle_slice_response(response: httpx.Response, *, export_3mf: bool) -> SliceResult:
    """Turn a sidecar ``/slice`` HTTP response into a validated ``SliceResult``.

    Shared by ``slice_with_profiles`` / ``slice_without_profiles`` so the status
    handling and output validation live in one place.

    Beyond the status check, this guards against the sidecar (or a reverse proxy
    in front of it) returning **HTTP 200 with a body that isn't a real slice**
    (#2671): a stock/misconfigured sidecar, a proxy interstitial or truncated
    response, or an OrcaSlicer/BambuStudio CLI crash that produces empty output.
    Without this check Bambuddy would store that tiny blob as a ``.gcode.3mf``,
    let it be queued, and FTP it to the printer — a silently-broken print. When
    a 3MF export was requested the body must be a valid ZIP (3MF container);
    anything else is treated as a sidecar failure.

    Raises:
        SlicerInputError: 4xx from the sidecar (bad input / proxy body limit).
        SlicerApiServerError: 5xx, or a 2xx whose body is not a valid 3MF.
    """
    if response.status_code == 413:
        # A 413 almost never comes from the slicer itself — it's a reverse proxy
        # (nginx/SWAG/Traefik) or a CDN capping the multipart upload (model +
        # profiles). Name the real fix so the user doesn't tweak the wrong layer.
        raise SlicerInputError(
            "The slice request was rejected as too large (HTTP 413). A reverse proxy "
            "in front of the slicer sidecar is capping the request body — raise "
            "'client_max_body_size' (nginx/SWAG) or the equivalent on the proxy that "
            "sits directly in front of the sidecar, then reload it. If the sidecar is "
            "behind Cloudflare, note its request-size cap."
        )
    if response.status_code >= 500:
        raise SlicerApiServerError(f"Slicer CLI failed ({response.status_code}): {_format_sidecar_error(response)}")
    if response.status_code >= 400:
        raise SlicerInputError(f"Slicer rejected input ({response.status_code}): {_format_sidecar_error(response)}")

    content = response.content
    if export_3mf and not zipfile.is_zipfile(io.BytesIO(content)):
        # 200 OK but the body is not a 3MF zip → the sidecar did not produce a
        # usable slice. Surface it loudly instead of persisting a corrupt file.
        detail = _format_sidecar_error(response) if len(content) <= 500 else ""
        raise SlicerApiServerError(
            f"Slicer sidecar returned HTTP {response.status_code} but the body is not a valid "
            f"3MF ({len(content)} bytes). This usually means a misconfigured sidecar, an "
            f"OrcaSlicer/BambuStudio CLI crash producing no output, or a reverse proxy returning "
            f"an error page or truncating the response — verify the sidecar URL and any proxy in "
            f"front of it." + (f" Body: {detail}" if detail else "")
        )

    return SliceResult(
        content=content,
        print_time_seconds=_safe_int(response.headers.get("x-print-time-seconds")),
        filament_used_g=_safe_float(response.headers.get("x-filament-used-g")),
        filament_used_mm=_safe_float(response.headers.get("x-filament-used-mm")),
    )


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


class _Liveness:
    """Tracks when the slicer last showed a sign of life.

    ``deadline`` is what the slice waits against, and it moves forward on every
    genuine progress update. A slice therefore fails only after the configured
    window of *silence*, however long the whole thing has been running (#2730).

    ``progress_supported`` stays False for sidecars that never answer the
    progress endpoint. Those give us nothing to judge liveness by, so the caller
    treats the same window as a total-elapsed ceiling rather than pretending a
    stall can be detected.
    """

    def __init__(self, window_seconds: float, poll_interval: float = _PROGRESS_POLL_INTERVAL) -> None:
        # Liveness can only be observed as often as the poller ticks, so a
        # window shorter than a few ticks would expire in the gap between two
        # polls and fail every slice instantly, however healthy. The settings
        # schema already floors the user-facing value at a minute; this guards
        # the constructor, which tests and any future caller can pass anything.
        self.window_seconds = max(window_seconds, poll_interval * 3)
        self.progress_supported = False
        self.started_at = time.monotonic()
        self._last_alive = self.started_at

    def saw_progress_endpoint(self) -> None:
        self.progress_supported = True

    def mark_alive(self) -> None:
        self._last_alive = time.monotonic()

    @property
    def deadline(self) -> float:
        """Monotonic time at which we stop waiting."""
        base = self._last_alive if self.progress_supported else self.started_at
        return base + self.window_seconds

    def silent_for(self) -> float:
        return time.monotonic() - self._last_alive

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def timeout_message(self) -> str:
        minutes = self.window_seconds / 60
        if self.progress_supported:
            return (
                f"The slicer stopped reporting progress for {minutes:.0f} minutes "
                f"(slicing had been running for {self.elapsed() / 60:.0f} minutes). "
                "Raise 'Slicer stall timeout' under Settings -> Workflow -> Slicer if this model "
                "legitimately needs longer between progress updates."
            )
        return (
            f"Slicing did not finish within {minutes:.0f} minutes, and this sidecar does not "
            "report progress, so there was no way to tell a slow model from a stalled one. "
            "Raise 'Slicer stall timeout' under Settings -> Workflow -> Slicer, or update the "
            "sidecar to a version that reports progress."
        )


class SlicerApiService:
    """Talks to an OrcaSlicer / BambuStudio API sidecar."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_SLICE_STALL_TIMEOUT_SECONDS,
    ) -> None:
        """``timeout_seconds`` bounds *silence*, not total slicing time (#2730).

        While a slice is running Bambuddy polls the sidecar's progress channel
        once a second, so it can tell a model that is merely slow from one that
        has stopped: the clock is reset by every progress update, and only runs
        out when the slicer has said nothing for this long. A heavy model that
        keeps reporting will run to completion however long it takes.

        Sidecars too old to report progress have no liveness signal to offer, so
        for those the same number bounds total elapsed time — the pre-#2730
        behaviour, but configurable and no longer five minutes flat.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # Instance-level so tests can compress the timing; production always
        # uses the module default.
        self.progress_poll_interval = _PROGRESS_POLL_INTERVAL
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
        *,
        liveness: "_Liveness | None" = None,
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

        When ``liveness`` is supplied this doubles as the stall watchdog: every
        200 carrying a *changed* payload marks the slicer alive, which is what
        keeps the slice's deadline moving (#2730). An unchanged payload
        deliberately does not count — the sidecar re-serves its last snapshot on
        every poll, so treating a repeat as progress would leave the watchdog
        unable to detect a stall at all.
        """
        url = f"{self.base_url}/slice/progress/{request_id}"
        last_payload: dict | None = None
        while True:
            try:
                response = await self._client.get(url, timeout=5.0)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict):
                        if liveness is not None:
                            liveness.saw_progress_endpoint()
                            if payload != last_payload:
                                liveness.mark_alive()
                        last_payload = payload
                        on_progress(payload)
                # 404 / other 4xx = no progress available (yet, or ever
                # for older sidecars). Keep polling — the outer slice
                # request will cancel this task on completion.
            except (httpx.RequestError, ValueError):
                # ValueError covers JSONDecodeError when the sidecar
                # returns a non-JSON 5xx. Don't crash the poller.
                pass
            try:
                await asyncio.sleep(self.progress_poll_interval)
            except asyncio.CancelledError:
                return

    async def _post_slice(
        self,
        *,
        files: list | dict,
        data: dict,
        request_id: str | None,
        on_progress: Callable[[dict], None] | None,
    ) -> httpx.Response:
        """POST /slice, supervised by the progress channel rather than a clock.

        Before #2730 this was a plain ``httpx`` call with a flat 300 s timeout on
        every phase. A genuinely heavy model — the reporter's was a MakerWorld
        model that Bambu Studio also took a long time over — hit the ceiling
        while it was still slicing perfectly happily, and because
        ``httpx.ReadTimeout`` is a ``RequestError`` it was reported as "Slicer
        sidecar unreachable". Meanwhile Bambuddy was polling the sidecar's
        progress endpoint once a second and could see the thing working.

        So the read timeout comes off the HTTP call and the poller supervises
        instead: the deadline is pushed forward by every progress update, and
        only a genuine silence ends the wait. Connect and pool keep short
        timeouts — a sidecar that won't accept the connection at all is
        unreachable, and should still say so quickly.
        """
        liveness = _Liveness(self.timeout_seconds, self.progress_poll_interval)

        # Poll whenever we have a request_id, even if the caller wants no
        # progress callbacks: the poll is what makes stall detection possible,
        # and one GET per second is cheaper than a wrongly-cancelled slice.
        progress_task: asyncio.Task | None = None
        if request_id is not None:
            progress_task = asyncio.create_task(
                self._poll_progress(request_id, on_progress or (lambda _payload: None), liveness=liveness),
                name=f"slicer-progress-{request_id}",
            )

        post_task = asyncio.create_task(
            self._client.post(
                f"{self.base_url}/slice",
                files=files,
                data=data,
                timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=30.0),
            ),
            name="slicer-slice-post",
        )

        try:
            while True:
                remaining = liveness.deadline - time.monotonic()
                if remaining <= 0:
                    post_task.cancel()
                    logger.warning(
                        "Slice abandoned after %.0fs (silent for %.0fs, progress channel %s)",
                        liveness.elapsed(),
                        liveness.silent_for(),
                        "available" if liveness.progress_supported else "unavailable",
                    )
                    raise SlicerTimeoutError(liveness.timeout_message())
                # Re-check at poll granularity so a progress update that lands
                # mid-wait extends the deadline promptly.
                done, _pending = await asyncio.wait({post_task}, timeout=min(remaining, self.progress_poll_interval))
                if post_task in done:
                    break
        finally:
            if progress_task is not None:
                progress_task.cancel()
            # Await both so neither is left pending — a cancelled POST still
            # needs its connection released back to the pool.
            await asyncio.gather(post_task, progress_task or asyncio.sleep(0), return_exceptions=True)

        try:
            return post_task.result()
        except httpx.RequestError as exc:
            raise SlicerApiUnavailableError(f"Slicer sidecar unreachable: {exc}") from exc

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
        arrange: bool = False,
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

        ``arrange`` forwards the sidecar's ``--arrange`` flag to BambuStudio.
        When True the slicer auto-repositions objects on the target bed,
        which Bambuddy uses for cross-nozzle-class re-slices (#1493) where
        the source's X1C-coordinate layout would otherwise drop into an H2D
        dead zone or trigger the multi-extruder geometry pipeline's polygon
        clipping crash. Default off so single-printer slices preserve the
        user's deliberate layout.

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
        if arrange:
            # Sidecar reads non-empty truthy strings as True; only send the
            # field when we want the flag on, so default-off callers exactly
            # match the previous wire payload.
            data["arrange"] = "true"
        if request_id is not None:
            data["requestId"] = request_id

        # When the caller supplied a request_id, kick off a parallel
        # poller that reads the sidecar's --pipe-fed progress endpoint
        # and surfaces structured updates via on_progress. Uses a
        # short-tick poll (1s) since the slicer emits stage changes
        # several times per minute on complex models.
        response = await self._post_slice(files=files, data=data, request_id=request_id, on_progress=on_progress)
        return _handle_slice_response(response, export_3mf=export_3mf)

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
        response = await self._post_slice(files=files, data=data, request_id=request_id, on_progress=on_progress)
        return _handle_slice_response(response, export_3mf=export_3mf)


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
