#!/usr/bin/env bash
# Weekly model maintenance: retrain behind the promotion gate, refit the pace
# network, then run the end-to-end smoke test. Meant for cron or launchd on the
# machine that runs the services (the Mac today), e.g. Tuesdays 06:00:
#
#   0 6 * * 2  cd ~/Desktop/Deepu\ Projects/F1-Dashboard && scripts/weekly_retrain.sh >> ~/f1-retrain.log 2>&1
#
# Report-only by default: it prints the gate's decision and changes nothing.
# Pass --apply to let it promote a challenger that clears the gate and write
# the refitted pace network; restart prediction-service afterwards so the
# running process loads the new weights.
#
# Exit status is non-zero if any step failed, so cron mail (or a wrapper that
# posts to ALERT_WEBHOOK_URL) catches it.
set -uo pipefail

APPLY=""
[ "${1:-}" = "--apply" ] && APPLY="--apply"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
INGEST="${INGEST:-http://localhost:8001}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/f1-retrain-XXXX")"
FIRST_SEASON="${FIRST_SEASON:-2010}"
status=0

step() { printf '\n== %s  %s\n' "$(date -u +%FT%TZ)" "$*"; }

step "export results from $INGEST"
if ! "$PY" - "$INGEST" "$FIRST_SEASON" "$WORK/results.jsonl" <<'PY'
import json, sys, urllib.request
base, first, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
seasons = json.load(urllib.request.urlopen(f"{base}/data/seasons", timeout=30))
rows = 0
with open(out, "w") as fh:
    for season in sorted(s for s in seasons if s >= first):
        url = f"{base}/data/results?season={season}&limit=5000"
        for row in json.load(urllib.request.urlopen(url, timeout=60)):
            fh.write(json.dumps(row) + "\n"); rows += 1
print(f"{rows} result rows, seasons {first}+")
if rows == 0:
    sys.exit("no results exported")
PY
then
  echo "export failed; is ingestion-service running at $INGEST?"; exit 1
fi

step "retrain behind the promotion gate ${APPLY:-(report only)}"
(cd "$ROOT/services/prediction-service" && "$PY" scripts/retrain.py "$WORK/results.jsonl" $APPLY) || status=1

step "refit the pace network ${APPLY:+(writing)}"
PACE_ARGS=(--api-url "$INGEST")
[ -n "$APPLY" ] && PACE_ARGS+=(--write)
(cd "$ROOT/services/prediction-service" && "$PY" scripts/evaluate_pace_network.py "${PACE_ARGS[@]}") || status=1

step "end-to-end smoke test"
bash "$ROOT/.claude/skills/run-f1-dashboard/smoke.sh" || status=1

[ -n "$APPLY" ] && echo "Restart prediction-service to load any promoted weights."
step "done (status $status)"
exit $status
