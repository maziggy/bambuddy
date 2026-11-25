"""Startup creates the table of every model (#3502).

init_db() imports the model modules so create_all knows their tables. A module
missing from that list (and from models/__init__) gets no table on a real
start, while the test suite, which imports every model itself, never notices:
hms_notified_faults (#3486) was never created that way. So this starts a clean
Python process, runs init_db() against an empty SQLite database and checks the
tables against the ones the model files declare.
"""

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MODELS = REPO / "backend" / "app" / "models"


def _declared_tables() -> set[str]:
    tables = set()
    for path in MODELS.glob("*.py"):
        source = path.read_text()
        tables.update(re.findall(r'__tablename__\s*=\s*"(\w+)"', source))
        tables.update(re.findall(r'Table\(\s*"(\w+)"', source))
    return tables


@pytest.mark.integration
@pytest.mark.sqlite_only  # inspects the SQLite file the started process creates
def test_a_fresh_start_creates_every_model_table(tmp_path):
    db_path = tmp_path / "bambuddy.db"
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "LOG_DIR": str(tmp_path / "logs"),
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio\n"
            "from backend.app.core.database import init_db, engine\n"
            "async def main():\n"
            "    await init_db()\n"
            "    await engine.dispose()\n"
            "asyncio.run(main())\n",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-3000:]

    with sqlite3.connect(db_path) as db:
        created = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    declared = _declared_tables()
    assert "hms_notified_faults" in declared
    assert declared - created == set()
