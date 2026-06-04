"""Shared primitives for reading, parsing, and sanitizing the Bambuddy app log.

Extracted from ``routes/support.py`` so service-layer code (e.g. the log-health
scanner in ``log_health.py``) can reuse log reading and redaction without
importing from the API layer. ``support.py`` re-imports these helpers and keeps
its own route handlers.
"""

import logging
import re

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.user import User

logger = logging.getLogger(__name__)

# Log line format: "2024-01-15 10:30:45,123 INFO [module.name] [trace_id] Message"
# The trace_id is left as part of the message group — callers that need it can
# parse it out; the log-health scanner does not.
LOG_LINE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})\s+(\w+)\s+\[([^\]]+)\]\s+(.*)$")


class LogEntry(BaseModel):
    """A single parsed log entry."""

    timestamp: str
    level: str
    logger_name: str
    message: str


def parse_log_line(line: str) -> LogEntry | None:
    """Parse a single log line into a LogEntry, or None if it is not a line start."""
    match = LOG_LINE_PATTERN.match(line.strip())
    if match:
        return LogEntry(
            timestamp=match.group(1),
            level=match.group(2),
            logger_name=match.group(3),
            message=match.group(4),
        )
    return None


def read_log_entries(
    limit: int = 200,
    level_filter: str | None = None,
    search: str | None = None,
) -> tuple[list[LogEntry], int]:
    """Read and parse log entries from ``bambuddy.log``, newest first.

    Continuation lines (tracebacks etc.) are folded into the message of the
    entry they belong to. Returns ``(entries, total_lines_in_file)``.
    """
    log_file = settings.log_dir / "bambuddy.log"
    if not log_file.exists():
        return [], 0

    entries: list[LogEntry] = []
    total_lines = 0

    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            total_lines = len(lines)

            # Parse lines in reverse order (newest first)
            current_entry: LogEntry | None = None
            multi_line_buffer: list[str] = []

            for line in reversed(lines):
                parsed = parse_log_line(line)
                if parsed:
                    # Found a new log entry start
                    if current_entry:
                        # Apply filters and add previous entry (without multi_line_buffer - it belongs to new entry)
                        should_include = True

                        # Level filter
                        if level_filter and current_entry.level.upper() != level_filter.upper():
                            should_include = False

                        # Search filter (case-insensitive)
                        if search and should_include:
                            search_lower = search.lower()
                            if not (
                                search_lower in current_entry.message.lower()
                                or search_lower in current_entry.logger_name.lower()
                            ):
                                should_include = False

                        if should_include:
                            entries.append(current_entry)

                            if len(entries) >= limit:
                                break

                    # Set new entry and attach any accumulated multi-line content to it
                    # (in reverse order, continuation lines come before their parent entry)
                    current_entry = parsed
                    if multi_line_buffer:
                        current_entry.message += "\n" + "\n".join(reversed(multi_line_buffer))
                    multi_line_buffer = []
                elif line.strip():
                    # Continuation of multi-line log entry (will be attached to next parsed entry)
                    multi_line_buffer.append(line.rstrip())

            # Don't forget the last (oldest) entry
            # Note: any remaining multi_line_buffer would be orphaned lines before the first entry
            if current_entry and len(entries) < limit:
                should_include = True
                if level_filter and current_entry.level.upper() != level_filter.upper():
                    should_include = False
                if search and should_include:
                    search_lower = search.lower()
                    if not (
                        search_lower in current_entry.message.lower()
                        or search_lower in current_entry.logger_name.lower()
                    ):
                        should_include = False
                if should_include:
                    entries.append(current_entry)

    except Exception as e:
        logger.error("Error reading log file: %s", e)
        return [], 0

    # Entries are already in newest-first order
    return entries, total_lines


def sanitize_log_content(content: str, sensitive_strings: dict[str, str] | None = None) -> str:
    """Remove sensitive data from log content.

    ``sensitive_strings`` maps known exact values (printer names, serials, etc.)
    to replacement labels; pass the result of :func:`collect_sensitive_strings`.
    Regex passes additionally redact credentials in URLs, emails, serials, and
    IP addresses that were not captured by exact matching.
    """
    # First, replace known sensitive values (database-aware exact matching)
    # This catches printer names, usernames, and other arbitrary user-chosen strings
    # that regex patterns cannot detect
    if sensitive_strings:
        # Sort by length descending to avoid partial matches (e.g. "My Printer 1" before "My Printer")
        for value, label in sorted(sensitive_strings.items(), key=lambda x: len(x[0]), reverse=True):
            if len(value) < 3:
                continue  # Skip very short strings to prevent over-redaction
            content = re.sub(re.escape(value), label, content)

    # Replace credentials in URLs (e.g. http://user:pass@host, rtsps://bblp:code@host)
    content = re.sub(r"((?:https?|rtsps?)://)[^/:@\s]+:[^/@\s]+@", r"\1[CREDENTIALS]@", content)

    # Replace email addresses
    content = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", content)

    # Replace Bambu Lab printer serial numbers (format: 00M/01D/01S/01P/03W + alphanumeric, 12-16 chars total)
    content = re.sub(r"\b0[0-3][A-Z0-9][A-Z0-9]{9,13}\b", "[SERIAL]", content, flags=re.IGNORECASE)

    # Replace IPv4 addresses (skip firmware versions like 01.09.01.00 which have leading zeros)
    content = re.sub(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)\b",
        "[IP]",
        content,
    )

    # Replace paths with usernames
    content = re.sub(r"/home/[^/\s]+/", "/home/[user]/", content)
    content = re.sub(r"/Users/[^/\s]+/", "/Users/[user]/", content)
    content = re.sub(r"/opt/[^/\s]+/", "/opt/[user]/", content)

    return content


async def collect_sensitive_strings(db: AsyncSession) -> dict[str, str]:
    """Collect known sensitive values from the database for log redaction.

    Covers printer names, serial numbers, IP addresses, access codes, auth
    usernames, and the Bambu Cloud email. Pass the result to
    :func:`sanitize_log_content`.
    """
    sensitive_strings: dict[str, str] = {}

    # Printer names, serial numbers, IP addresses, and access codes
    result = await db.execute(select(Printer.name, Printer.serial_number, Printer.ip_address, Printer.access_code))
    for name, serial, ip_address, access_code in result.all():
        if name:
            sensitive_strings[name] = "[PRINTER]"
        if serial:
            sensitive_strings[serial] = "[SERIAL]"
        if ip_address:
            sensitive_strings[ip_address] = "[IP]"
        if access_code:
            sensitive_strings[access_code] = "[ACCESS_CODE]"

    # Auth usernames
    result = await db.execute(select(User.username))
    for (username,) in result.all():
        if username:
            sensitive_strings[username] = "[USER]"

    # Bambu Cloud email
    result = await db.execute(select(Settings.value).where(Settings.key == "bambu_cloud_email"))
    cloud_email = result.scalar_one_or_none()
    if cloud_email:
        sensitive_strings[cloud_email] = "[EMAIL]"

    return sensitive_strings
