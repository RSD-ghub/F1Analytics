#!/usr/bin/env bash
# End-to-end smoke test for the F1 forecasting platform.
#
# Exercises the whole product path, not just liveness: ingest completeness ->
# locked forecast -> reconciliation -> scoring -> track record -> blog ->
# Bernie. Each check states what it proves, because a green tick that does not
# say what it verified is not evidence of anything.
#
# Requires the stack running (see SKILL.md). Read-only apart from creating one
# throwaway account and, optionally, locking a forecast for a past race.

set -uo pipefail

CORE=${CORE:-http://localhost:8000}
INGEST=${INGEST:-http://localhost:8001}
PREDICT=${PREDICT:-http://localhost:8002}
SCORE=${SCORE:-http://localhost:8003}
WEB=${WEB:-http://localhost:5174}

PASS=0; FAIL=0; SKIP=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
skip() { printf "  \033[33m–\033[0m %s\n" "$1"; SKIP=$((SKIP+1)); }
head() { printf "\n\033[1m%s\033[0m\n" "$1"; }

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

head "Services"
for pair in "core-api:$CORE" "ingestion:$INGEST" "prediction:$PREDICT" "scoring:$SCORE"; do
  name=${pair%%:*}; url=${pair#*:}
  status=$(curl -sf "$url/health" | json "d['status']")
  [ "$status" = "ok" ] && ok "$name healthy" || bad "$name not responding at $url"
done

# core-api is the only publicly reachable service; the rest are internal in
# compose. Reaching them directly here is a dev-mode convenience, not the
# production topology.
REACHABLE=$(curl -sf "$CORE/status" | json "sum(1 for s in d['services'] if s['reachable'])")
[ "${REACHABLE:-0}" = "3" ] \
  && ok "core-api can reach all three internal services" \
  || bad "core-api sees only ${REACHABLE:-0}/3 internal services"

head "Data completeness"
# The guarantee: 'complete' is answered at the depth actually consumed.
COMPLETE=$(curl -sf "$INGEST/ingest/status?from_season=2024&to_season=2024&depth=results" | json "d['is_complete']")
[ "$COMPLETE" = "True" ] \
  && ok "2024 is complete at results depth (what the model consumes)" \
  || bad "2024 has gaps at results depth — training and forecasts will be degraded"

GAPS=$(curl -sf "$INGEST/ingest/status?from_season=2024&to_season=2024&depth=full" | json "len(d['summary']['open_gaps'])")
[ -n "$GAPS" ] \
  && ok "full depth reports separately ($GAPS gaps) — depth is not conflated" \
  || bad "full-depth completeness did not answer"

head "Model"
VERSION=$(curl -sf "$PREDICT/predictions/model/versions" | json "d[0]['version'] if d else ''")
[ -n "$VERSION" ] && ok "model registered: $VERSION" || skip "no model version recorded yet"

head "Forecast → score → record"
LOCKED=$(curl -sf "$PREDICT/predictions?limit=100" | json "len(d)")
if [ "${LOCKED:-0}" -gt 0 ]; then
  ok "$LOCKED forecast(s) locked"

  # Immutability is the load-bearing guarantee: a second lock must be refused.
  FIRST=$(curl -sf "$PREDICT/predictions?limit=1" | json "'%s %s %s' % (d[0]['season'], d[0]['round'], d[0]['window'])")
  set -- $FIRST
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$PREDICT/predictions/lock" \
    -H 'Content-Type: application/json' \
    -d "{\"season\":$1,\"round\":$2,\"window\":\"$3\"}")
  [ "$CODE" = "409" ] \
    && ok "re-locking $1 R$2 $3 is refused (409) — predictions are immutable" \
    || bad "re-lock returned $CODE, expected 409 — the record is editable"

  # A pre-quali forecast must make no win claim.
  PRE=$(curl -sf "$PREDICT/predictions?limit=100" | json "next((p for p in d if p['window']=='pre_quali'), None) is not None")
  if [ "$PRE" = "True" ]; then
    WINCLAIM=$(curl -sf "$PREDICT/predictions?limit=100" | json "any(r['p_win'] is not None for p in d if p['window']=='pre_quali' for r in p['driver_probabilities'])")
    [ "$WINCLAIM" = "False" ] \
      && ok "pre-quali forecasts publish no win probability" \
      || bad "a pre-quali forecast carries a win probability it should not claim"
  else
    skip "no pre-quali forecast to check the market policy against"
  fi
else
  skip "no forecasts locked — lock one or wait for the scheduler"
fi

RECORD=$(curl -sf "$CORE/track-record" | json "d['record'] is not None")
if [ "$RECORD" = "True" ]; then
  SCORED=$(curl -sf "$CORE/track-record" | json "d['record']['predictions_scored']")
  PENDING=$(curl -sf "$CORE/track-record" | json "d['record']['predictions_pending']")
  ok "track record served: $SCORED scored, $PENDING awaiting reconciliation"
  # Pending must be reported: a record showing only scored predictions could be
  # improved by never reconciling the bad ones.
  [ -n "$PENDING" ] && ok "pending forecasts are counted, not hidden" || bad "pending count missing"
else
  skip "nothing scored yet — reconcile a finished race first"
fi

head "One Blog"
ENTRIES=$(curl -sf "$CORE/blog/2024/1?narrate=false" | json "len(d['entries'])")
if [ "${ENTRIES:-0}" -gt 0 ]; then
  ok "weekend blog renders $ENTRIES entries"
  SOURCED=$(curl -sf "$CORE/blog/2024/1?narrate=false" | json "all(e['sources'] for e in d['entries'])")
  [ "$SOURCED" = "True" ] \
    && ok "every entry cites its sources — no unsourced claims published" \
    || bad "an entry was published without provenance"
else
  skip "no blog entries for 2024 R1 — ingest that weekend first"
fi

head "Auth"
EMAIL="smoke-$(date +%s)@example.com"
TOKEN=$(curl -sf -X POST "$CORE/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"a suitably long password\"}" | json "d['access_token']")
[ -n "$TOKEN" ] && ok "registration issues a token" || bad "registration failed"

ME=$(curl -sf "$CORE/auth/me" -H "Authorization: Bearer $TOKEN" | json "d['email']")
[ "$ME" = "$EMAIL" ] && ok "token authenticates" || bad "token rejected"

# The two failures must be indistinguishable, or login becomes an
# account-enumeration oracle.
WRONG=$(curl -s -X POST "$CORE/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"wrong password here\"}" | json "d['detail']")
GHOST=$(curl -s -X POST "$CORE/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"nobody-here@example.com","password":"wrong password here"}' | json "d['detail']")
[ "$WRONG" = "$GHOST" ] && [ -n "$WRONG" ] \
  && ok "wrong password and unknown account are indistinguishable" \
  || bad "login leaks whether an account exists"

UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" "$CORE/auth/me")
[ "$UNAUTH" = "401" ] && ok "unauthenticated access refused" || bad "/auth/me returned $UNAUTH without a token"

head "Bernie"
BERNIE=$(curl -s -X POST "$CORE/bernie/threads" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"season":2024,"round":1,"question":"Who was quickest in practice?"}')
ANSWER=$(echo "$BERNIE" | json "d.get('answer','')")
REASON=$(echo "$BERNIE" | json "(d.get('detail') or {}).get('reason','')")
if [ -n "$ANSWER" ]; then
  ok "Bernie answered ($(echo "$ANSWER" | wc -c | tr -d ' ') chars)"
  # The reasoning trace must never reach a reader.
  case "$ANSWER" in
    *"</think>"*|*"assistantfinal"*) bad "a reasoning trace leaked into Bernie's answer" ;;
    *) ok "no reasoning trace in the answer" ;;
  esac
elif [ -n "$REASON" ]; then
  skip "Bernie unavailable (reason: $REASON) — the rest of the product is unaffected"
else
  bad "Bernie returned neither an answer nor a reason"
fi

head "Frontend"
for route in / /track-record /championship /weekend/2024/1 /bernie; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$WEB$route")
  [ "$CODE" = "200" ] && ok "$route serves" || bad "$route returned $CODE"
done
PROXY=$(curl -s -o /dev/null -w "%{http_code}" "$WEB/api/next-race")
[ "$PROXY" = "200" ] && ok "/api proxy reaches core-api" || bad "/api proxy returned $PROXY"

printf "\n\033[1m%d passed, %d failed, %d skipped\033[0m\n" "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
