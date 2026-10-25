#!/bin/sh

cd backend
{ ruff check && ruff format --check; } || exit 1

# Type check against the pinned environment (see CONTRIBUTING.md). The venv is
# rebuilt whenever the lock changes, so it holds what CI installs (on Python
# 3.11 exactly; newer Pythons add a few unpinned backports). Created here on
# first use when Python 3.11 is available (through uv, or as python3.11).
if [ ! -d ../.typecheck-venv ]; then
    if command -v uv >/dev/null 2>&1; then
        echo "Creating .typecheck-venv (Python 3.11, uv)"
        uv venv -q --seed --python 3.11 ../.typecheck-venv || exit 1
    elif command -v python3.11 >/dev/null 2>&1; then
        echo "Creating .typecheck-venv (python3.11)"
        python3.11 -m venv ../.typecheck-venv || exit 1
    fi
fi
if [ -d ../.typecheck-venv ]; then
(
    cd .. || exit 1
    lock_hash=$( (sha256sum requirements-typecheck.lock 2>/dev/null || shasum -a 256 requirements-typecheck.lock) | cut -d' ' -f1)
    if [ -z "$lock_hash" ]; then
        echo "Type check: neither sha256sum nor shasum is available."
        exit 1
    fi
    if ! .typecheck-venv/bin/python -c '' 2>/dev/null; then
        echo "Type check: the python in .typecheck-venv no longer runs (interpreter removed or upgraded); delete .typecheck-venv and recreate it (see CONTRIBUTING.md)."
        exit 1
    fi
    if [ ! -x .typecheck-venv/bin/basedpyright ] || [ "$(cat .typecheck-venv/.lock-hash 2>/dev/null)" != "$lock_hash" ]; then
        echo "Rebuilding .typecheck-venv from requirements-typecheck.lock"
        # Rebuild from the interpreter the venv was created from (pyvenv.cfg
        # "home"), not a fully resolved path, so a stable link stays stable.
        py="$(sed -n 's/^home *= *//p' .typecheck-venv/pyvenv.cfg)/python3"
        "$py" -m venv --clear .typecheck-venv || exit 1
        .typecheck-venv/bin/pip install -q -r requirements-typecheck.lock || exit 1
        echo "$lock_hash" > .typecheck-venv/.lock-hash
    fi
    if ! out=$(.typecheck-venv/bin/pip install --dry-run -r requirements.txt 2>&1); then
        echo "$out"
        echo "pip could not resolve requirements.txt against .typecheck-venv."
        exit 1
    fi
    if echo "$out" | grep '^Would install'; then
        echo "requirements-typecheck.lock is missing packages from requirements.txt; regenerate it (see requirements-typecheck.txt)."
        exit 1
    fi
    .typecheck-venv/bin/basedpyright --pythonpath .typecheck-venv/bin/python
) || exit 1
else
echo "Skipping the type check: no .typecheck-venv and no Python 3.11 to create it (see CONTRIBUTING.md)."
fi

# PostgreSQL upgrade check, as CI runs it, in a throwaway container. Needs
# Docker and the release tags.
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
echo "Skipping the PostgreSQL upgrade check: Docker is not available."
elif [ -z "$(git tag --list v0.2.3)" ]; then
echo "Skipping the PostgreSQL upgrade check: no release tags in this clone (git fetch --tags)."
else
(
    cd .. || exit 1
    name="bambuddy-pg-check-$$"
    port=$(venv/bin/python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])') || exit 1
    docker run -d --rm --name "$name" --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=check \
        -p "127.0.0.1:$port:5432" postgres:16-alpine >/dev/null || exit 1
    # dash runs no EXIT trap when interrupted, so remove the container on a
    # signal too (and then exit as interrupted).
    trap 'docker rm -f "$name" >/dev/null 2>&1' EXIT
    trap 'docker rm -f "$name" >/dev/null 2>&1; exit 130' INT TERM
    ready=0
    for _ in $(seq 60); do
        if docker exec "$name" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1; then ready=1; break; fi
        sleep 1
    done
    if [ "$ready" != 1 ]; then
        echo "PostgreSQL upgrade check: the container did not become ready."
        exit 1
    fi
    venv/bin/python scripts/check_postgres_upgrade.py --url "postgresql://postgres:check@127.0.0.1:$port/postgres"
) || exit 1
fi

if [ "$1" = "--full" ]; then
../venv/bin/python3 -m pytest tests/ -v -n 30 || exit 1
else
../venv/bin/python3 -m pytest tests/ -v -n 30 --ignore=tests/unit/services/test_bambu_ftp.py || exit 1
fi

# The same tests on PostgreSQL, in a throwaway container (--full only: about
# three minutes). Tests of SQLite-only behaviour are skipped there.
if [ "$1" != "--full" ]; then
echo "Skipping the tests on PostgreSQL (run with --full)."
elif ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
echo "Skipping the tests on PostgreSQL: Docker is not available."
else
(
    name="bambuddy-pg-tests-$$"
    port=$(../venv/bin/python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])') || exit 1
    # One database pair per test worker, each with its own connection pool.
    docker run -d --rm --name "$name" --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=tests \
        -p "127.0.0.1:$port:5432" postgres:16-alpine \
        -c max_connections=2000 -c fsync=off -c synchronous_commit=off -c full_page_writes=off >/dev/null || exit 1
    trap 'docker rm -f "$name" >/dev/null 2>&1' EXIT
    trap 'docker rm -f "$name" >/dev/null 2>&1; exit 130' INT TERM
    ready=0
    for _ in $(seq 60); do
        if docker exec "$name" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1; then ready=1; break; fi
        sleep 1
    done
    if [ "$ready" != 1 ]; then
        echo "Tests on PostgreSQL: the container did not become ready."
        exit 1
    fi
    TEST_POSTGRES_URL="postgresql+asyncpg://postgres:tests@127.0.0.1:$port/postgres" \
        ../venv/bin/python3 -m pytest tests/ -v -n 30
) || exit 1
fi
