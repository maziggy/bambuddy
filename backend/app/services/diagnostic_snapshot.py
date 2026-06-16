"""Aggregate connection, virtual-printer, and log-health diagnostics into a
single snapshot for the support bundle and bug-report submission paths.

Each user-triggered support artifact (the System-page support ZIP and the
bug-report bubble) already exposed these three checks inline in the UI but
omitted them from what landed in the maintainer's hands. This module is the
single entry point both flows call to capture all three at once.

Designed around three constraints:

- **Fail-soft per probe.** A crash inside one printer's check must not nuke the
  whole snapshot — that's the whole point of including diagnostics in the
  bundle: a partial result is more useful than a 500.
- **Bounded total runtime.** Each probe runs concurrently and is guarded by an
  outer wall-clock cap; timeouts emit a marker entry rather than blocking.
- **No mutation.** Connection / VP diagnostics only probe TCP ports and read
  state; log-health is a passive scanner. Safe to run on every bundle.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Mirrors the IPv4 pattern in services.log_reader.sanitize_log_content. Kept as
# a literal here (not imported) so a refactor of that module's internals can't
# silently change snapshot sanitization. Skips firmware-version-shaped strings
# (leading-zero octets like "01.09.01.00") via the [1-9]\d|\d alternations.
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\b")

# Per-diagnostic wall-clock cap. Each underlying probe carries its own (smaller)
# TCP / HTTP timeouts; this is the outer guard so a hung interface or a wedged
# subprocess can't stall bundle generation past about this many seconds per
# printer/VP. Snapshot total runtime is bounded by max(per-cap) thanks to the
# concurrent gather, not the sum.
_PER_DIAGNOSTIC_TIMEOUT_SECONDS = 15.0


def _serialize(result: Any) -> Any:
    """Convert a Pydantic model to a dict; pass through plain dicts/lists."""
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return result


async def _run_connection_for(printer) -> dict:
    from backend.app.services.printer_diagnostic import run_connection_diagnostic

    base = {"printer_id": printer.id, "printer_name": printer.name}
    try:
        result = await asyncio.wait_for(
            run_connection_diagnostic(
                printer.ip_address,
                printer=printer,
                serial_number=printer.serial_number,
                access_code=printer.access_code,
            ),
            timeout=_PER_DIAGNOSTIC_TIMEOUT_SECONDS,
        )
        return {**base, "result": _serialize(result)}
    except asyncio.TimeoutError:
        return {**base, "error": "timed_out"}
    except Exception as e:
        # Log with traceback so the bundle generation isn't silent about
        # a broken probe, but never propagate.
        logger.warning("Connection diagnostic failed for printer %s: %s", printer.id, e, exc_info=True)
        return {**base, "error": str(e)}


async def _run_vp_for(vp) -> dict:
    from backend.app.services.virtual_printer import virtual_printer_manager
    from backend.app.services.virtual_printer.diagnostic import run_vp_diagnostic

    base = {"vp_id": vp.id, "name": vp.name}
    try:
        instance = virtual_printer_manager.get_instance(vp.id)
        result = await asyncio.wait_for(
            run_vp_diagnostic(vp, instance),
            timeout=_PER_DIAGNOSTIC_TIMEOUT_SECONDS,
        )
        return {**base, "result": _serialize(result)}
    except asyncio.TimeoutError:
        return {**base, "error": "timed_out"}
    except Exception as e:
        logger.warning("VP diagnostic failed for VP %s: %s", vp.id, e, exc_info=True)
        return {**base, "error": str(e)}


async def _run_log_health() -> Any:
    from backend.app.services.log_health import scan_logs

    try:
        # scan_logs is sync I/O-bound (file read + regex); push off the loop.
        result = await asyncio.wait_for(
            asyncio.to_thread(scan_logs),
            timeout=_PER_DIAGNOSTIC_TIMEOUT_SECONDS,
        )
        return _serialize(result)
    except asyncio.TimeoutError:
        return {"error": "timed_out"}
    except Exception as e:
        logger.warning("Log-health scan failed: %s", e, exc_info=True)
        return {"error": str(e)}


async def collect_diagnostic_snapshot(db: AsyncSession) -> dict[str, Any]:
    """Return the three-section diagnostic snapshot.

    Always returns a dict with keys ``connection_diagnostics`` (list, one entry
    per active printer), ``vp_diagnostics`` (list, one entry per enabled VP —
    empty if none), and ``log_health`` (the ``scan_logs`` result or an error
    marker). Each list entry carries either ``result`` (success) or ``error``
    (timeout / exception) so the maintainer can tell at a glance whether a
    given probe ran.
    """
    from backend.app.models.printer import Printer
    from backend.app.models.virtual_printer import VirtualPrinter

    printers_result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
    printers = list(printers_result.scalars().all())

    vps_result = await db.execute(select(VirtualPrinter).where(VirtualPrinter.enabled.is_(True)))
    vps = list(vps_result.scalars().all())

    # Concurrent: total wall-clock ≈ max(per-cap), not sum.
    results = await asyncio.gather(
        asyncio.gather(*(_run_connection_for(p) for p in printers)) if printers else _noop_list(),
        asyncio.gather(*(_run_vp_for(vp) for vp in vps)) if vps else _noop_list(),
        _run_log_health(),
        return_exceptions=True,
    )
    connection_results, vp_results, log_health = results

    def _coerce_list(r) -> list:
        if isinstance(r, BaseException):
            logger.warning("Diagnostic snapshot batch failed: %s", r)
            return []
        return list(r) if r is not None else []

    snapshot = {
        "connection_diagnostics": _coerce_list(connection_results),
        "vp_diagnostics": _coerce_list(vp_results),
        "log_health": log_health if not isinstance(log_health, BaseException) else {"error": str(log_health)},
    }

    # Sanitize before returning. The diagnostic schemas embed printer/host IPs
    # (`PrinterDiagnosticResult.ip_address`, network-mode check params, VP
    # `bind_ip`) and the snapshot adds printer names — none of which should
    # leak into a submitted GitHub issue or a shared support ZIP. Use the
    # same `collect_sensitive_strings` table the log sanitizer already
    # consults so the replacement labels stay consistent ([PRINTER], [SERIAL],
    # [IP], [ACCESS_CODE]); the IPv4 regex fallback in `_mask_string` then
    # catches host / bind IPs that aren't in the DB.
    try:
        from backend.app.services.log_reader import collect_sensitive_strings

        sensitive_strings = await collect_sensitive_strings(db)
    except Exception:
        logger.warning("Could not collect sensitive strings for snapshot sanitization", exc_info=True)
        sensitive_strings = {}
    return _sanitize_recursive(snapshot, sensitive_strings)


async def _noop_list() -> list:
    return []


def _mask_string(value: str, sensitive_strings: dict[str, str]) -> str:
    """Apply known-value replacement + IPv4 regex masking to a single string.

    Known values are matched first (longest first so "My Printer 1" beats
    "My Printer"); the regex pass then catches any IPs the sensitive_strings
    table didn't already cover — most importantly the Bambuddy host's own
    IP (returned by ``_get_host_ip`` inside the diagnostic, not in the DB)
    and any virtual-printer ``bind_ip`` the user picked at setup.
    """
    if not value:
        return value
    for raw, label in sorted(sensitive_strings.items(), key=lambda x: len(x[0]), reverse=True):
        if len(raw) < 3:
            continue
        if raw in value:
            value = value.replace(raw, label)
    value = _IPV4_RE.sub("[IP]", value)
    return value


def _sanitize_recursive(node: Any, sensitive_strings: dict[str, str]) -> Any:
    """Walk the snapshot and redact strings in place — dicts, lists, scalars.

    Non-string scalars (ints, bools, None) pass through; we only need to
    mask user-visible values. Keys are NOT renamed (those are structural).
    """
    if isinstance(node, str):
        return _mask_string(node, sensitive_strings)
    if isinstance(node, dict):
        return {k: _sanitize_recursive(v, sensitive_strings) for k, v in node.items()}
    if isinstance(node, list):
        return [_sanitize_recursive(item, sensitive_strings) for item in node]
    return node
