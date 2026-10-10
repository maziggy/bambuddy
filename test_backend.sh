#!/bin/sh

cd backend
ruff check && ruff format --check

# Type check against the pinned environment (see CONTRIBUTING.md). The venv is
# rebuilt whenever the lock changes, so it holds what CI installs (on Python
# 3.11 exactly; newer Pythons add a few unpinned backports).
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
echo "Skipping the type check: no .typecheck-venv (see CONTRIBUTING.md)."
fi

if [ "$1" = "--full" ]; then
../venv/bin/python3 -m pytest tests/ -v -n 30
else
../venv/bin/python3 -m pytest tests/ -v -n 30 --ignore=tests/unit/services/test_bambu_ftp.py
fi
