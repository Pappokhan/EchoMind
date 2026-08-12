#!/usr/bin/env sh
# EchoMind container entrypoint.
#
# Runs gunicorn instead of `python app.py` — the Flask dev server used by
# app.py's __main__ block is single-threaded and not meant to serve real
# traffic (its own docs say so). gunicorn gives us multiple worker
# processes, proper request handling, and graceful worker recycling.
set -e

WORKERS="${WEB_CONCURRENCY:-${WORKERS:-2}}"
TIMEOUT="${GUNICORN_TIMEOUT:-60}"
PORT="${PORT:-5000}"

mkdir -p "${DATA_DIR:-/app/data}" "${LOG_DIR:-/app/logs}"

exec gunicorn \
  --bind "0.0.0.0:${PORT}" \
  --workers "${WORKERS}" \
  --timeout "${TIMEOUT}" \
  --access-logfile - \
  --error-logfile - \
  app:app
