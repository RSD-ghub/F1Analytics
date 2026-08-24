---
name: run-f1-dashboard
description: Run, start, launch, build, test, or smoke-test the F1 Analytics Dashboard. Use when starting the backend, frontend, or verifying the app works.
---

# Run — F1 Analytics Dashboard

Three services: **MongoDB** (27017), **Java/Helidon backend** (8082), **Vite dev server** (5173).
Drive it with `smoke.sh` (curl-based) at `.claude/skills/run-f1-dashboard/smoke.sh`.

All paths are relative to the project root `/Users/sreedeep/Desktop/Deepu Projects/F1-Dashboard`.

---

## Prerequisites

```bash
# Java 21
JAVA_HOME=/Users/sreedeep/java/jdk-21.0.11+10/Contents/Home
# Maven 3.9.6
MVN=/Users/sreedeep/java/apache-maven-3.9.6/bin/mvn
# Node 20 via nvm (npm is NOT in default PATH — must source nvm first)
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
# MongoDB data directory
MONGO_DATA=~/java/mongodb-data
```

`.env` at project root must contain:
```
ANTHROPIC_API_KEY=sk-ant-...
JWT_SECRET=<32+ random chars>
```

---

## Build

```bash
JAVA_HOME=/Users/sreedeep/java/jdk-21.0.11+10/Contents/Home \
  /Users/sreedeep/java/apache-maven-3.9.6/bin/mvn package -DskipTests -q
# Produces: target/prompts.jar + target/libs/
```

Frontend build (only needed for embedded jar deploy; dev uses Vite directly):
```bash
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd frontend/f1-react && npm run build -- --outDir dist --emptyOutDir
```

---

## Start all services

### 1. MongoDB
```bash
mongod --dbpath ~/java/mongodb-data --fork --logpath /tmp/mongod.log
# Already running? Check: lsof -i :27017 | grep LISTEN
```

### 2. Java backend
```bash
# Load env vars (ANTHROPIC_API_KEY + JWT_SECRET), then start
set -a && source .env && set +a
JAVA_HOME=/Users/sreedeep/java/jdk-21.0.11+10/Contents/Home
nohup "$JAVA_HOME/bin/java" -jar target/prompts.jar > /tmp/f1-backend.log 2>&1 &
# Takes ~8 seconds to start. Verify:
curl -s http://localhost:8082/f1/seasons   # returns JSON array of years
```

### 3. Vite dev server
```bash
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd frontend/f1-react
nohup npm run dev > /tmp/f1-vite.log 2>&1 &
# Verify: curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/  → 200
```

App is at **http://localhost:5173**

---

## Run (agent path) — smoke.sh

Runs 11 curl checks covering liveness, auth, auth guard, data endpoints, and Vite proxy.
Requires all three services already running.

```bash
cd /Users/sreedeep/Desktop/Deepu\ Projects/F1-Dashboard
bash .claude/skills/run-f1-dashboard/smoke.sh
```

Expected output: `Passed: 11   Failed: 0`

The script creates and uses a temporary user (`smoketest_<PID>`) for auth checks — it does not pollute existing user data.

---

## Key endpoints (verified)

| Endpoint | Method | Auth | Notes |
|---|---|---|---|
| `/f1/seasons` | GET | No | Returns `[2026, 2025, 2024, ...]` |
| `/f1/analytics/{year}` | GET | No | Season analytics |
| `/f1/races/{year}` | GET | No | Race list for season |
| `/f1/auth/register` | POST | No | Body: `{"username","password"}` → 201 |
| `/f1/auth/login` | POST | No | Body: `{"username","password"}` → `{"token","username"}` |
| `/f1/ai/season-review?season=` | POST | Bearer JWT | Streams markdown |
| `/f1/ai/race-rewind?season=&round=` | POST | Bearer JWT | Streams markdown |
| `/f1/predict/race?season=&round=` | GET | Bearer JWT | ML prediction JSON |
| `/f1/predict/race/refresh?season=&round=` | POST | Bearer JWT | Evicts cache + recomputes |

**Health check gotcha:** `GET /health` returns HTML (SPA fallback). Use `GET /f1/seasons` to verify the backend is alive.

---

## Gotchas

**npm not in PATH** — Node is installed via nvm. `npm: command not found` means you forgot to source nvm first. Always run `export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"` before any npm command.

**Backend needs JAVA_HOME explicit** — `java` is not in default PATH. Always set `JAVA_HOME=/Users/sreedeep/java/jdk-21.0.11+10/Contents/Home` and use `$JAVA_HOME/bin/java`.

**Maven same issue** — `mvn` is not in PATH. Use `/Users/sreedeep/java/apache-maven-3.9.6/bin/mvn` directly.

**JWT_SECRET must be 32+ chars** — The backend throws `IllegalStateException` on startup if `JWT_SECRET` env var is missing or under 32 characters. Source `.env` before starting.

**Vite EPERM on index.html** — If Vite was started by a different process or session, it may lose read access. `pkill -f vite` then restart using the command above.

**`((var++))` with `set -e`** — Arithmetic expressions that evaluate to 0 cause bash to exit under `set -e`. Use `var=$((var+1))` instead.

**Old jar still running** — After `mvn package`, you must kill the old backend process before starting the new one. Check: `lsof -i :8082 | grep LISTEN`, then `pkill -f "prompts.jar"`.

**Vite build outDir** — `vite.config.js` sets `outDir` to the Java classpath (`../../src/main/resources/web/f1`). For Docker builds override it: `npm run build -- --outDir dist --emptyOutDir`.

---

## Troubleshooting

**`curl: (7) Failed to connect to localhost port 8082`**
→ Backend not started or still starting (takes ~8s). Check: `tail -20 /tmp/f1-backend.log`

**`HTTP 401` on AI/predict endpoints with a valid token**
→ JWT_SECRET env var wasn't set when the backend started. Restart with `source .env` first.

**`HTTP 409` on register**
→ Username already taken. The smoke script uses a unique PID-based username; this shouldn't happen unless two smoke runs collide.

**Backend starts but returns wrong data**
→ You may be running the old jar. `ls -lh target/prompts.jar` — check the timestamp matches your last `mvn package`.
