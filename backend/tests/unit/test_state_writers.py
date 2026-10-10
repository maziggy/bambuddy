"""
Every function that writes a guarded field is listed here, with why it writes it.

Some fields are written from many places, and those writers drift apart: one
honours a lock or skips while a print runs, a sibling added later does not.
This test makes a new writer a deliberate step. Adding a write to a guarded
field fails here until the function is added to ``allowed`` below, which is
the moment to check the existing writers' guards and give the new one the same.

A listed function that no longer writes the field fails too, so the list stays
the real list of writers.

Found statically: attribute assignment (``spool.weight_used = ...``, ``+=``),
the model constructor with the field as a keyword or with ``**kwargs``,
``.values(field=...)``, ``setattr(obj, "field", ...)`` and raw ``UPDATE <table>``
SQL that names the field. Not found: ``setattr`` with a variable name (generic
PATCH loops), so those are listed by hand where they reach the field.
"""

import ast
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent.parent / "app"
REPO_DIR = BACKEND_DIR.parent.parent


GUARDED_FIELDS = [
    {
        "field": "weight_used",
        "model": "Spool",
        "table": "spool",
        "allowed": {
            ("backend/app/main.py", "on_ams_change"): "AMS remain% sync while idle",
            ("backend/app/api/routes/inventory.py", "sync_weights_from_ams"): "manual Sync AMS weights",
            ("backend/app/api/routes/spoolbuddy.py", "update_spool_weight"): "SpoolBuddy scale reading",
            ("backend/app/services/usage_tracker.py", "on_print_complete"): "AMS remain% delta fallback",
            ("backend/app/services/usage_tracker.py", "_track_from_3mf"): "3MF per-filament usage",
            ("backend/app/services/spool_tag_matcher.py", "create_spool_from_tray"): "new spool from an RFID tray",
            ("backend/app/core/database.py", "_migrate_repair_rfid_core_weight"): "one-shot #2909 backfill",
            ("backend/app/api/routes/inventory.py", "create_spool"): "Spool(**payload) from the add form",
            ("backend/app/api/routes/inventory.py", "bulk_create_spools"): "Spool(**payload) per bulk-added spool",
            ("backend/app/api/routes/inventory.py", "import_spools_csv"): "Spool(**row) from CSV import",
        },
        # Reached through setattr loops or Spool(**payload) built from user input;
        # the static scan cannot see the field name, so these are listed by hand.
        "dynamic": {
            ("backend/app/api/routes/inventory.py", "update_spool"): "PATCH /spools/{id}",
            ("backend/app/api/routes/inventory.py", "bulk_update_spools"): "POST /spools/bulk-update",
            ("backend/app/services/github_restore.py", "_restore_spools"): "backup restore",
        },
    },
]


class _WriterVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, field: str, model: str, table: str):
        self.rel_path = rel_path
        self.field = field
        self.model = model
        self.sql = re.compile(rf"update\s+{table}\b.*\b{field}\b", re.IGNORECASE | re.DOTALL)
        self.scope: list[str] = []
        self.hits: list[tuple[str, str, int]] = []

    def _function(self) -> str:
        # Report the outermost function, so helpers nested inside it stay covered
        # by its entry.
        return self.scope[0] if self.scope else "<module>"

    def _hit(self, node: ast.AST) -> None:
        self.hits.append((self.rel_path, self._function(), node.lineno))

    def _enter(self, node) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Methods are reported by name only; the class adds nothing for the list.
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Attribute) and sub.attr == self.field:
                    self._hit(node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Attribute) and node.target.attr == self.field:
            self._hit(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in (self.model, "values") and any(kw.arg == self.field for kw in node.keywords):
            self._hit(node)
        if name == self.model and any(kw.arg is None for kw in node.keywords):
            self._hit(node)
        if (
            name == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == self.field
        ):
            self._hit(node)
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        # A bare string statement is a docstring, not SQL.
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and self.sql.search(node.value):
            self._hit(node)


def _find_writers(field: str, model: str, table: str) -> dict[tuple[str, str], list[int]]:
    writers: dict[tuple[str, str], list[int]] = {}
    for path in sorted(BACKEND_DIR.rglob("*.py")):
        rel_path = path.relative_to(REPO_DIR).as_posix()
        visitor = _WriterVisitor(rel_path, field, model, table)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for file, function, line in visitor.hits:
            writers.setdefault((file, function), []).append(line)
    return writers


def _function_exists(rel_path: str, function: str) -> bool:
    tree = ast.parse((REPO_DIR / rel_path).read_text(encoding="utf-8"))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function for node in ast.walk(tree)
    )


class TestGuardedFieldWriters:
    def test_no_unlisted_writers(self):
        problems = []
        for guard in GUARDED_FIELDS:
            found = _find_writers(guard["field"], guard["model"], guard["table"])
            for (file, function), lines in sorted(found.items()):
                if (file, function) not in guard["allowed"] and (file, function) not in guard["dynamic"]:
                    problems.append(
                        f"{file}:{lines[0]} {function}() writes {guard['model']}.{guard['field']}, "
                        "but is not listed. Compare its guards with the listed writers, then add it."
                    )
        assert not problems, "\n".join(problems)

    def test_listed_writers_still_write(self):
        problems = []
        for guard in GUARDED_FIELDS:
            found = _find_writers(guard["field"], guard["model"], guard["table"])
            for file, function in sorted(guard["allowed"]):
                if (file, function) not in found:
                    problems.append(
                        f"{file} {function}() is listed as a writer of {guard['model']}.{guard['field']} "
                        "but no longer writes it. Remove it from the list."
                    )
            for file, function in sorted(guard["dynamic"]):
                if not _function_exists(file, function):
                    problems.append(f"{file} {function}() is listed but no longer exists. Remove it from the list.")
        assert not problems, "\n".join(problems)
