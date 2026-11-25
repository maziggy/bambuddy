"""Every spool assignment writer logs a mid-print swap (#3325).

A spool assigned to a tray while a print runs splits the print between it and
the spool the tray started with, but only if the writer told
``spool_swaps.record_assignment`` about it. A writer added later without that
call would put the whole print on the new spool again, so each function that
writes an assignment row must make the call, or be listed below with why not.
"""

import ast
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent.parent / "app"

WRITES_ASSIGNMENT = re.compile(
    r"INSERT INTO spoolman_slot_assignments|INSERT INTO spool_assignment|SpoolAssignment\(|"
    r"update\(SpoolmanSlotAssignment\)|update\(SpoolAssignment\)"
)

EXEMPT = {
    ("core/database.py", "_migrate_widen_spoolman_slot_ams_id_range"): "startup migration copying existing rows",
}


def _writers():
    for path in BACKEND_DIR.rglob("*.py"):
        source = path.read_text()
        if not WRITES_ASSIGNMENT.search(source):
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(source, node) or ""
            nested = [
                n for n in ast.walk(node) if n is not node and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if WRITES_ASSIGNMENT.search(body) and not any(
                WRITES_ASSIGNMENT.search(ast.get_source_segment(source, n) or "") for n in nested
            ):
                yield str(path.relative_to(BACKEND_DIR)), node.name, "record_assignment" in body


def test_every_assignment_writer_logs_a_swap():
    writers = list(_writers())
    assert len(writers) >= 7  # the scan finds the writers at all

    missing = sorted(f"{path}:{name}" for path, name, records in writers if not records and (path, name) not in EXEMPT)
    assert missing == [], "call spool_swaps.record_assignment before committing:\n" + "\n".join(missing)


def test_exemptions_still_exist():
    found = {(path, name) for path, name, _ in _writers()}
    assert set(EXEMPT) <= found
