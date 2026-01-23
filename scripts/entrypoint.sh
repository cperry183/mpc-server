#!/usr/bin/env sh
set -eu

echo "Starting MPC coordinator..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

