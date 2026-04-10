#!/bin/sh

cd backend
ruff check && ruff format --check

# Resolve the correct python binary (Windows uses Scripts/, Unix uses bin/)
if [ -f "../venv/Scripts/python" ]; then
  PYTHON="../venv/Scripts/python"
elif [ -f "../venv/bin/python3" ]; then
  PYTHON="../venv/bin/python3"
else
  echo "ERROR: no python found in venv (tried Scripts/python and bin/python3)" >&2
  exit 1
fi

#if [ "$1" = "--full" ]; then
$PYTHON -m pytest tests/ -v -n 30
#else
#$PYTHON -m pytest tests/ -v -n 30 --ignore=tests/unit/services/test_bambu_ftp.py
#fi
#cd ..
