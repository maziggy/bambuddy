"""Check that upgrading a PostgreSQL database gives the schema of a fresh install.

A fresh install gets every table from the models (``create_all``); an existing
database only gets what the migrations in ``core/database.py`` add to it. Run
on an empty database, as a plain migration check would, ``create_all`` builds
everything first and the migrations have nothing left to do, so a migration
that is missing or adds a column differently goes unnoticed.

This builds a database with an older release's code, upgrades it with the
current code twice, and compares its schema with a fresh install: every column
(type with length, precision and element type, nullability, default), and how
many of each constraint and index there are. Constraints and indexes are
compared by definition, so only a name differing is not drift. A restart must
leave every schema, fresh or upgraded, exactly as the first start left it.

Some upgrades only go wrong with data in the database; SCENARIOS builds those
along a fixed chain of releases, adds the rows, upgrades and checks the data.

scripts/postgres_schema_drift.txt can list known drift, each line the object
and exactly how it differs. Drift not listed there fails, including a listed
object that now differs in another way, and so does a listed line that no
longer drifts. The drift found when this check was introduced has been fixed
and the list is empty: fix new drift with a migration rather than listing it.

Usage (needs git with the release tags and a PostgreSQL server the given user
may create databases on; every database it creates is dropped again):

    python scripts/check_postgres_upgrade.py --url postgresql://user:pw@host:5432/postgres

Never point it at a server holding data you care about.
"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
ALLOWLIST = REPO / "scripts" / "postgres_schema_drift.txt"
# The first release with PostgreSQL support: the oldest schema an install can have.
FIRST_PG_RELEASE = "v0.2.3"


@dataclass(frozen=True)
class Scenario:
    """An upgrade that needs data to go wrong.

    The database is built along ``chain`` (old releases, pinned: a newer one may
    no longer produce the state this needs), then ``seed`` runs and the current
    code upgrades it twice. ``check`` must then return no rows; each row it
    returns is a problem.
    """

    name: str
    chain: tuple[str, ...]
    seed: str
    check: str


SCENARIOS = (
    Scenario(
        name="orphaned references",
        # v1.2.5.7 adds these columns to a v0.2.3 database without their foreign
        # keys, so deleting an archive, user or library file left ids behind.
        # Run 2 is such a leftover; its print_name matches archive 1, which an
        # every-start backfill that fills in a NULL archive_id would link it to.
        chain=("v0.2.3", "v1.2.5.7"),
        seed="""
            INSERT INTO users (id, username, role, is_active, auth_source) VALUES (1, 'kept', 'user', true, 'local');
            INSERT INTO library_files (id, filename, file_path, file_type, file_size, print_count, is_external)
            VALUES (1, 'kept.3mf', 'l/kept.3mf', '3mf', 1, 0, false);
            INSERT INTO print_archives (id, filename, file_path, file_size, status, quantity, is_favorite,
                                        print_name, library_file_id)
            VALUES (1, 'kept.3mf', 'a/kept.3mf', 1, 'completed', 1, false, 'Benchy', 1),
                   (2, 'gone.3mf', 'a/gone.3mf', 1, 'completed', 1, false, 'Other', 999999);
            INSERT INTO print_log_entries (id, status, print_name, archive_id, created_by_id)
            VALUES (1, 'completed', 'Benchy', 1, 1),
                   (2, 'completed', 'Benchy', 999999, 999999);
        """,
        check="""
            SELECT 'print_archives.library_file_id still points at a missing file'
            FROM print_archives WHERE library_file_id IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM library_files WHERE id = library_file_id)
            UNION ALL
            SELECT 'print_log_entries.archive_id still points at a missing archive'
            FROM print_log_entries WHERE archive_id IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM print_archives WHERE id = archive_id)
            UNION ALL
            SELECT 'print_log_entries.created_by_id still points at a missing user'
            FROM print_log_entries WHERE created_by_id IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM users WHERE id = created_by_id)
            UNION ALL
            SELECT 'the run of a deleted archive was linked to archive ' || archive_id
            FROM print_log_entries WHERE id = 2 AND archive_id IS NOT NULL
            UNION ALL
            SELECT 'a valid reference was changed'
            WHERE NOT EXISTS (SELECT 1 FROM print_log_entries WHERE id = 1 AND archive_id = 1 AND created_by_id = 1)
                OR NOT EXISTS (SELECT 1 FROM print_archives WHERE id = 1 AND library_file_id = 1)
        """,
    ),
    Scenario(
        name="column types",
        # v1.2.5.7 has these as TEXT and REAL in a v0.2.3 database, where a
        # fresh install has JSON and DOUBLE PRECISION. The values must survive
        # the conversion as the user saw them, and invalid JSON values must not
        # stop the upgrade (they are cleared). extra_float_digits = 0, which a
        # DBA may set, must not cost the floats their digits.
        chain=("v0.2.3", "v1.2.5.7"),
        seed="""
            DO $$ BEGIN
                EXECUTE format('ALTER DATABASE %I SET extra_float_digits = 0', current_database());
            END $$;
            INSERT INTO printers (id, name, serial_number, ip_address, access_code, auto_archive,
                                  awaiting_plate_clear, camera_rotation, external_camera_enabled, is_active,
                                  nozzle_count, plate_detection_enabled, print_hours_offset, runtime_seconds)
            VALUES (1, 'p', 'S1', '192.0.2.1', 'x', true, false, 0, false, true, 1, false, 0, 0);
            INSERT INTO print_archives (id, printer_id, filename, file_path, file_size, status, quantity, is_favorite)
            VALUES (1, 1, 'a.3mf', 'a/a.3mf', 1, 'completed', 1, false),
                   (2, 1, 'b.3mf', 'a/b.3mf', 1, 'completed', 1, false),
                   (3, 1, 'c.3mf', 'a/c.3mf', 1, 'completed', 1, false);
            INSERT INTO active_print_spoolman (id, printer_id, archive_id, ams_trays, tray_remain_start)
            VALUES (1, 1, 1, '{}', '{"0-1": {"remain": 80}}'),
                   (2, 1, 2, '{}', 'not json'),
                   (3, 1, 3, '{}', '{"0-1": {"remain": 80, "tray_uuid": NaN}}');
            INSERT INTO smart_plugs (id, name, plug_type, auto_off, auto_off_executed, auto_off_pending,
                                     auto_off_persistent, auto_on, enabled, mqtt_energy_multiplier, mqtt_multiplier,
                                     mqtt_power_multiplier, off_delay_minutes, off_delay_mode, off_temp_threshold,
                                     power_alert_enabled, schedule_enabled, show_in_switchbar, show_on_printer_card,
                                     rest_energy_total_multiplier)
            VALUES (1, 'plug', 'rest', false, false, false, false, false, true, 1, 1, 1, 0, 'time', 70,
                    false, false, false, false, 0.001);
            INSERT INTO spool (id, material, core_weight, label_weight, weight_locked, weight_used,
                               weight_used_baseline)
            VALUES (1, 'PLA', 250, 1000, false, 1234.567, 1234.567);
        """,
        check="""
            SELECT 'active_print_spoolman.tray_remain_start is ' || pg_typeof(tray_remain_start) || ', not json'
            FROM active_print_spoolman WHERE id = 1 AND pg_typeof(tray_remain_start)::text <> 'json'
            UNION ALL
            SELECT 'active_print_spoolman.tray_remain_start changed: ' || tray_remain_start::text
            FROM active_print_spoolman
            WHERE id = 1 AND tray_remain_start::text::jsonb IS DISTINCT FROM '{"0-1": {"remain": 80}}'::jsonb
            UNION ALL
            SELECT 'an invalid tray_remain_start was kept: ' || tray_remain_start::text
            FROM active_print_spoolman WHERE id IN (2, 3) AND tray_remain_start IS NOT NULL
            UNION ALL
            SELECT 'smart_plugs.rest_energy_total_multiplier is ' || rest_energy_total_multiplier::text
            FROM smart_plugs
            WHERE id = 1 AND rest_energy_total_multiplier IS DISTINCT FROM CAST(0.001 AS double precision)
            UNION ALL
            SELECT 'spool.weight_used_baseline is ' || weight_used_baseline::text
            FROM spool WHERE id = 1 AND weight_used_baseline IS DISTINCT FROM CAST(1234.567 AS double precision)
        """,
    ),
    Scenario(
        name="NULLs in columns that become NOT NULL",
        # A v0.2.3 database upgraded by v1.2.5.7 has these columns nullable.
        # A NULL is filled with the model's default, except an API key's
        # permissions: the code reads NULL as denied, and that must stay so
        # rather than become the default (granted). An OIDC provider with no
        # email claim never resolved an email, so it never linked an account:
        # filling the claim must not start that, so auto-linking goes off.
        chain=("v0.2.3", "v1.2.5.7"),
        seed="""
            INSERT INTO api_keys (id, name, key_hash, key_prefix, enabled, can_queue, can_control_printer,
                                  can_read_status, can_manage_archives, can_access_cloud)
            VALUES (1, 'k', 'hash', 'bb_', true, false, false, true, NULL, NULL);
            INSERT INTO library_files (id, filename, file_path, file_type, file_size, print_count, is_external,
                                       variant_position)
            VALUES (1, 'f.3mf', 'l/f.3mf', '3mf', 1, 0, false, NULL);
            INSERT INTO oidc_providers (id, name, issuer_url, client_id, client_secret, scopes, is_enabled,
                                        auto_create_users, auto_link_existing_accounts, email_claim,
                                        require_email_verified)
            VALUES (1, 'unsafe once filled', 'https://idp.example', 'c', 's', 'openid', true, false, true, NULL,
                    false),
                   (2, 'strict once filled', 'https://idp2.example', 'c', 's', 'openid', true, false, true, NULL,
                    NULL),
                   (3, 'has a claim', 'https://idp3.example', 'c', 's', 'openid', true, false, true, 'upn', NULL);
        """,
        check="""
            SELECT 'api_keys.can_manage_archives is ' || coalesce(can_manage_archives::text, 'NULL')
            FROM api_keys WHERE id = 1 AND can_manage_archives IS DISTINCT FROM false
            UNION ALL
            SELECT 'api_keys.can_access_cloud is ' || coalesce(can_access_cloud::text, 'NULL')
            FROM api_keys WHERE id = 1 AND can_access_cloud IS DISTINCT FROM false
            UNION ALL
            SELECT 'library_files.variant_position is ' || coalesce(variant_position::text, 'NULL')
            FROM library_files WHERE id = 1 AND variant_position IS DISTINCT FROM 0
            UNION ALL
            SELECT 'oidc_providers ' || id || ': ' || coalesce(email_claim, 'NULL') || ', verified '
                   || coalesce(require_email_verified::text, 'NULL') || ', auto-link '
                   || auto_link_existing_accounts::text
            FROM oidc_providers
            WHERE (id = 1 AND (email_claim IS DISTINCT FROM 'email' OR require_email_verified IS DISTINCT FROM false
                               OR auto_link_existing_accounts))
               OR (id = 2 AND (email_claim IS DISTINCT FROM 'email' OR require_email_verified IS DISTINCT FROM true
                               OR auto_link_existing_accounts))
               OR (id = 3 AND (email_claim IS DISTINCT FROM 'upn' OR require_email_verified IS DISTINCT FROM true
                               OR NOT auto_link_existing_accounts))
        """,
    ),
)

_INIT_DB = """
import asyncio
from backend.app.core import database
from backend.app.core.config import settings
assert settings.database_url.startswith("postgresql+asyncpg://"), settings.database_url
asyncio.run(database.init_db())
"""


def newest_stable_release() -> str:
    tags = subprocess.run(
        ["git", "tag", "--list", "v*", "--sort=-v:refname"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.split()
    stable = [t for t in tags if re.fullmatch(r"v\d+(\.\d+)+", t)]
    if not stable:
        sys.exit("no release tags found; fetch them (git fetch --tags)")
    return stable[0]


def extract_release(tag: str, into: Path) -> Path:
    """The release's backend/ as a directory to run init_db from (git archive, read-only)."""
    target = into / tag
    if target.exists():
        return target
    archive = into / f"{tag}.tar"
    with archive.open("wb") as out:
        subprocess.run(["git", "archive", tag, "backend"], cwd=REPO, check=True, stdout=out)
    with tarfile.open(archive) as tar:
        tar.extractall(target, filter="data")
    return target


def run_init_db(code_root: Path, url: str, label: str) -> None:
    """Run init_db (create_all + migrations) of the code under code_root against url."""
    with tempfile.TemporaryDirectory() as data_dir:
        env = {**os.environ, "DATABASE_URL": url, "DATA_DIR": data_dir, "PYTHONPATH": str(code_root)}
        done = subprocess.run([sys.executable, "-c", _INIT_DB], cwd=data_dir, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"init_db failed ({label}):\n{done.stdout}{done.stderr}")


def with_database(url: str, name: str, driver: str = "") -> str:
    parts = urlsplit(url)
    scheme = "postgresql" + (f"+{driver}" if driver else "")
    return urlunsplit((scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def dump_schema(url: str) -> dict[str, str]:
    """Every column, constraint and index of the public schema, as {object: definition}."""
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        schema: dict[str, str] = {}
        for row in await conn.fetch(
            "SELECT table_name, column_name, data_type, udt_name, character_maximum_length, numeric_precision, "
            "numeric_scale, datetime_precision, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public'"
        ):
            table, column, data_type, udt, length, precision, scale, dt_precision, nullable, default = row
            # udt_name names what data_type leaves generic: the element type of an
            # ARRAY (_int4), the enum or domain of a USER-DEFINED column.
            attributes = [f"udt {udt}"]
            if length is not None:
                attributes.append(f"length {length}")
            if precision is not None:
                attributes.append(f"precision {precision}")
            if scale is not None:
                attributes.append(f"scale {scale}")
            if dt_precision is not None:
                attributes.append(f"time precision {dt_precision}")
            schema[f"column {table}.{column}"] = (
                f"{data_type} ({', '.join(attributes)})"
                + (" NULL" if nullable == "YES" else " NOT NULL")
                + f" default {default}"
            )
        # Counted: two identical constraints or indexes are drift too (an
        # unnamed constraint that every start adds again, say).
        objects: Counter[str] = Counter()
        for row in await conn.fetch(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace"
        ):
            objects[f"constraint {row[0]}: {row[1]}"] += 1
        for row in await conn.fetch("SELECT tablename, indexdef FROM pg_indexes WHERE schemaname = 'public'"):
            definition = re.sub(r"^CREATE (UNIQUE )?INDEX \S+ ON ", r"CREATE \1INDEX ON ", row[1])
            objects[f"index {row[0]}: {definition}"] += 1
        schema.update({key: f"{count}x" for key, count in objects.items()})
        return schema
    finally:
        await conn.close()


def drift(
    fresh: dict[str, str], upgraded: dict[str, str], labels: tuple[str, str] = ("fresh install", "upgraded")
) -> set[str]:
    """One line per object that differs between the two schemas: the object and how."""
    return {
        f"{key} | {labels[0]}: {fresh.get(key) or 'missing'}; {labels[1]}: {upgraded.get(key) or 'missing'}"
        for key in fresh.keys() | upgraded.keys()
        if fresh.get(key) != upgraded.get(key)
    }


def read_allowlist(path: Path) -> set[str]:
    """The allowlist's lines; '#' starts a comment only at the beginning of a line."""
    if not path.exists():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def compare(found: dict[str, set[str]], allowed: set[str]) -> list[str]:
    """Problems: drift (per start release) not in the allowlist, and allowlist lines no start shows."""
    problems = []
    for release, lines in sorted(found.items()):
        for line in sorted(lines - allowed):
            problems.append(f"upgraded from {release}: {line}")
    seen = set().union(*found.values()) if found else set()
    for line in sorted(allowed - seen):
        problems.append(f"no longer drifts, remove it from {ALLOWLIST.name}: {line}")
    return problems


def start_twice(url: str, label: str) -> tuple[dict[str, str], list[str]]:
    """Start the current code on url twice; the schema, and what the restart changed."""
    run_init_db(REPO, url, label)
    first = asyncio.run(dump_schema(with_database(url, urlsplit(url).path.lstrip("/"))))
    run_init_db(REPO, url, f"{label}, restarted")
    second = asyncio.run(dump_schema(with_database(url, urlsplit(url).path.lstrip("/"))))
    changed = drift(first, second, ("first start", "restart"))
    return second, [f"{label}: a restart changed the schema: {line}" for line in sorted(changed)]


def run_scenario(scenario: Scenario, url: str, releases_dir: Path) -> list[str]:
    """Build, seed and upgrade the scenario's database; its problems."""
    for release in scenario.chain:
        run_init_db(extract_release(release, releases_dir), url, f"{scenario.name}: {release} install")
    plain_url = with_database(url, urlsplit(url).path.lstrip("/"))
    asyncio.run(_admin(plain_url, scenario.seed))
    _, problems = start_twice(url, f"{scenario.name} (via {', '.join(scenario.chain)})")
    rows = asyncio.run(_fetch(plain_url, scenario.check))
    return problems + [f"{scenario.name}: {row[0]}" for row in rows]


async def _fetch(url: str, query: str) -> list:
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(query)
    finally:
        await conn.close()


async def _admin(url: str, *statements: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="PostgreSQL server URL (its database is only used to connect)")
    parser.add_argument(
        "--from-release", action="append", help=f"start release(s); default {FIRST_PG_RELEASE} and the newest stable"
    )
    args = parser.parse_args()
    releases = args.from_release or sorted({FIRST_PG_RELEASE, newest_stable_release()})
    admin_url = with_database(args.url, urlsplit(args.url).path.lstrip("/") or "postgres")

    databases = (
        {"fresh": "bambuddy_check_fresh"}
        | {r: "bambuddy_check_" + re.sub(r"\W", "_", r) for r in releases}
        | {s.name: f"bambuddy_check_scenario_{i}" for i, s in enumerate(SCENARIOS)}
    )
    asyncio.run(_admin(admin_url, *(f"DROP DATABASE IF EXISTS {db}" for db in databases.values())))
    asyncio.run(_admin(admin_url, *(f"CREATE DATABASE {db}" for db in databases.values())))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fresh, problems = start_twice(with_database(args.url, databases["fresh"], "asyncpg"), "fresh install")

            found = {}
            for release in releases:
                url = with_database(args.url, databases[release], "asyncpg")
                run_init_db(extract_release(release, Path(tmp)), url, f"{release} install")
                upgraded, changed = start_twice(url, f"upgrade from {release}")
                problems += changed
                found[release] = drift(fresh, upgraded)
                print(f"{release}: upgrade ok, {len(found[release])} object(s) differ from a fresh install")

            for scenario in SCENARIOS:
                url = with_database(args.url, databases[scenario.name], "asyncpg")
                scenario_problems = run_scenario(scenario, url, Path(tmp))
                problems += scenario_problems
                print(f"{scenario.name}: {'ok' if not scenario_problems else 'FAILED'}")
    finally:
        asyncio.run(_admin(admin_url, *(f"DROP DATABASE IF EXISTS {db}" for db in databases.values())))

    allowed = read_allowlist(ALLOWLIST)
    problems += compare(found, allowed)
    if problems:
        print("\nSchema problems:\n  " + "\n  ".join(problems))
        print(
            "\nA new column or constraint needs a migration in core/database.py that builds it exactly as the "
            "model does. See the docstring of scripts/check_postgres_upgrade.py."
        )
        return 1
    print("Upgraded schemas match a fresh install" + (" (apart from the listed known drift)." if allowed else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
