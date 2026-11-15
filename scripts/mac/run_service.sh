#!/usr/bin/env bash
# Run one part of the platform in the foreground, the way the Mac runs it.
#
#   scripts/mac/run_service.sh ingestion|prediction|scoring|core-api|frontend
#
# This is what the launchd agents call (see install_launchd.sh), and it works
# the same from a Terminal. It loads the repo's .env, points each service at
# the others on localhost (compose hostnames do not resolve outside Docker),
# and execs the process so launchd sees it directly and can restart it.
#
# Python: $PYTHON if set, else .venv312 (the 3.12 setup that F1TV needs), else
# .venv. Node: $NPM if set, else npm on PATH.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -z "${PYTHON:-}" ]; then
  if [ -x .venv312/bin/python ]; then PYTHON=.venv312/bin/python; else PYTHON=.venv/bin/python; fi
fi

export INGESTION_SERVICE_URL="${INGESTION_SERVICE_URL:-http://localhost:8001}"
export PREDICTION_SERVICE_URL="${PREDICTION_SERVICE_URL:-http://localhost:8002}"
export SCORING_SERVICE_URL="${SCORING_SERVICE_URL:-http://localhost:8003}"

uvicorn() {  # name port
  exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$2" --app-dir "services/$1"
}

case "${1:-}" in
  ingestion)  uvicorn ingestion-service 8001 ;;
  prediction) uvicorn prediction-service 8002 ;;
  scoring)    MONGO_DATABASE=f1_scoring uvicorn scoring-service 8003 ;;
  core-api)   uvicorn core-api 8000 ;;
  frontend)
    cd frontend/f1-react
    exec "${NPM:-npm}" run dev
    ;;
  *)
    echo "usage: $0 ingestion|prediction|scoring|core-api|frontend" >&2
    exit 64
    ;;
esac
