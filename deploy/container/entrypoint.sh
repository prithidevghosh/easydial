#!/bin/sh
set -eu
umask 077
mkdir -p /data/customers
Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp &
xvfb_pid=$!
trap 'kill "$xvfb_pid" 2>/dev/null || true' EXIT
# Uvicorn must listen on the container interface. It still has exactly one worker.
uvicorn easydial.whatsapp.app:app --host 0.0.0.0 --port 8001 --workers 1 --no-access-log &
server_pid=$!
trap 'kill -TERM "$server_pid" "$xvfb_pid" 2>/dev/null || true; wait "$server_pid" || true' TERM INT
wait "$server_pid"
