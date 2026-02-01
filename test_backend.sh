#!/bin/sh

cd backend
../venv/bin/python3 -m pytest tests/ -v -n 14
cd ..
