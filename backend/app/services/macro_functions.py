"""Registry of macro system functions.

This module owns only the registry machinery: the decorator, the data types,
and the discover() auto-loader.  Actual function implementations live in
backend/app/services/macro_integrations/*.py — each file is a self-contained
domain (printer control, notifications, spoolman, etc.).

─── Adding a new system function ─────────────────────────────────────────────

Create or edit a file in  backend/app/services/macro_integrations/

    from backend.app.services.macro_functions import macro_function, FunctionContext, FunctionResult

    @macro_function(
        name="MY_COMMAND",
        description="Does something useful.",
        args={"value": ArgSpec("Some value", required=True)},
        context_var=None,          # None → command only
        requires_printer=False,
        allowed_in_embed=True,
    )
    async def _my_command(ctx: FunctionContext) -> FunctionResult:
        ...
        return FunctionResult(ok=True)

That's it.  No other files need to change.

─── Integration points ───────────────────────────────────────────────────────

1. Imperative commands  — any registered name is a valid command token.
2. Context variables    — context_var functions run before Jinja2 render;
                          their FunctionResult.value is injected by that name.
3. API catalogue        — GET /macros/functions returns all_specs() as JSON,
                          which the editor uses to build the hints panel.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ── Public data types ─────────────────────────────────────────────────────────


@dataclass
class ArgSpec:
    description: str
    required: bool = False
    default: str | None = None


@dataclass
class FunctionSpec:
    """Serialisable descriptor — returned by the catalogue API."""

    name: str
    description: str
    args: dict[str, ArgSpec]
    context_var: str | None  # Jinja2 variable name, or None if command-only
    requires_printer: bool
    allowed_in_embed: bool


@dataclass
class FunctionContext:
    """Runtime context passed to every function implementation."""

    flags: dict[str, str]  # parsed --key=value flags
    printer_id: int | None  # resolved printer id for this run
    run_id: int | None  # MacroRun id (None in exec_line / sub-macro)
    log: Any  # async callable: log(run_id, text)
    allow_printer_commands: bool = True
    # Opaque runner reference + call_stack — set only when inside run_macro
    _runner: Any = None
    _call_stack: frozenset[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._call_stack is None:
            self._call_stack = frozenset()


@dataclass
class FunctionResult:
    ok: bool = True
    message: str = ""  # appended to run log
    value: Any = None  # only used for context_var functions


# ── Registry ──────────────────────────────────────────────────────────────────


_REGISTRY: dict[str, tuple[FunctionSpec, Any]] = {}  # name → (spec, coro_fn)


def macro_function(
    *,
    name: str,
    description: str,
    args: dict[str, str | ArgSpec] | None = None,
    context_var: str | None = None,
    requires_printer: bool = False,
    allowed_in_embed: bool = True,
):
    """Decorator that registers a coroutine as a macro system function.

    Can be used from any module — typically files under macro_integrations/.
    The decorated function receives a single FunctionContext argument.
    """

    def _normalise(a: dict[str, str | ArgSpec] | None) -> dict[str, ArgSpec]:
        if not a:
            return {}
        return {k: (v if isinstance(v, ArgSpec) else ArgSpec(v)) for k, v in a.items()}

    def decorator(fn):
        uname = name.upper()
        spec = FunctionSpec(
            name=uname,
            description=description,
            args=_normalise(args),
            context_var=context_var,
            requires_printer=requires_printer,
            allowed_in_embed=allowed_in_embed,
        )
        if uname in _REGISTRY:
            logger.warning("macro_function: overwriting existing registration for %s", uname)
        _REGISTRY[uname] = (spec, fn)
        logger.debug("macro_function registered: %s", uname)
        return fn

    return decorator


# ── Discovery ─────────────────────────────────────────────────────────────────


def discover() -> None:
    """Import every module in macro_integrations/ so their decorators fire.

    Called once at application startup (main.py lifespan).  Safe to call
    multiple times — subsequent calls are no-ops because Python caches modules.
    """
    import backend.app.services.macro_integrations as _pkg  # noqa: PLC0415

    for _info in pkgutil.iter_modules(_pkg.__path__):
        module_name = f"backend.app.services.macro_integrations.{_info.name}"
        try:
            importlib.import_module(module_name)
            logger.debug("macro_functions: loaded integration %s", module_name)
        except Exception:  # noqa: BLE001
            logger.exception("macro_functions: failed to load integration %s", module_name)


# ── Registry accessors ────────────────────────────────────────────────────────


def get_registry() -> dict[str, tuple[FunctionSpec, Any]]:
    return _REGISTRY


def get_spec(name: str) -> FunctionSpec | None:
    entry = _REGISTRY.get(name.upper())
    return entry[0] if entry else None


def all_specs() -> list[FunctionSpec]:
    return [spec for spec, _ in _REGISTRY.values()]


def command_names() -> frozenset[str]:
    return frozenset(_REGISTRY)


def embed_blocked_names() -> frozenset[str]:
    return frozenset(name for name, (spec, _) in _REGISTRY.items() if not spec.allowed_in_embed)


# ── Execution helpers ─────────────────────────────────────────────────────────


async def execute(name: str, ctx: FunctionContext) -> FunctionResult:
    """Look up and call a registered function."""
    entry = _REGISTRY.get(name.upper())
    if entry is None:
        return FunctionResult(ok=False, message=f"[ERROR] Unknown system command: {name}\n")

    spec, fn = entry
    if spec.requires_printer and ctx.printer_id is None:
        msg = f"[ERROR] {name} requires a target printer\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)

    try:
        return await fn(ctx)
    except Exception as exc:  # noqa: BLE001
        msg = f"[ERROR] {name} failed: {exc}\n"
        logger.exception("macro_function %s raised", name)
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)


async def build_context_values(printer_id: int | None, log_fn, run_id: int | None = None) -> dict[str, Any]:
    """Call all context_var functions eagerly; return {var_name: value}.

    Errors are swallowed: the variable is set to None and a warning is logged.
    """
    out: dict[str, Any] = {}
    for spec, fn in _REGISTRY.values():
        if spec.context_var is None:
            continue
        ctx = FunctionContext(flags={}, printer_id=printer_id, run_id=run_id, log=log_fn)
        try:
            result = await fn(ctx)
            out[spec.context_var] = result.value
        except Exception as exc:  # noqa: BLE001
            logger.warning("context_var function %s failed: %s", spec.name, exc)
            out[spec.context_var] = None
    return out
