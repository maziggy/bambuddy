"""Started with DEBUG, the logs still carry no credentials.

httpx and httpcore log every request URL at INFO/DEBUG, and some carry
credentials (bot tokens, webhook URLs, API keys in the query). The SQL echo and
aiosqlite log every statement's parameters, among them every secret saved. The
runtime log-level toggle already kept these quiet; a start with the DEBUG
environment variable did not.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_a_debug_start_logs_no_request_urls_or_sql_parameters(tmp_path):
    env = {
        **os.environ,
        "DEBUG": "true",
        "DATA_DIR": str(tmp_path),
        "LOG_DIR": str(tmp_path / "logs"),
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'b.db'}",
    }
    code = (
        "import logging\n"
        "import backend.app.main  # noqa: F401\n"
        "from backend.app.core.database import engine\n"
        "print(engine.sync_engine.hide_parameters)\n"
        "print(logging.getLogger('httpx').getEffectiveLevel(), logging.getLogger('httpcore').getEffectiveLevel(),"
        " logging.getLogger('aiosqlite').getEffectiveLevel())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120, check=True
    )

    *_, hidden, levels = result.stdout.strip().splitlines()
    assert hidden == "True"
    assert levels.split() == [str(logging.WARNING)] * 3
