#!/usr/bin/env bash
# Run a command; if it fails, push its last lines to ALERT_WEBHOOK_URL.
#
#   scripts/mac/notify_on_failure.sh "Weekly backup" scripts/backup_mongo.sh
#
# Scheduled jobs (backups, the weekly retrain report) run with nobody
# watching, so a failure has to reach a person. Uses the same ntfy or webhook
# address as scoring-service's watchdog, read from the repo's .env.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ -f .env ]; then set -a; . ./.env; set +a; fi

name="$1"; shift
log="$(mktemp "${TMPDIR:-/tmp}/f1-job-XXXX")"
"$@" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}

if [ "$status" -ne 0 ] && [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
  body="$name failed (exit $status) on $(hostname -s). Last lines:
$(tail -n 15 "$log")"
  if [ "${ALERT_WEBHOOK_FORMAT:-text}" = "json" ]; then
    python3 -c 'import json,sys; t=sys.stdin.read(); print(json.dumps({"text": t, "content": t}))' <<<"$body" |
      curl -fsS -m 20 -H "Content-Type: application/json" --data-binary @- "$ALERT_WEBHOOK_URL" >/dev/null || true
  else
    curl -fsS -m 20 -H "Title: $name failed" --data-binary "$body" "$ALERT_WEBHOOK_URL" >/dev/null || true
  fi
fi
rm -f "$log"
exit "$status"
