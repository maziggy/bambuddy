#!/bin/sh

cd backend
ruff check && ruff format --check
../venv/bin/python3 -m pytest tests/ -v -n 14
cd ..
