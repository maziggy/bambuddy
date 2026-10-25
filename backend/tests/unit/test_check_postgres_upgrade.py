"""The comparison in scripts/check_postgres_upgrade.py.

The script itself needs a PostgreSQL server and runs in CI; these cover how it
decides: what counts as drift, and that the allowlist can only shrink.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_postgres_upgrade.py"
_spec = importlib.util.spec_from_file_location("check_postgres_upgrade", SCRIPT)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

FK = "constraint print_log_entries: FOREIGN KEY (archive_id) REFERENCES print_archives(id) ON DELETE SET NULL"
FRESH = {
    "column spool.weight_used_baseline": "double precision (udt float8, precision 53) NOT NULL default None",
    FK: "1x",
}


def test_identical_schemas_do_not_drift():
    assert check.drift(FRESH, dict(FRESH)) == set()


def test_a_column_built_differently_drifts_with_both_definitions():
    upgraded = FRESH | {"column spool.weight_used_baseline": "real (udt float4, precision 24) NULL default 0"}
    assert check.drift(FRESH, upgraded) == {
        "column spool.weight_used_baseline | fresh install: double precision (udt float8, precision 53) NOT NULL "
        "default None; upgraded: real (udt float4, precision 24) NULL default 0"
    }


def test_a_missing_constraint_drifts_and_so_does_an_extra_one():
    upgraded = {k: v for k, v in FRESH.items() if k != FK} | {"column legacy.only_upgraded": "text NULL default None"}
    assert check.drift(FRESH, upgraded) == {
        f"{FK} | fresh install: 1x; upgraded: missing",
        "column legacy.only_upgraded | fresh install: missing; upgraded: text NULL default None",
    }


def test_a_duplicated_constraint_drifts():
    assert check.drift(FRESH, FRESH | {FK: "2x"}) == {f"{FK} | fresh install: 1x; upgraded: 2x"}


def test_unlisted_drift_fails_and_listed_drift_passes():
    found = {"v0.2.3": {"column a.x | one way", "column a.y | another"}}
    assert check.compare(found, {"column a.x | one way", "column a.y | another"}) == []
    assert check.compare(found, {"column a.x | one way"}) == ["upgraded from v0.2.3: column a.y | another"]


def test_a_listed_object_that_now_differs_in_another_way_fails():
    """The allowlist records how an object drifts, not just that it does: a new
    regression on a listed column must not hide behind its old entry."""
    found = {"v0.2.3": {"column a.x | fresh install: varchar(200); upgraded: varchar(30)"}}
    problems = check.compare(found, {"column a.x | fresh install: varchar(30); upgraded: varchar(30) NULL"})
    assert "upgraded from v0.2.3: column a.x | fresh install: varchar(200); upgraded: varchar(30)" in problems


def test_a_listed_line_that_no_longer_drifts_from_any_release_fails():
    """So the list only ever shrinks: a fix must remove its line."""
    found = {"v0.2.3": {"column a.x | d"}, "v1.2.5.7": set()}
    problems = check.compare(found, {"column a.x | d", "column a.fixed | d"})
    assert problems == [f"no longer drifts, remove it from {check.ALLOWLIST.name}: column a.fixed | d"]


def test_a_line_drifting_from_only_one_release_is_still_in_use():
    found = {"v0.2.3": {"column a.x | d"}, "v1.2.5.7": set()}
    assert check.compare(found, {"column a.x | d"}) == []


def test_the_allowlist_skips_comment_lines_but_keeps_a_hash_inside_a_line(tmp_path):
    path = tmp_path / "drift.txt"
    path.write_text("# header\n\ncolumn a.x | default '#1'\n  # indented comment\n", encoding="utf-8")
    assert check.read_allowlist(path) == {"column a.x | default '#1'"}


def test_the_database_name_and_driver_are_replaced_in_the_url():
    url = "postgresql+asyncpg://u:p@db:5432/postgres?sslmode=disable"
    assert check.with_database(url, "x") == "postgresql://u:p@db:5432/x?sslmode=disable"
    assert check.with_database(url, "x", "asyncpg") == "postgresql+asyncpg://u:p@db:5432/x?sslmode=disable"
