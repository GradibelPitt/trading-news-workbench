#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade -r backend/requirements.txt
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
