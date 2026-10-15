#!/usr/bin/env bash
# Starts the simulation API (internal, port 8000) and the website (public, port 7860).
# If either process stops, the container exits so that the host restarts it.
set -u
mkdir -p "${RAICING_DATA_DIR:-/tmp/raicing-data}" "${NUMBA_CACHE_DIR:-/tmp/numba-cache}"
python -m raicing.api --host 127.0.0.1 --port 8000 &
cd web
PORT="${PORT:-7860}" HOSTNAME=0.0.0.0 RAICING_API_URL=http://127.0.0.1:8000 node server.js &
wait -n
exit 1
