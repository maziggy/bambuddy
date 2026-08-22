"""Helpers for matching and downloading printer-side video files."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from backend.app.core.config import settings
from backend.app.core.tasks import spawn_background_task
from backend.app.services.bambu_ftp import (
    DownloadCancelled,
    DownloadInsufficientSpace,
    DownloadLimitExceeded,
    download_file_async,
)

logger = logging.getLogger(__name__)

VIDEO_SUFFIXES = (".mp4", ".avi", ".mkv")
MAX_PRINTER_ZIP_BYTES = 10 * 1024**3
PRINTER_ZIP_FREE_SPACE_RESERVE = 256 * 1024**2
_STALE_BUNDLE_SECONDS = 60 * 60
MAX_PRINTER_ZIP_PREPARE_SECONDS = 30 * 60
MAX_OPEN_ARCHIVE_IPCAM_SECONDS = 24 * 60 * 60
_BUNDLE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_JOB_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{20,200}$")
_LOCAL_JOB_TASKS: dict[str, asyncio.Task] = {}
_cleanup_task: asyncio.Task | None = None
_CLEANUP_INTERVAL_SECONDS = 15 * 60


class PrinterFilesZipTooLargeError(ValueError):
    """The selected printer files exceed the bounded ZIP staging limit."""


class PrinterFilesZipInsufficientSpaceError(OSError):
    """The app data volume cannot safely stage the selected files."""


@dataclass(frozen=True)
class PrinterFilesZipResult:
    """Result of staging one printer ZIP."""

    path: Path
    requested: int
    successful: int
    failed_paths: tuple[str, ...]
    total_bytes: int


@dataclass(frozen=True)
class PrinterFilesJobStatus:
    """Serializable state for an asynchronous browser preparation job."""

    job_id: str
    printer_id: int
    state: str
    requested: int
    successful: int = 0
    failed: int = 0
    token: str | None = None
    filename: str | None = None
    message: str | None = None


class _FileCancelSignal:
    """Cross-worker cancellation signal checked by the FTP callback thread."""

    def __init__(self, path: Path):
        self.path = path
        self._last_check = 0.0
        self._cached = False

    def is_set(self) -> bool:
        if self._cached:
            return True
        now = time.monotonic()
        if now - self._last_check >= 0.25:
            self._last_check = now
            self._cached = self.path.exists()
        return self._cached


def _job_status_path(job_id: str) -> Path:
    if not _JOB_KEY_RE.fullmatch(job_id):
        raise ValueError("Invalid printer download job id")
    return _printer_zip_root() / f"job-{job_id}.json"


def _job_cancel_path(job_id: str) -> Path:
    if not _JOB_KEY_RE.fullmatch(job_id):
        raise ValueError("Invalid printer download job id")
    return _printer_zip_root() / f"job-{job_id}.cancel"


def _write_job_status(status: PrinterFilesJobStatus) -> None:
    """Atomically publish job state for polling from any app worker."""

    path = _job_status_path(status.job_id)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(status.__dict__, separators=(",", ":")), encoding="utf-8")
    temp_path.replace(path)


def _read_job_status(job_id: str) -> PrinterFilesJobStatus | None:
    try:
        data = json.loads(_job_status_path(job_id).read_text(encoding="utf-8"))
        return PrinterFilesJobStatus(**data)
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def match_ipcam_chunks(
    files: list[dict],
    started_at: datetime | None,
    completed_at: datetime | None,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Return `/ipcam` chunks whose completion time overlaps a print.

    Bambu's `ipcam-record.*.mp4` files are fixed-size chunks. On the tested X1C
    and H2D firmware, their FTP mtime is the chunk completion time in the same
    UTC-naive basis used by archive timestamps. Some firmware reports FTP LIST
    mtimes in printer-local time instead; LIST carries no timezone with which
    to correct those values reliably. A ten-minute tail includes the final
    chunk, whose mtime lands after the print-complete event.
    """

    start = _naive_utc(started_at)
    if start is None:
        return []
    live_end = _naive_utc(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    # A crash can leave an archive in ``printing`` indefinitely. Do not turn
    # that stale row into a window covering every chunk created since then.
    end = _naive_utc(completed_at) or min(live_end, start + timedelta(seconds=MAX_OPEN_ARCHIVE_IPCAM_SECONDS))
    lower = start - timedelta(minutes=1)
    upper = max(start, end) + timedelta(minutes=10)

    matches: list[dict] = []
    for file in files:
        name = str(file.get("name") or "")
        mtime = file.get("mtime")
        if file.get("is_directory") or not name.lower().startswith("ipcam-record."):
            continue
        if not name.lower().endswith(VIDEO_SUFFIXES) or not isinstance(mtime, datetime):
            continue
        timestamp = _naive_utc(mtime)
        if timestamp is not None and lower <= timestamp <= upper:
            matches.append(file)

    matches.sort(key=lambda item: _naive_utc(item.get("mtime")) or datetime.min)
    return matches


def _zip_arcname(remote_path: str, used: set[str]) -> str:
    """Return a safe, unique relative archive name for a printer path."""

    parts = [part for part in PurePosixPath(remote_path).parts if part not in ("/", "", ".", "..")]
    candidate = "/".join(parts) or "printer-file"
    stem = candidate
    suffix = ""
    if "." in PurePosixPath(candidate).name:
        suffix = "".join(PurePosixPath(candidate).suffixes)
        stem = candidate[: -len(suffix)] if suffix else candidate
    counter = 2
    while candidate in used:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate)
    return candidate


def _printer_zip_root() -> Path:
    """Return the dedicated staging root without doing event-loop I/O."""

    return settings.archive_dir / "temp" / "printer-file-downloads"


def _ensure_printer_zip_root() -> Path:
    """Create and return the staging root on the persistent data volume."""

    root = _printer_zip_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prune_stale_bundles(root: Path) -> None:
    """Remove abandoned bundles after token expiry, without touching archives."""

    cutoff = time.time() - _STALE_BUNDLE_SECONDS
    if not root.exists():
        return
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file() and child.name.startswith("job-") and child.stat().st_mtime < cutoff:
                child.unlink(missing_ok=True)
        except OSError:
            continue


async def prune_stale_printer_file_bundles() -> None:
    """Prune abandoned printer ZIPs without blocking the event loop."""

    root = await asyncio.to_thread(_ensure_printer_zip_root)
    await asyncio.to_thread(_prune_stale_bundles, root)


async def _printer_download_cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            await prune_stale_printer_file_bundles()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Periodic printer-download cleanup failed")


def start_printer_download_cleanup() -> None:
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_task = spawn_background_task(_printer_download_cleanup_loop(), name="printer-download-cleanup")


async def stop_printer_download_cleanup() -> None:
    """Stop cleanup and cancel every in-process preparation before shutdown."""

    global _cleanup_task
    tasks: list[asyncio.Task] = []
    cleanup_task = _cleanup_task
    _cleanup_task = None
    if cleanup_task is not None:
        cleanup_task.cancel()
        tasks.append(cleanup_task)

    # Jobs can be inside an FTP worker thread. Publish the same cooperative
    # cancellation marker used by the DELETE endpoint before cancelling the
    # asyncio wrapper, then await every wrapper so no executor work is left
    # behind when the application event loop closes.
    for job_id, task in list(_LOCAL_JOB_TASKS.items()):
        if not task.done():
            await asyncio.to_thread(_job_cancel_path(job_id).touch)
            task.cancel()
        tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _LOCAL_JOB_TASKS.clear()


def printer_files_zip_path(printer_id: int, token: str) -> Path | None:
    """Resolve the staged ZIP for a resource-bound browser token."""

    bundle_key = f"{printer_id}-{token}"
    if not _BUNDLE_KEY_RE.fullmatch(bundle_key):
        return None
    return _printer_zip_root() / bundle_key / "printer-files.zip"


def bind_printer_files_zip_to_token(
    result: PrinterFilesZipResult,
    printer_id: int,
    token: str,
) -> PrinterFilesZipResult:
    """Move a prepared bundle to the path derived from its persisted token."""

    target = printer_files_zip_path(printer_id, token)
    if target is None:
        raise ValueError("Invalid printer ZIP token")
    result.path.parent.rename(target.parent)
    return replace(result, path=target)


def _check_initial_space(root: Path, sizes: dict[str, int]) -> None:
    # These sizes are client-reported hints used only for an early rejection,
    # so this is a courtesy, not the bound. The real one is enforced per write
    # and per FTP callback below, against actual bytes and the live free space,
    # which is the only thing that can hold when several preparations run at
    # once -- and they do: nothing serializes them. Two concurrent jobs that
    # both pass here stop independently at the reserve, and the one that gets
    # there second fails with a message saying so.
    expected_total = sum(sizes.values())
    if expected_total > MAX_PRINTER_ZIP_BYTES:
        raise PrinterFilesZipTooLargeError(
            f"Selected files total {expected_total} bytes; the limit is {MAX_PRINTER_ZIP_BYTES} bytes"
        )

    largest_file = max(sizes.values(), default=0)
    # In the worst case the ZIP is as large as the inputs while the largest
    # source is still staged beside it. Keep a reserve for the database/logs.
    required = expected_total + largest_file + PRINTER_ZIP_FREE_SPACE_RESERVE
    free = shutil.disk_usage(root).free
    if free < required:
        raise PrinterFilesZipInsufficientSpaceError(
            f"The app data volume needs {required} bytes free to stage this selection; {free} bytes are available"
        )


async def build_printer_files_zip(
    printer,
    paths: list[str],
    sizes: dict[str, int],
    *,
    bundle_key: str | None = None,
    preserve_paths: bool = True,
    allow_empty: bool = False,
    cancel_signal: _FileCancelSignal | None = None,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
) -> PrinterFilesZipResult:
    """Download printer files one at a time into a disk-backed ZIP.

    The previous implementation held every source file and the final ZIP in
    memory. Continuous `/ipcam` chunks are commonly ~250 MB each, so selecting
    only a few could exhaust both server and browser memory.
    """

    root = await asyncio.to_thread(_ensure_printer_zip_root)
    await asyncio.to_thread(_prune_stale_bundles, root)
    await asyncio.to_thread(_check_initial_space, root, sizes)
    bundle_dir: Path | None = None
    try:
        if bundle_key is None:
            bundle_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="bundle-", dir=root))
        else:
            if not _BUNDLE_KEY_RE.fullmatch(bundle_key):
                raise ValueError("Invalid printer ZIP bundle key")
            bundle_dir = root / bundle_key
            await asyncio.to_thread(bundle_dir.mkdir, mode=0o700)
        zip_path = bundle_dir / "printer-files.zip"
        successful = 0
        total_bytes = 0
        failed_paths: list[str] = []
        used_names: set[str] = set()

        archive = await asyncio.to_thread(zipfile.ZipFile, zip_path, "w", allowZip64=True)
        try:
            for index, remote_path in enumerate(paths):
                if cancel_signal is not None and cancel_signal.is_set():
                    raise asyncio.CancelledError
                if not isinstance(remote_path, str) or not remote_path.startswith("/") or "\x00" in remote_path:
                    logger.warning("Skipping invalid printer file path: %r", remote_path)
                    failed_paths.append(remote_path)
                    continue
                staged_path = bundle_dir / f"download-{index}"
                try:
                    expected_size = sizes.get(remote_path)
                    if expected_size is not None:
                        free = (await asyncio.to_thread(shutil.disk_usage, root)).free
                        if free < expected_size + PRINTER_ZIP_FREE_SPACE_RESERVE:
                            raise PrinterFilesZipInsufficientSpaceError(
                                "The app data volume lacks space for the next selected file"
                            )
                    downloaded = await download_file_async(
                        printer.ip_address,
                        printer.access_code,
                        remote_path,
                        staged_path,
                        timeout=600,
                        socket_timeout=60,
                        printer_model=printer.model,
                        expected_size=expected_size,
                        max_bytes=MAX_PRINTER_ZIP_BYTES - total_bytes,
                        cancel_event=cancel_signal,
                        min_free_bytes=PRINTER_ZIP_FREE_SPACE_RESERVE,
                    )
                    if not downloaded:
                        failed_paths.append(remote_path)
                        continue
                    # Deliberately no second size comparison here. The transfer
                    # was already checked against the printer's own SIZE, which
                    # download_to_file treats as the authority precisely because
                    # it beats a hint the browser round-tripped; re-judging the
                    # result against that hint would overrule the better number
                    # with the worse one. The hint goes stale in exactly the case
                    # this feature exists for -- an /ipcam chunk or a timelapse
                    # still being written when the listing was taken -- and a
                    # complete file would then be dropped as "truncated".
                    file_size = (await asyncio.to_thread(staged_path.stat)).st_size
                    if total_bytes + file_size > MAX_PRINTER_ZIP_BYTES:
                        raise PrinterFilesZipTooLargeError(
                            f"Downloaded files exceed the {MAX_PRINTER_ZIP_BYTES}-byte limit"
                        )
                    free = (await asyncio.to_thread(shutil.disk_usage, root)).free
                    if free < file_size + PRINTER_ZIP_FREE_SPACE_RESERVE:
                        raise PrinterFilesZipInsufficientSpaceError(
                            "The app data volume ran out of safe staging space while building the ZIP"
                        )
                    compression = (
                        zipfile.ZIP_STORED if remote_path.lower().endswith(VIDEO_SUFFIXES) else zipfile.ZIP_DEFLATED
                    )
                    arc_source = remote_path if preserve_paths else PurePosixPath(remote_path).name
                    await asyncio.to_thread(
                        archive.write,
                        staged_path,
                        _zip_arcname(arc_source, used_names),
                        compress_type=compression,
                    )
                    successful += 1
                    total_bytes += file_size
                except DownloadLimitExceeded as exc:
                    raise PrinterFilesZipTooLargeError(
                        f"Downloaded files exceed the {MAX_PRINTER_ZIP_BYTES}-byte limit"
                    ) from exc
                except DownloadInsufficientSpace as exc:
                    raise PrinterFilesZipInsufficientSpaceError(
                        "The app data volume ran out of safe staging space during transfer"
                    ) from exc
                except DownloadCancelled as exc:
                    raise asyncio.CancelledError from exc
                except (PrinterFilesZipTooLargeError, PrinterFilesZipInsufficientSpaceError):
                    raise
                except Exception as exc:
                    logger.warning("Failed to add %s to printer ZIP: %s", remote_path, exc)
                    failed_paths.append(remote_path)
                finally:
                    await asyncio.to_thread(staged_path.unlink, missing_ok=True)
                    if progress_callback is not None:
                        await progress_callback(successful, len(failed_paths))
        finally:
            await asyncio.shield(asyncio.to_thread(archive.close))
    except BaseException:
        if bundle_dir is not None:
            await asyncio.shield(asyncio.to_thread(shutil.rmtree, bundle_dir, ignore_errors=True))
        raise

    if successful == 0 and not allow_empty:
        await asyncio.to_thread(shutil.rmtree, bundle_dir, ignore_errors=True)
        raise FileNotFoundError("No files could be downloaded")
    return PrinterFilesZipResult(
        path=zip_path,
        requested=len(paths),
        successful=successful,
        failed_paths=tuple(failed_paths),
        total_bytes=total_bytes,
    )


def printer_file_path(printer_id: int, token: str) -> Path | None:
    """Resolve a prepared native single-file download."""

    bundle_key = f"{printer_id}-{token}"
    if not _BUNDLE_KEY_RE.fullmatch(bundle_key):
        return None
    return _printer_zip_root() / bundle_key / "printer-file"


def bind_printer_file_to_token(result: PrinterFilesZipResult, printer_id: int, token: str) -> PrinterFilesZipResult:
    target = printer_file_path(printer_id, token)
    if target is None:
        raise ValueError("Invalid printer file token")
    result.path.parent.rename(target.parent)
    return replace(result, path=target)


async def build_printer_file(
    printer,
    remote_path: str,
    expected_size: int | None,
    *,
    bundle_key: str,
    cancel_signal: _FileCancelSignal | None = None,
) -> PrinterFilesZipResult:
    """Stage one printer file on disk for a browser-native download.

    Also the read path for the 3MF preview in the file browser, which is why
    nothing here waits on a shared lock: a preview must not queue behind
    somebody else's ten-gigabyte selection for as long as that takes.
    """

    if not remote_path.startswith("/") or "\x00" in remote_path:
        raise FileNotFoundError("Invalid printer file path")
    root = await asyncio.to_thread(_ensure_printer_zip_root)
    size_hints = {remote_path: expected_size} if expected_size is not None else {}
    await asyncio.to_thread(_check_initial_space, root, size_hints)
    bundle_dir = root / bundle_key
    try:
        await asyncio.to_thread(bundle_dir.mkdir, mode=0o700)
        local_path = bundle_dir / "printer-file"
        downloaded = await download_file_async(
            printer.ip_address,
            printer.access_code,
            remote_path,
            local_path,
            timeout=600,
            socket_timeout=60,
            printer_model=printer.model,
            expected_size=expected_size,
            max_bytes=MAX_PRINTER_ZIP_BYTES,
            cancel_event=cancel_signal,
            min_free_bytes=PRINTER_ZIP_FREE_SPACE_RESERVE,
        )
        if not downloaded:
            raise FileNotFoundError("The selected printer file could not be downloaded")
        file_size = (await asyncio.to_thread(local_path.stat)).st_size
        return PrinterFilesZipResult(
            path=local_path,
            requested=1,
            successful=1,
            failed_paths=(),
            total_bytes=file_size,
        )
    except DownloadLimitExceeded as exc:
        await asyncio.shield(asyncio.to_thread(shutil.rmtree, bundle_dir, ignore_errors=True))
        raise PrinterFilesZipTooLargeError(f"Downloaded file exceeds the {MAX_PRINTER_ZIP_BYTES}-byte limit") from exc
    except DownloadInsufficientSpace as exc:
        await asyncio.shield(asyncio.to_thread(shutil.rmtree, bundle_dir, ignore_errors=True))
        raise PrinterFilesZipInsufficientSpaceError(
            "The app data volume ran out of safe staging space during transfer"
        ) from exc
    except DownloadCancelled as exc:
        await asyncio.shield(asyncio.to_thread(shutil.rmtree, bundle_dir, ignore_errors=True))
        raise asyncio.CancelledError from exc
    except BaseException:
        await asyncio.shield(asyncio.to_thread(shutil.rmtree, bundle_dir, ignore_errors=True))
        raise


async def _run_printer_files_job(
    printer,
    job_id: str,
    paths: list[str],
    sizes: dict[str, int],
    filename: str,
    as_zip: bool,
) -> None:
    from backend.app.core.auth import create_slicer_download_token

    cancel_signal = _FileCancelSignal(_job_cancel_path(job_id))
    status = PrinterFilesJobStatus(job_id, printer.id, "preparing", len(paths), filename=filename)
    await asyncio.to_thread(_write_job_status, status)

    async def report_progress(successful: int, failed: int) -> None:
        await asyncio.to_thread(
            _write_job_status,
            PrinterFilesJobStatus(
                job_id,
                printer.id,
                "preparing",
                len(paths),
                successful=successful,
                failed=failed,
                filename=filename,
            ),
        )

    try:
        async with asyncio.timeout(MAX_PRINTER_ZIP_PREPARE_SECONDS):
            if as_zip:
                result = await build_printer_files_zip(
                    printer,
                    paths,
                    sizes,
                    bundle_key=f"job-{job_id}",
                    cancel_signal=cancel_signal,
                    progress_callback=report_progress,
                )
            else:
                result = await build_printer_file(
                    printer,
                    paths[0],
                    sizes.get(paths[0]),
                    bundle_key=f"job-{job_id}",
                    cancel_signal=cancel_signal,
                )
        if cancel_signal.is_set():
            await asyncio.to_thread(remove_printer_files_zip, result.path)
            raise asyncio.CancelledError
        token = await create_slicer_download_token("printer-files", printer.id)
        if as_zip:
            result = await asyncio.to_thread(bind_printer_files_zip_to_token, result, printer.id, token)
        else:
            result = await asyncio.to_thread(bind_printer_file_to_token, result, printer.id, token)
        await asyncio.to_thread(
            _write_job_status,
            PrinterFilesJobStatus(
                job_id,
                printer.id,
                "ready",
                len(paths),
                successful=result.successful,
                failed=len(result.failed_paths),
                token=token,
                filename=filename,
            ),
        )
    except asyncio.CancelledError:
        await asyncio.shield(
            asyncio.to_thread(
                _write_job_status,
                PrinterFilesJobStatus(job_id, printer.id, "cancelled", len(paths), filename=filename),
            )
        )
    except PrinterFilesZipTooLargeError as exc:
        await asyncio.to_thread(
            _write_job_status,
            PrinterFilesJobStatus(job_id, printer.id, "failed", len(paths), filename=filename, message=str(exc)),
        )
    except PrinterFilesZipInsufficientSpaceError as exc:
        await asyncio.to_thread(
            _write_job_status,
            PrinterFilesJobStatus(job_id, printer.id, "failed", len(paths), filename=filename, message=str(exc)),
        )
    except TimeoutError:
        await asyncio.to_thread(
            _write_job_status,
            PrinterFilesJobStatus(
                job_id,
                printer.id,
                "failed",
                len(paths),
                filename=filename,
                message="Printer download preparation exceeded the 30-minute limit",
            ),
        )
    except FileNotFoundError as exc:
        await asyncio.to_thread(
            _write_job_status,
            PrinterFilesJobStatus(job_id, printer.id, "failed", len(paths), filename=filename, message=str(exc)),
        )
    except Exception:
        logger.exception("Printer download job %s failed", job_id)
        await asyncio.to_thread(
            _write_job_status,
            PrinterFilesJobStatus(
                job_id,
                printer.id,
                "failed",
                len(paths),
                filename=filename,
                message="Printer download preparation failed",
            ),
        )
    finally:
        await asyncio.to_thread(_job_cancel_path(job_id).unlink, missing_ok=True)


async def start_printer_files_job(
    printer,
    paths: list[str],
    sizes: dict[str, int],
    filename: str,
    *,
    as_zip: bool,
) -> PrinterFilesJobStatus:
    """Start a bounded background preparation and return immediately."""

    if not paths:
        raise ValueError("No files specified")
    root = await asyncio.to_thread(_ensure_printer_zip_root)
    await asyncio.to_thread(_prune_stale_bundles, root)
    await asyncio.to_thread(_check_initial_space, root, sizes)
    job_id = secrets.token_urlsafe(24)
    status = PrinterFilesJobStatus(job_id, printer.id, "queued", len(paths), filename=filename)
    await asyncio.to_thread(_write_job_status, status)
    task = spawn_background_task(
        _run_printer_files_job(printer, job_id, paths, sizes, filename, as_zip),
        name=f"printer-download-{printer.id}-{job_id}",
    )
    _LOCAL_JOB_TASKS[job_id] = task
    task.add_done_callback(lambda _task: _LOCAL_JOB_TASKS.pop(job_id, None))
    return status


async def get_printer_files_job(job_id: str, printer_id: int) -> PrinterFilesJobStatus | None:
    status = await asyncio.to_thread(_read_job_status, job_id)
    if status is None or status.printer_id != printer_id:
        return None
    return status


async def cancel_printer_files_job(job_id: str, printer_id: int) -> bool:
    status = await get_printer_files_job(job_id, printer_id)
    if status is None:
        return False
    await asyncio.to_thread(_job_cancel_path(job_id).touch)
    task = _LOCAL_JOB_TASKS.get(job_id)
    if task is not None and not task.done():
        task.cancel()
    if status.state == "ready" and status.token:
        zip_path = printer_files_zip_path(printer_id, status.token)
        prepared = (
            zip_path
            if zip_path is not None and await asyncio.to_thread(zip_path.is_file)
            else printer_file_path(printer_id, status.token)
        )
        if prepared is not None:
            await asyncio.to_thread(remove_printer_files_zip, prepared)
        await asyncio.to_thread(
            _write_job_status,
            replace(status, state="cancelled", token=None),
        )
    return True


def remove_printer_files_zip(zip_path: Path) -> None:
    """Remove a completed download bundle after FileResponse finishes."""

    shutil.rmtree(zip_path.parent, ignore_errors=True)
