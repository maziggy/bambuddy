"""A route takes its printers from a media token only if its permission check does too."""

from __future__ import annotations

import ast
from pathlib import Path

API = Path(__file__).resolve().parents[2] / "app" / "api"
MEDIA_SCOPE = {"MediaOrRequestPrinterScope", "get_media_or_request_printer_scope"}
HEADER_PRINCIPAL = {"RequestPrinterScope", "get_printer_scope_if_auth_enabled", "RequestActor"}


def _names(node: ast.AST) -> set[str]:
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def _route_dependencies(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.decorator_list:
            continue
        args = node.args
        parts = [*args.defaults, *[d for d in args.kw_defaults if d is not None]]
        parts += [a.annotation for a in [*args.args, *args.kwonlyargs] if a.annotation is not None]
        names = set().union(*(_names(p) for p in parts)) if parts else set()
        yield f"{path.relative_to(API)}:{node.name}", names


def test_a_route_takes_its_caller_from_one_place():
    offenders = []
    for path in sorted(API.rglob("*.py")):
        for route, names in _route_dependencies(path):
            media_gate = any(name.startswith("require_media_token_") for name in names)
            if names & MEDIA_SCOPE and not media_gate:
                offenders.append(f"{route}: media-token printers without a media-token check")
            if media_gate and names & HEADER_PRINCIPAL:
                offenders.append(f"{route}: media-token check with a header-based caller")
    assert offenders == []
