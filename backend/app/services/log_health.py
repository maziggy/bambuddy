"""Log-health scanner.

Matches the recent Bambuddy app log against a curated catalog of known failure
signatures, so users can self-diagnose setup ("layer 8") issues before filing a
bug report.

The catalog is a deliberate *allowlist*: only known-bad, actionable signatures
are matched — a healthy install produces an empty finding list. Human-readable
cause and fix text is intentionally NOT stored here; the frontend renders it
from i18n keys ``systemHealth.signature.<id>.{name,cause,fix}`` so it stays
translatable across all locales. This module only carries the machine-facing
fields (pattern, severity, category, wiki anchor).
"""

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel

from backend.app.core.config import settings
from backend.app.services.log_reader import LogEntry, read_log_entries, sanitize_log_content

logger = logging.getLogger(__name__)

# How many recent log entries to scan by default.
DEFAULT_SCAN_LIMIT = 4000

# Log levels ranked so a signature can require "at least WARNING" etc.
_LEVEL_RANK = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

# Findings are ordered layer8 first (the user can act on these), then
# environment, then bug (please report). Within a group: errors before warnings.
_CATEGORY_ORDER = {"layer8": 0, "environment": 1, "bug": 2}
_SEVERITY_ORDER = {"error": 0, "warning": 1}

# Cap the sample line length so a finding can never carry a huge folded traceback.
_SAMPLE_MAX_LEN = 400


@dataclass(frozen=True)
class LogSignature:
    """One curated known-issue signature.

    ``patterns`` are matched (``re.search``, case-insensitive) against the log
    entry message. A signature only becomes a reported finding once it has
    matched ``min_count`` times within the scan window — this gates noisy,
    individually-benign symptoms (e.g. an occasional MQTT reconnect after a
    Wi-Fi blip) from being surfaced as a problem.
    """

    id: str
    patterns: tuple[re.Pattern[str], ...]
    severity: str  # "error" | "warning"
    category: str  # "layer8" | "environment" | "bug"
    wiki_anchor: str  # slug appended to the troubleshooting wiki page URL
    min_level: str = "WARNING"
    logger_prefix: str | None = None  # only match entries from this logger tree
    min_count: int = 1


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# --- The catalog -----------------------------------------------------------
# Seeded from the ranked "layer 8" root causes found in the closed-issue triage
# review. Each id MUST have matching i18n keys: systemHealth.signature.<id>.*
SIGNATURES: tuple[LogSignature, ...] = (
    LogSignature(
        # Wrong/mistyped access code — FTPS login is rejected (530).
        id="ftp-auth-rejected",
        patterns=_compile(r"FTP connection permission error"),
        severity="error",
        category="layer8",
        wiki_anchor="wrong-access-code",
        logger_prefix="backend.app.services.bambu_ftp",
    ),
    LogSignature(
        # FTPS :990 unreachable — port blocked by a firewall, or the printer is
        # off / on a different subnet.
        id="ftp-connection-timeout",
        patterns=_compile(r"FTP connection timed out"),
        severity="warning",
        category="layer8",
        wiki_anchor="ftps-port-990-blocked",
        logger_prefix="backend.app.services.bambu_ftp",
        min_count=3,
    ),
    LogSignature(
        # TLS negotiation to the printer's FTPS server failed.
        id="ftp-ssl-error",
        patterns=_compile(r"FTP SSL error connecting"),
        severity="warning",
        category="layer8",
        wiki_anchor="ftps-tls-failure",
        logger_prefix="backend.app.services.bambu_ftp",
        min_count=3,
    ),
    LogSignature(
        # MQTT connection keeps dropping — typically MQTT :8883 partially
        # blocked, LAN mode unstable, or a flaky network path to the printer.
        id="mqtt-connection-flapping",
        patterns=_compile(r"Forcing MQTT reconnect", r"Hard reset reconnect failed"),
        severity="warning",
        category="layer8",
        wiki_anchor="mqtt-connection-unstable",
        logger_prefix="backend.app.services.bambu_mqtt",
        min_count=5,
    ),
    LogSignature(
        # Camera stream unreachable — RTSPS :322 blocked, or the printer
        # camera / LAN liveview is disabled.
        id="camera-connection-refused",
        patterns=_compile(
            r"Chamber image: connection refused",
            r"Chamber image: connection timeout",
            r"Camera connection test failed",
        ),
        severity="warning",
        category="layer8",
        wiki_anchor="camera-rtsps-port-322",
        logger_prefix="backend.app.services.camera",
        min_count=3,
    ),
    LogSignature(
        # SQLite write contention. Surfaces inside exception tracebacks; folded
        # continuation lines are part of the entry message, so this still
        # matches. The fix is switching to PostgreSQL under multi-printer load.
        id="database-locked",
        patterns=_compile(r"database is locked"),
        severity="error",
        category="environment",
        wiki_anchor="database-is-locked",
    ),
)


class LogFinding(BaseModel):
    """An aggregated, sanitized match of one signature against the log."""

    signature_id: str
    severity: str
    category: str
    wiki_anchor: str
    count: int
    first_seen: str
    last_seen: str
    sample: str


class ScanResult(BaseModel):
    """Result of a log-health scan."""

    findings: list[LogFinding]
    scanned_entries: int
    log_available: bool
    summary: dict[str, int]


def _level_ok(entry: LogEntry, min_level: str) -> bool:
    return _LEVEL_RANK.get(entry.level.upper(), 0) >= _LEVEL_RANK.get(min_level, 30)


def _matches(sig: LogSignature, entry: LogEntry) -> bool:
    if not _level_ok(entry, sig.min_level):
        return False
    if sig.logger_prefix and not entry.logger_name.startswith(sig.logger_prefix):
        return False
    return any(p.search(entry.message) for p in sig.patterns)


def _sample_line(message: str) -> str:
    """Take the first line of a (possibly multi-line) entry, length-capped."""
    first_line = message.splitlines()[0] if message else ""
    if len(first_line) > _SAMPLE_MAX_LEN:
        return first_line[:_SAMPLE_MAX_LEN] + "…"
    return first_line


def scan_logs(
    limit: int = DEFAULT_SCAN_LIMIT,
    sensitive_strings: dict[str, str] | None = None,
) -> ScanResult:
    """Scan the recent app log against the signature catalog.

    ``sensitive_strings`` (from :func:`log_reader.collect_sensitive_strings`) is
    applied to every sample line so printer names, serials, IPs, and access
    codes never leave the process. Even when it is ``None`` the regex-based
    redaction passes still run.
    """
    log_file = settings.log_dir / "bambuddy.log"
    log_available = log_file.exists()

    entries, _total = read_log_entries(limit=limit)

    # entry_id -> accumulator. entries arrive newest-first.
    agg: dict[str, dict] = {}
    for entry in entries:
        for sig in SIGNATURES:
            if not _matches(sig, entry):
                continue
            acc = agg.get(sig.id)
            if acc is None:
                # First (== newest) occurrence encountered.
                agg[sig.id] = {
                    "count": 1,
                    "sample": entry.message,
                    "last_seen": entry.timestamp,
                    "first_seen": entry.timestamp,
                }
            else:
                acc["count"] += 1
                # Iterating newest-first, so each later hit is older.
                acc["first_seen"] = entry.timestamp

    findings: list[LogFinding] = []
    for sig in SIGNATURES:
        acc = agg.get(sig.id)
        if acc is None or acc["count"] < sig.min_count:
            continue
        sample = sanitize_log_content(_sample_line(acc["sample"]), sensitive_strings)
        findings.append(
            LogFinding(
                signature_id=sig.id,
                severity=sig.severity,
                category=sig.category,
                wiki_anchor=sig.wiki_anchor,
                count=acc["count"],
                first_seen=acc["first_seen"],
                last_seen=acc["last_seen"],
                sample=sample,
            )
        )

    findings.sort(
        key=lambda f: (
            _CATEGORY_ORDER.get(f.category, 9),
            _SEVERITY_ORDER.get(f.severity, 9),
            -f.count,
        )
    )

    summary = {
        "total": len(findings),
        "layer8": sum(1 for f in findings if f.category == "layer8"),
        "environment": sum(1 for f in findings if f.category == "environment"),
        "bug": sum(1 for f in findings if f.category == "bug"),
    }

    return ScanResult(
        findings=findings,
        scanned_entries=len(entries),
        log_available=log_available,
        summary=summary,
    )
