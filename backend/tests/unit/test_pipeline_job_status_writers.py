"""Queue status changes stay visible to the pipeline job hooks.

services/pipeline_job_status stores a job's final status from flushes and from
statements run through a session. A status written or a row deleted as raw SQL
text would skip both, and the job would forget whether its copy printed.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

# Matched across lines: SQL is usually split into string pieces.
RAW_WRITES = re.compile(
    r"UPDATE\s+print_queue\b[\s\"']*SET\b[^;)]*?\bstatus\s*=|DELETE\s+FROM\s+print_queue\b",
    re.IGNORECASE,
)

# Raw writes that cannot end a job, with why.
ALLOWED = {
    # The #1667 migration resets skipped items to pending: not a final status.
    "core/database.py": 1,
}


def test_no_raw_sql_ends_or_deletes_a_queue_entry():
    found: dict[str, int] = {}
    for path in APP.rglob("*.py"):
        count = len(RAW_WRITES.findall(path.read_text(encoding="utf-8")))
        if count:
            found[str(path.relative_to(APP))] = count
    assert found == ALLOWED
