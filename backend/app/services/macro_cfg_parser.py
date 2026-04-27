r"""Parser and serializer for Bambuddy .cfg macro files.

Format (Klipper-style):
    [macro preheat_bed]
    description: Heat bed to 60°C and wait
    trigger: schedule
    cron: 0 8 * * *
    printer: My X1C
    M140 S60
    WAIT_FOR_TEMP --target=60 --tolerance=2
    NOTIFY --message="Bed ready"

    [macro imperial_march]
    ; optional comment
    M17
    M1006 S1
    ...

Rules:
  - A block starts with a line matching r'^\[macro\s+(\S+)\]'
  - Everything between two block headers (or EOF) is the body
  - Config lines (key: value) are read from the top of the block before any
    G-code or command lines. Recognised keys: description, trigger, cron, printer.
    Any unrecognised key:value line before body content is treated as body.
  - Lines starting with ';' or '#' are comments — preserved in file, stripped at render
  - Blank lines within a block are preserved for readability
  - Duplicate block names produce a ParseError on the duplicate; first kept
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_BLOCK_RE = re.compile(r"^\[macro\s+(\S+)\]", re.IGNORECASE)
_CONFIG_RE = re.compile(r"^(description|trigger|cron|printer)\s*:\s*(.*)", re.IGNORECASE)

_KNOWN_KEYS = {"description", "trigger", "cron", "printer"}


@dataclass
class ParsedMacro:
    name: str
    description: str | None
    trigger_type: str  # manual | webhook | schedule
    cron_expression: str | None
    printer_name: str | None  # raw name from file; caller resolves to printer_id
    body: str  # raw body text (may include comments and blank lines)
    line_no: int  # 1-based line of the [macro ...] header
    error: str | None = None  # set if this block had a parse-level error


@dataclass
class ParseResult:
    macros: list[ParsedMacro] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # file-level errors


def parse(text: str) -> ParseResult:
    """Parse the full text of a .cfg file and return all macro blocks."""
    result = ParseResult()
    lines = text.splitlines()

    # Find all block header positions
    headers: list[tuple[int, str]] = []  # (line_index, macro_name)
    for i, line in enumerate(lines):
        m = _BLOCK_RE.match(line.strip())
        if m:
            headers.append((i, m.group(1)))

    if not headers:
        return result  # empty file or no macros — valid

    seen_names: dict[str, int] = {}  # name → first line_no

    for idx, (header_line, name) in enumerate(headers):
        # Body lines: from after the header to before the next header (or EOF)
        body_start = header_line + 1
        body_end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        body_lines = lines[body_start:body_end]

        # Duplicate name check
        if name in seen_names:
            error = (
                f"Duplicate macro name '{name}' at line {header_line + 1} "
                f"(first defined at line {seen_names[name]}); skipping duplicate"
            )
            result.errors.append(error)
            result.macros.append(
                ParsedMacro(
                    name=name,
                    description=None,
                    trigger_type="manual",
                    cron_expression=None,
                    printer_name=None,
                    body="",
                    line_no=header_line + 1,
                    error=error,
                )
            )
            continue

        seen_names[name] = header_line + 1

        # Extract config fields from leading key: value lines
        config: dict[str, str] = {}
        remaining_lines: list[str] = []
        config_done = False

        for line in body_lines:
            stripped = line.strip()
            if not config_done:
                # blank or comment lines before config are kept as-is in body
                if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                    # If we haven't started config yet, these are preamble — skip into body
                    remaining_lines.append(line)
                    continue
                cm = _CONFIG_RE.match(stripped)
                if cm:
                    key = cm.group(1).lower()
                    value = cm.group(2).strip()
                    config[key] = value
                    continue
                else:
                    # First non-blank non-comment non-config line ends config section
                    config_done = True
                    remaining_lines.append(line)
            else:
                remaining_lines.append(line)

        description = config.get("description") or None
        trigger_raw = (config.get("trigger") or "manual").strip().lower()
        trigger_type = trigger_raw if trigger_raw in ("manual", "webhook", "schedule") else "manual"
        cron_expression = config.get("cron") or None
        printer_name = config.get("printer") or None

        # Strip trailing blank lines from body, keep internal ones
        body = "\n".join(remaining_lines).rstrip()

        result.macros.append(
            ParsedMacro(
                name=name,
                description=description,
                trigger_type=trigger_type,
                cron_expression=cron_expression,
                printer_name=printer_name,
                body=body,
                line_no=header_line + 1,
            )
        )

    return result


def get_macro_body(text: str, name: str) -> str | None:
    """Return just the body text for a named macro, or None if not found."""
    result = parse(text)
    for m in result.macros:
        if m.name == name and not m.error:
            return m.body
    return None


def serialize(macros: list[dict]) -> str:
    """Build .cfg file text from a list of dicts.

    Recognised keys: name, description, trigger, cron, printer, body.
    """
    parts: list[str] = []
    for macro in macros:
        header = f"[macro {macro['name']}]"
        lines = [header]
        for key in ("description", "trigger", "cron", "printer"):
            val = macro.get(key)
            if val:
                lines.append(f"{key}: {val}")
        body = (macro.get("body") or "").strip()
        if body:
            lines.append(body)
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"
