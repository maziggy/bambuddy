"""Backstop: every Path-arithmetic site in the API routes that joins a
variable to a directory-like parent must either use ``safe_join_under`` or
carry a ``# SEC-PATH-OK: <reason>`` marker.

A critical advisory traced to plain ``Path / user_string`` arithmetic in
``import_project_file`` — the join had no resolve + containment check, and an
attacker-supplied absolute path collapsed the left side. This test catches the
same shape in any new route added later: it AST-walks every Python file under
``backend/app/api/routes/`` and flags every ``a / b`` where ``a`` looks like a
directory variable and ``b`` is a non-constant (i.e. variable / call result).

False positives are intentionally cheap to silence (add a one-line
``# SEC-PATH-OK: <reason>`` justifying the existing guard) so that *future*
unsafe joins are noisy by default.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# The route surface receives external input directly; the services layer is
# called by the routes and routinely receives values that originated from a
# request (filenames, query params) or from an untrusted external source
# (printer FTP listings — the printer is part of the threat surface in the
# compromised-printer model). Both layers need the strictest gate.
_BACKEND_APP = Path(__file__).resolve().parents[2] / "app"
SCAN_DIRS = [
    _BACKEND_APP / "api" / "routes",
    _BACKEND_APP / "services",
]

# Identifier substrings that suggest the LHS is a filesystem directory. Heuristic
# but tuned to Bambuddy's conventions — every actual directory variable in the
# routes hits one of these.
_DIR_NAME_HINTS = (
    "_dir",
    "_path",
    "dir_",
    "path_",
    "temp_path",
    "library_dir",
    "archive_dir",
    "photos_dir",
    "base_dir",
    "ext_dir",
    "attachments_dir",
    "static_dir",
    "log_dir",
    "data_dir",
    "folder_path",
    "file_disk_path",
    "photo_path",
    "dest",
    "output_path",
)

# Function calls whose return value is a Path under our control. Hits to these
# don't need scrutiny — they're constructed by Bambuddy code, not by the request.
_KNOWN_PATH_FACTORIES = (
    "Path",
    "get_library_dir",
    "get_library_files_dir",
    "get_archive_dir",
    "get_project_attachments_dir",
    "get_project_cover_dir",
    "resolve",
)

_MARKER = "# SEC-PATH-OK:"


def _looks_path_like(node: ast.AST) -> bool:
    """Heuristic for whether *node* evaluates to a ``pathlib.Path``."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _KNOWN_PATH_FACTORIES:
            return True
        return bool(isinstance(func, ast.Attribute) and func.attr in _KNOWN_PATH_FACTORIES)
    if isinstance(node, ast.Name):
        return any(hint in node.id for hint in _DIR_NAME_HINTS)
    if isinstance(node, ast.Attribute):
        # `settings.base_dir`, `cls.archive_dir`, etc.
        return any(hint in node.attr for hint in _DIR_NAME_HINTS)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        # Chains like ``base_dir / "x" / variable`` — keep looking left.
        return _looks_path_like(node.left)
    return False


def _is_constant_string(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _rhs_is_attacker_shape(node: ast.AST) -> bool:
    """The high-risk shape is ``path / Name`` — RHS is a bare variable that
    came from somewhere outside this scope (a function parameter, request
    body field, ZIP namelist entry).

    Attribute (``lib_file.file_path``), Subscript (``photos[i]``), Call
    (``str(vp_id)``), and JoinedStr (f-strings) all have *some* structure
    that the audit can reason about — those are caught by the broader audit
    sweep, not the regression backstop. This narrows the noise to the exact
    shape that produced the path-traversal class so the backstop only fires
    when something that *looks* like that bug appears.
    """
    return isinstance(node, ast.Name)


_CONTINUATION_TOKENS = (")", "]", "}", ",")


def _line_has_marker(source_lines: list[str], lineno: int, end_lineno: int | None) -> bool:
    """Check whether a ``# SEC-PATH-OK:`` marker covers this join.

    Looks at every line spanned by the BinOp itself, plus one line past
    ``end_lineno`` IF that line begins with a continuation token (``)``,
    ``]``, ``}``, ``,``). The peek captures the project's convention of
    wrapping a BinOp in parens and placing the marker on the closing line:

        file_path = (
            library_dir / filename
        )  # SEC-PATH-OK: filename validated above

    The BinOp itself ends on the inner line, but the marker reads more
    naturally on the closing line. Restricting the peek to continuation
    lines prevents giving a free pass to a marker that happens to sit on
    a wholly unrelated follow-on statement.
    """
    start = max(1, lineno)
    end = max(start, end_lineno or lineno)
    for i in range(start, end + 1):
        if i - 1 >= len(source_lines):
            continue
        if _MARKER in source_lines[i - 1]:
            return True
    # Project convention: marker often sits on the closing-paren line, one
    # line past the BinOp's `end_lineno`. Only accept it when the line is a
    # continuation of the wrapping expression.
    if end < len(source_lines):
        trailing = source_lines[end].lstrip()
        if trailing.startswith(_CONTINUATION_TOKENS) and _MARKER in source_lines[end]:
            return True
    return False


def _enclosing_call_is_safe_join(stack: list[ast.AST]) -> bool:
    """True if the BinOp is being passed directly into ``safe_join_under(...)``.

    Tracking parent links keeps the test conservative — a ``base_dir / x``
    expression that's already inside ``safe_join_under(base_dir / x, ...)``
    is fine because the helper does its own containment check. This rarely
    happens in practice but keeps the test from yelling about an idiomatic
    arrangement.
    """
    for ancestor in reversed(stack):
        if isinstance(ancestor, ast.Call):
            func = ancestor.func
            if isinstance(func, ast.Name) and func.id == "safe_join_under":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "safe_join_under":
                return True
    return False


def _scan_file(py_file: Path) -> list[str]:
    source = py_file.read_text()
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(py_file))
    findings: list[str] = []

    # Walk with parent stack so we can detect "inside safe_join_under" and
    # skip such nodes.
    stack: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        stack.append(node)
        try:
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div)
                and _looks_path_like(node.left)
                and _rhs_is_attacker_shape(node.right)
                and not _is_constant_string(node.right)
                and not _enclosing_call_is_safe_join(stack)
                and not _line_has_marker(source_lines, node.lineno, node.end_lineno)
            ):
                line = source_lines[node.lineno - 1].strip() if node.lineno - 1 < len(source_lines) else "<?>"
                findings.append(f"{py_file.name}:{node.lineno}  {line}")
            for child in ast.iter_child_nodes(node):
                visit(child)
        finally:
            stack.pop()

    visit(tree)
    return findings


def _scan_source(source: str, tmp_path: Path) -> list[str]:
    """Drop ``source`` into a temp .py file and run ``_scan_file`` against it.
    Returns the findings list."""
    f = tmp_path / "candidate.py"
    f.write_text(source)
    return _scan_file(f)


class TestMarkerDetection:
    """Pins the contract for where a ``# SEC-PATH-OK:`` marker is accepted.

    Markers must sit either (a) somewhere within the BinOp's own source
    lines, or (b) on the immediately-following line when that line is a
    continuation token (closing paren / bracket / brace / comma). The
    second case is the project's convention of wrapping the BinOp in
    parens and placing the marker on the closing-paren line.
    """

    def test_marker_on_binop_line_recognised(self, tmp_path):
        source = (
            "from pathlib import Path\nbase_dir = Path('.')\ndef f(x): return base_dir / x  # SEC-PATH-OK: trusted x\n"
        )
        assert _scan_source(source, tmp_path) == []

    def test_marker_on_closing_paren_line_recognised(self, tmp_path):
        # The exact convention used across api/routes/ and services/: the
        # BinOp lives inside a parenthesised expression and the marker sits
        # on the closing-paren line, one past the BinOp's `end_lineno`.
        source = (
            "from pathlib import Path\n"
            "base_dir = Path('.')\n"
            "def f(x):\n"
            "    return (\n"
            "        base_dir / x\n"
            "    )  # SEC-PATH-OK: trusted x\n"
        )
        assert _scan_source(source, tmp_path) == []

    def test_unrelated_marker_below_does_not_silence(self, tmp_path):
        # A second join below carries a marker; the first does not. Only
        # the first must be flagged — markers don't cross statement
        # boundaries.
        source = (
            "from pathlib import Path\n"
            "base_dir = Path('.')\n"
            "def f(x, y):\n"
            "    p = base_dir / x\n"
            "    q = base_dir / y  # SEC-PATH-OK: trusted y\n"
        )
        findings = _scan_source(source, tmp_path)
        assert len(findings) == 1
        assert "base_dir / x" in findings[0]

    def test_marker_on_non_continuation_line_does_not_silence(self, tmp_path):
        # A SEC-PATH-OK comment on the line right after the BinOp counts
        # only when that line is a continuation of the wrapping expression.
        # An unrelated next statement's marker must not free-pass the join.
        source = (
            "from pathlib import Path\n"
            "base_dir = Path('.')\n"
            "def f(x):\n"
            "    a = base_dir / x\n"
            "    b = 1  # SEC-PATH-OK: unrelated, for a different statement\n"
        )
        findings = _scan_source(source, tmp_path)
        assert len(findings) == 1
        assert "base_dir / x" in findings[0]

    def test_no_marker_anywhere_is_flagged(self, tmp_path):
        # Pin the negative path so a future refactor can't accidentally turn
        # marker detection into "always returns True".
        source = "from pathlib import Path\nbase_dir = Path('.')\ndef f(x): return base_dir / x\n"
        findings = _scan_source(source, tmp_path)
        assert len(findings) == 1
        assert "base_dir / x" in findings[0]


def test_route_path_arithmetic_is_safe_joined_or_marked():
    """Every ``<dir-like> / <non-constant>`` join in a route handler must
    either route through ``safe_join_under(...)`` or carry a
    ``# SEC-PATH-OK: <reason>`` marker on one of its source lines.

    Adding ``# SEC-PATH-OK: <reason>`` is the escape hatch for sites where
    the input has already been validated (e.g. a denylist + membership
    check, a pre-sanitised alphanumeric filter, or an explicit resolve +
    ``relative_to`` containment check inline). The marker MUST explain the
    existing guard — silent suppression defeats the backstop's purpose.
    """
    findings: list[str] = []
    for scan_dir in SCAN_DIRS:
        for py_file in sorted(scan_dir.rglob("*.py")):
            if py_file.name == "__init__.py":
                continue
            findings.extend(_scan_file(py_file))

    if findings:
        pytest.fail(
            "Found Path-arithmetic sites in api/routes/ or services/ that "
            "join a non-constant value to a directory-like parent without "
            "using safe_join_under() or carrying a # SEC-PATH-OK: marker. "
            "Each site must either be refactored to "
            "safe_join_under(parent, *parts) or tagged with the marker "
            "explaining why the existing guard is sufficient.\n\nFindings:\n" + "\n".join(findings)
        )
