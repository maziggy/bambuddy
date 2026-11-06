"""Restoring a SQLite backup onto PostgreSQL gives old projects a valid priority and status.

Older versions stored any project priority or status; one longer than the
column made PostgreSQL refuse the row and the whole restore failed (measured
end to end against a throwaway PostgreSQL when this was written).
"""

from backend.app.api.routes.settings import _restored_value_fixes
from backend.app.models.project import PROJECT_PRIORITIES, PROJECT_STATUSES


def test_projects_fall_back_like_import_and_the_start_up_repair():
    fixes = _restored_value_fixes("projects")

    assert fixes == {"priority": (PROJECT_PRIORITIES, "normal"), "status": (PROJECT_STATUSES, "active")}


def test_other_tables_are_copied_as_they_are():
    assert _restored_value_fixes("print_queue") == {}
