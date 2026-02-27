#!/bin/sh

cd backend
ruff check && ruff format --check

#if [ "$1" = "--full" ]; then
../venv/bin/python3 -m pytest tests/ -v -n 30
#else
#../venv/bin/python3 -m pytest tests/ -v -n 30 --ignore=tests/unit/services/test_bambu_ftp.py
#fi
#cd ..
