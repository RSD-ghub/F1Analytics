#!/usr/bin/env bash
# F1 Dashboard — smoke test driver
# Run from the project root: bash .claude/skills/run-f1-dashboard/smoke.sh
# Requires: MongoDB on 27017, backend on 8082, Vite on 5173 (see SKILL.md to start them).
set -euo pipefail

PASS=0; FAIL=0
ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }
hdr()  { echo ""; echo "── $1 ──"; }

# ── 1. Backend liveness ──────────────────────────────────────────────────────
hdr "Backend liveness"
SEASONS=$(curl -sf http://localhost:8082/f1/seasons 2>/dev/null)
if echo "$SEASONS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d)>0" 2>/dev/null; then
  ok "/f1/seasons returns season list"
else
  fail "/f1/seasons — backend not responding"; FAIL=$((FAIL+1))
  echo "  Start backend first. See SKILL.md."
  exit 1
fi

# ── 2. Frontend liveness ─────────────────────────────────────────────────────
hdr "Frontend liveness"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/ 2>/dev/null)
[ "$HTTP" = "200" ] && ok "Vite dev server at :5173" || fail "Vite not responding (HTTP $HTTP)"

# ── 3. Auth — register ───────────────────────────────────────────────────────
hdr "Auth: register"
SMOKE_USER="smoketest_$$"
REG=$(curl -sf -X POST http://localhost:8082/f1/auth/register \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$SMOKE_USER\",\"password\":\"smokepass1\"}" 2>/dev/null)
echo "$REG" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'message' in d" 2>/dev/null \
  && ok "register → 201 with message" || fail "register failed: $REG"

# ── 4. Auth — duplicate username → 409 ──────────────────────────────────────
hdr "Auth: duplicate username"
HTTP_DUP=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8082/f1/auth/register \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$SMOKE_USER\",\"password\":\"smokepass1\"}" 2>/dev/null)
[ "$HTTP_DUP" = "409" ] && ok "duplicate username → 409" || fail "expected 409, got $HTTP_DUP"

# ── 5. Auth — login → JWT token ──────────────────────────────────────────────
hdr "Auth: login"
LOGIN=$(curl -sf -X POST http://localhost:8082/f1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$SMOKE_USER\",\"password\":\"smokepass1\"}" 2>/dev/null)
TOKEN=$(echo "$LOGIN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['token'])" 2>/dev/null)
[ -n "$TOKEN" ] && ok "login → JWT token (${TOKEN:0:20}…)" || fail "login failed: $LOGIN"

# ── 6. Auth — wrong password → 401 ───────────────────────────────────────────
hdr "Auth: wrong password"
HTTP_BAD=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8082/f1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$SMOKE_USER\",\"password\":\"wrongpass\"}" 2>/dev/null)
[ "$HTTP_BAD" = "401" ] && ok "wrong password → 401" || fail "expected 401, got $HTTP_BAD"

# ── 7. Auth guard — AI endpoint without token → 401 ─────────────────────────
hdr "Auth guard"
HTTP_UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:8082/f1/ai/season-review?season=2024" 2>/dev/null)
[ "$HTTP_UNAUTH" = "401" ] \
  && ok "AI endpoint without token → 401" \
  || fail "expected 401, got $HTTP_UNAUTH (auth guard may not be active)"

# ── 8. Prediction endpoint — auth guard ──────────────────────────────────────
hdr "Prediction auth guard"
HTTP_PRED_UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:8082/f1/predict/race?season=2024&round=1" 2>/dev/null)
[ "$HTTP_PRED_UNAUTH" = "401" ] \
  && ok "predict endpoint without token → 401" \
  || fail "expected 401, got $HTTP_PRED_UNAUTH"

# ── 9. Data endpoints ────────────────────────────────────────────────────────
hdr "Data endpoints"
curl -sf "http://localhost:8082/f1/analytics/2024" -o /dev/null 2>/dev/null \
  && ok "/f1/analytics/2024 responds" || fail "/f1/analytics/2024 failed"

curl -sf "http://localhost:8082/f1/races/2024" -o /dev/null 2>/dev/null \
  && ok "/f1/races/2024 responds" || fail "/f1/races/2024 failed"

# ── 10. Vite proxy ───────────────────────────────────────────────────────────
hdr "Vite proxy"
PROXY=$(curl -sf http://localhost:5173/f1/seasons 2>/dev/null)
echo "$PROXY" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d)>0" 2>/dev/null \
  && ok "Vite proxies /f1/* to backend" || fail "Vite proxy not working"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────"
echo "  Passed: $PASS   Failed: $FAIL"
echo "────────────────────────────────"
[ $FAIL -eq 0 ]
