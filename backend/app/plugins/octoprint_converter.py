"""OctoPrint plugin detector and Bambuddy converter.

Detects whether an extracted directory is:
  - A native Bambuddy plugin   → install as-is
  - An OctoPrint plugin        → convert and install
  - Unknown                    → reject

OctoPrint → Bambuddy mixin mapping
───────────────────────────────────
  StartupPlugin       → StartupPlugin      (on_after_startup)
  ShutdownPlugin      → ShutdownPlugin     (on_before_shutdown)
  EventHandlerPlugin  → EventHandlerPlugin (on_event)
  SettingsPlugin      → SettingsPlugin     (get_settings_defaults)
  SimpleApiPlugin     → SimpleApiPlugin    (get_api_commands, on_api_command, on_api_get)
  AssetPlugin         → AssetPlugin        (get_assets)
  TemplatePlugin      → ✗ not supported
  BlueprintPlugin     → ✗ not supported
  ProgressPlugin      → EventHandlerPlugin (partial, via PRINT_PROGRESS event)
"""

from __future__ import annotations

import ast
import re
import textwrap
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_MIXIN_MAP: dict[str, str] = {
    "StartupPlugin": "StartupPlugin",
    "ShutdownPlugin": "ShutdownPlugin",
    "EventHandlerPlugin": "EventHandlerPlugin",
    "SettingsPlugin": "SettingsPlugin",
    "SimpleApiPlugin": "SimpleApiPlugin",
    "AssetPlugin": "AssetPlugin",
    "ProgressPlugin": "EventHandlerPlugin",  # partial
}

UNSUPPORTED_MIXINS: dict[str, str] = {
    "TemplatePlugin": "Template rendering is OctoPrint-specific",
    "BlueprintPlugin": "Blueprint routes require manual porting to FastAPI",
    "WizardPlugin": "Wizard UI is OctoPrint-specific",
    "UiPlugin": "Custom UI is OctoPrint-specific",
}

# OctoPrint event string → Bambuddy Events constant
OCTOPRINT_EVENT_MAP: dict[str, str] = {
    "Connected": "Events.CONNECTED",
    "Disconnected": "Events.DISCONNECTED",
    "PrintStarted": "Events.PRINT_STARTED",
    "PrintDone": "Events.PRINT_DONE",
    "PrintFailed": "Events.PRINT_FAILED",
    "PrintCancelled": "Events.PRINT_CANCELLED",
    "PrintPaused": "Events.PRINT_PAUSED",
    "PrintResumed": "Events.PRINT_RESUMED",
    "PrintProgress": "Events.PRINT_PROGRESS",
    "Startup": "Events.STARTUP",
    "Shutdown": "Events.SHUTDOWN",
}

# Methods that must be declared async in Bambuddy
ASYNC_METHODS: set[str] = {
    "on_after_startup",
    "on_before_shutdown",
    "on_event",
    "on_api_command",
    "on_api_get",
    "on_print_progress",
}

# Methods we'll try to extract and port
PORTABLE_METHODS: set[str] = {
    "get_settings_defaults",
    "get_api_commands",
    "on_api_command",
    "on_api_get",
    "on_after_startup",
    "on_before_shutdown",
    "on_event",
    "get_assets",
    "on_print_progress",
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ConversionResult:
    plugin_type: str           # "bambuddy" | "octoprint" | "unknown"
    plugin_key: str
    name: str
    version: str
    description: str
    author: str
    supported_mixins: list[str] = field(default_factory=list)
    unsupported_mixins: list[str] = field(default_factory=list)
    conversion_notes: list[str] = field(default_factory=list)
    converted_code: Optional[str] = None   # new __init__.py for OctoPrint plugins
    plugin_dir: Optional[Path] = None      # resolved plugin directory to install


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_and_convert(extract_dir: Path, desired_key: Optional[str] = None) -> ConversionResult:
    """Analyse *extract_dir* and return a ConversionResult.

    *extract_dir* is the root of the extracted zip contents (may contain a
    single top-level sub-directory, as GitHub archives do).
    """
    plugin_dir = _unwrap_single_dir(extract_dir)
    main_file = _find_main_module(plugin_dir)

    if main_file is None:
        return ConversionResult(
            plugin_type="unknown",
            plugin_key=desired_key or "unknown_plugin",
            name="Unknown",
            version="0.0.0",
            description="",
            author="",
            conversion_notes=["No Python file with __plugin_implementation__ found."],
            plugin_dir=plugin_dir,
        )

    source = main_file.read_text(encoding="utf-8", errors="replace")

    if _is_bambuddy_module(source):
        meta = _extract_metadata(source)
        key = desired_key or _derive_key(plugin_dir.name, meta["name"])
        return ConversionResult(
            plugin_type="bambuddy",
            plugin_key=key,
            name=meta["name"] or key,
            version=meta["version"],
            description=meta["description"],
            author=meta["author"],
            conversion_notes=["Native Bambuddy plugin — will be installed as-is."],
            plugin_dir=plugin_dir,
        )

    if _is_octoprint_module(source):
        return _convert_octoprint(main_file, plugin_dir, desired_key)

    return ConversionResult(
        plugin_type="unknown",
        plugin_key=desired_key or "unknown_plugin",
        name="Unknown",
        version="0.0.0",
        description="",
        author="",
        conversion_notes=[
            "File contains __plugin_implementation__ but no recognisable "
            "Bambuddy or OctoPrint signatures."
        ],
        plugin_dir=plugin_dir,
    )


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _unwrap_single_dir(path: Path) -> Path:
    """If a directory contains exactly one sub-directory and nothing else, unwrap it."""
    entries = [e for e in path.iterdir() if not e.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return path


def _find_main_module(plugin_dir: Path) -> Optional[Path]:
    """Return the .py file that contains __plugin_implementation__, or None."""
    candidates = list(plugin_dir.rglob("*.py"))
    # Prefer __init__.py at top level
    for candidate in sorted(candidates, key=lambda p: (len(p.parts), p.name != "__init__.py")):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if "__plugin_implementation__" in text:
                return candidate
        except OSError:
            continue
    return None


def _is_bambuddy_module(source: str) -> bool:
    return "backend.app.plugins.base" in source or "BambuddyPlugin" in source


def _is_octoprint_module(source: str) -> bool:
    octo_signatures = [
        "import octoprint",
        "from octoprint",
        "__plugin_pythoncompat__",
        "octoprint.plugin",
    ]
    return any(sig in source for sig in octo_signatures)


def _extract_metadata(source: str) -> dict[str, str]:
    meta: dict[str, str] = {"name": "", "version": "1.0.0", "description": "", "author": ""}
    for line in source.splitlines():
        for key, attr in [
            ("__plugin_name__", "name"),
            ("__plugin_version__", "version"),
            ("__plugin_description__", "description"),
            ("__plugin_author__", "author"),
        ]:
            m = re.match(rf'^{key}\s*=\s*["\'](.+?)["\']', line.strip())
            if m:
                meta[attr] = m.group(1)
    return meta


def _derive_key(dir_name: str, plugin_name: str) -> str:
    """Derive a safe plugin_key from a directory or plugin name."""
    base = dir_name or plugin_name
    # Strip common OctoPrint prefixes
    base = re.sub(r"^[Oo]cto[Pp]rint[-_]", "", base)
    base = re.sub(r"^octoprint_", "", base)
    base = re.sub(r"[-\s]+", "_", base)
    base = re.sub(r"[^\w]", "", base).lower()
    return base or "converted_plugin"


# ---------------------------------------------------------------------------
# OctoPrint conversion
# ---------------------------------------------------------------------------

def _convert_octoprint(main_file: Path, plugin_dir: Path, desired_key: Optional[str]) -> ConversionResult:
    source = main_file.read_text(encoding="utf-8", errors="replace")
    meta = _extract_metadata(source)
    key = desired_key or _derive_key(plugin_dir.name, meta["name"])

    supported: list[str] = []
    unsupported: list[str] = []
    notes: list[str] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ConversionResult(
            plugin_type="octoprint",
            plugin_key=key,
            name=meta["name"] or key,
            version=meta["version"],
            description=meta["description"],
            author=meta["author"],
            conversion_notes=[f"Syntax error in plugin source: {exc}"],
            plugin_dir=plugin_dir,
        )

    # Find the plugin class
    plugin_class = _find_plugin_class(tree)

    if plugin_class is None:
        notes.append("Could not locate the plugin class — generated a skeleton only.")
        converted = _generate_skeleton(meta, key)
        return ConversionResult(
            plugin_type="octoprint",
            plugin_key=key,
            name=meta["name"] or key,
            version=meta["version"],
            description=meta["description"],
            author=meta["author"],
            supported_mixins=[],
            unsupported_mixins=[],
            conversion_notes=notes,
            converted_code=converted,
            plugin_dir=plugin_dir,
        )

    # Determine which mixins are present
    for base in plugin_class.bases:
        base_name = _ast_name(base)
        short = base_name.split(".")[-1]
        if short in SUPPORTED_MIXIN_MAP:
            mapped = SUPPORTED_MIXIN_MAP[short]
            if mapped not in supported:
                supported.append(mapped)
            if short == "ProgressPlugin":
                notes.append("ProgressPlugin → EventHandlerPlugin (on_print_progress mapped to PRINT_PROGRESS event)")
        elif short in UNSUPPORTED_MIXINS:
            unsupported.append(short)
            notes.append(f"[not converted] {short}: {UNSUPPORTED_MIXINS[short]}")

    # Extract portable methods
    source_lines = source.splitlines()
    extracted_methods: list[str] = []

    for node in ast.walk(plugin_class):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in PORTABLE_METHODS:
            continue

        try:
            method_src = ast.get_source_segment(source, node) or ""
        except Exception:
            method_src = ""

        if not method_src:
            continue

        method_src = _normalize_method_source(method_src, node.col_offset)
        method_src = _transform_method(method_src, node.name)
        extracted_methods.append(textwrap.indent(method_src, "    "))

    # Note any OctoPrint-specific API usage we couldn't convert
    octo_apis = _find_octoprint_api_usage(source)
    for api in octo_apis:
        notes.append(f"[manual] {api} — requires manual porting (see TODO comments in generated code)")

    if not extracted_methods:
        notes.append("No portable methods found — generated a skeleton. Add your logic manually.")

    converted = _generate_converted_code(
        meta=meta,
        plugin_key=key,
        class_name=plugin_class.name,
        supported_mixins=supported,
        extracted_methods=extracted_methods,
        notes=notes,
    )

    return ConversionResult(
        plugin_type="octoprint",
        plugin_key=key,
        name=meta["name"] or key,
        version=meta["version"],
        description=meta["description"],
        author=meta["author"],
        supported_mixins=supported,
        unsupported_mixins=unsupported,
        conversion_notes=notes,
        converted_code=converted,
        plugin_dir=plugin_dir,
    )


def _find_plugin_class(tree: ast.Module) -> Optional[ast.ClassDef]:
    """Return the class assigned to __plugin_implementation__, or the first class."""
    # Find what name is assigned to __plugin_implementation__ = Foo()
    impl_class_name: Optional[str] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__plugin_implementation__":
                    if isinstance(node.value, ast.Call):
                        if isinstance(node.value.func, ast.Name):
                            impl_class_name = node.value.func.id

    # Find the class
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if impl_class_name is None or node.name == impl_class_name:
                return node
    return None


def _ast_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_ast_name(node.value)}.{node.attr}"
    return ""


def _normalize_method_source(source: str, col_offset: int) -> str:
    """Fix indentation from ast.get_source_segment.

    get_source_segment clips the *first* line at col_offset but returns
    subsequent lines with their full original indentation.  Strip col_offset
    spaces from every body line so that the whole method is consistently
    indented at 0 + 4 (def + body).
    """
    lines = source.splitlines()
    if len(lines) <= 1:
        return source
    result = [lines[0]]
    for line in lines[1:]:
        if line.strip():
            result.append(line[col_offset:] if line.startswith(" " * col_offset) else line.lstrip())
        else:
            result.append("")
    return "\n".join(result)


def _transform_method(source: str, method_name: str) -> str:
    """Apply OctoPrint → Bambuddy text substitutions to a method's source."""
    # Make eligible methods async (only if not already async)
    if method_name in ASYNC_METHODS:
        source = re.sub(r"^(\s*)(?<!async )def\b", r"\1async def", source, count=1)

    # self._logger → logger
    source = re.sub(r"\bself\._logger\b", "logger", source)

    # self._settings.get([key]) → None with TODO comment
    source = re.sub(
        r'self\._settings\.get\(\[([^\]]+)\](?:[^)]*)\)',
        r'None  # TODO: port self._settings.get([\1]) to Bambuddy settings',
        source,
    )
    # Any remaining self._settings references
    source = re.sub(r'\bself\._settings\b', 'None  # TODO: self._settings', source)

    # self._printer.* → TODO comment
    source = re.sub(r'\bself\._printer\b', '# TODO: self._printer', source)

    # self._plugin_manager → self._plugin_registry
    source = re.sub(r'\bself\._plugin_manager\b', 'self._plugin_registry', source)

    # Replace OctoPrint event strings with Events.* constants
    for op_name, bb_const in OCTOPRINT_EVENT_MAP.items():
        source = source.replace(f'"{op_name}"', bb_const)
        source = source.replace(f"'{op_name}'", bb_const)

    return source


def _find_octoprint_api_usage(source: str) -> list[str]:
    """Return list of OctoPrint-specific APIs used in the source."""
    found: list[str] = []
    patterns = [
        (r"\bself\._settings\b", "self._settings (OctoPrint settings helper)"),
        (r"\bself\._printer\b", "self._printer (OctoPrint printer control)"),
        (r"\bself\._plugin_manager\b", "self._plugin_manager (replaced by self._plugin_registry)"),
        (r"\bself\._event_bus\b", "self._event_bus (use plugin_registry.fire_event instead)"),
        (r"\bself\.get_template_vars\b", "get_template_vars (template rendering not supported)"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, source):
            found.append(label)
    return found


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def _generate_converted_code(
    meta: dict[str, str],
    plugin_key: str,
    class_name: str,
    supported_mixins: list[str],
    extracted_methods: list[str],
    notes: list[str],
) -> str:
    mixin_imports = ", ".join(["BambuddyPlugin"] + supported_mixins + ["Events"])
    mixin_bases = ", ".join(["BambuddyPlugin"] + supported_mixins)
    notes_block = "\n".join(f"#   {n}" for n in notes) if notes else "#   (none)"
    methods_block = "\n\n".join(extracted_methods) if extracted_methods else "    pass"

    return f'''"""
Converted from OctoPrint plugin by Bambuddy.

Original : {meta["name"]} v{meta["version"]}
Author   : {meta["author"]}

Conversion notes:
{notes_block}

Review TODO comments before enabling this plugin.
"""
import logging

from backend.app.plugins.base import (
    {mixin_imports},
)

__plugin_name__ = "{meta["name"]}"
__plugin_version__ = "{meta["version"]}"
__plugin_description__ = "{meta["description"]}"
__plugin_author__ = "{meta["author"]}"

logger = logging.getLogger(__name__)


class {class_name}({mixin_bases}):
{methods_block}


__plugin_implementation__ = {class_name}()
'''


def _generate_skeleton(meta: dict[str, str], plugin_key: str) -> str:
    return f'''"""
Converted from OctoPrint plugin by Bambuddy (skeleton — class not detected).

Original : {meta["name"]} v{meta["version"]}
Review and fill in the plugin body before enabling.
"""
import logging

from backend.app.plugins.base import BambuddyPlugin, Events

__plugin_name__ = "{meta["name"]}"
__plugin_version__ = "{meta["version"]}"
__plugin_description__ = "{meta["description"]}"
__plugin_author__ = "{meta["author"]}"

logger = logging.getLogger(__name__)


class ConvertedPlugin(BambuddyPlugin):
    pass  # TODO: port your plugin logic here


__plugin_implementation__ = ConvertedPlugin()
'''
