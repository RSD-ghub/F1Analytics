---
name: run-f1-dashboard
description: Run, start, launch, build, test, or smoke-test the F1 forecasting platform. Use when starting the services, the frontend, or verifying the app works end to end.
---

# Run — F1 forecasting platform

Four FastAPI services plus MongoDB and a React frontend.

**Only `core-api` is publicly reachable.** The other three have no published
host port in compose and are reached solely over the compose network — that is
what makes core-api the single place authentication is enforced. In dev mode
below they are exposed directly for convenience, which is *not* the production
topology.

| Service | Port | Database | Public |
|---|---|---|---|
| mongodb | 27017 | — | — |
| ingestion-service | 8001 | `f1_ingestion` | no |
| prediction-service | 8002 | `f1_prediction` | no |
| scoring-service | 8003 | `f1_scoring` | no |
| core-api | 8000 | `f1_core` | **yes** |
| frontend (Vite dev) | 5173 | — | yes |

Project root: `/Users/sreedeep/Desktop/Deepu Projects/F1-Dashboard`

---

## Fastest path: compose

```bash
docker compose up --build
```

Frontend on `:3000`, API on `:8000`. Requires `.env` with `JWT_SECRET` (and
`TINKER_API_KEY` if you want Bernie).

**Note:** Docker is not installed on this machine, so the compose path —
including the nginx reverse proxy — has never been executed here. Everything
below is the dev-mode path, which is what has actually been verified.

---

## Dev mode (verified)

Prerequisites: MongoDB on 27017, Python venv at `.venv`, `npm` available.

```bash
cd "/Users/sreedeep/Desktop/Deepu Projects/F1-Dashboard"
export $(grep -E '^(JWT_SECRET|TINKER_API_KEY|TINKER_BASE_URL|INKLING_MODEL)=' .env | sed 's/"//g' | xargs)

# Internal services. Each needs the others' URLs pointed at localhost, since
# compose hostnames (ingestion-service, etc.) do not resolve outside Docker.
.venv/bin/python -m uvicorn app.main:app --port 8001 --app-dir services/ingestion-service &
INGESTION_SERVICE_URL=http://localhost:8001 \
  .venv/bin/python -m uvicorn app.main:app --port 8002 --app-dir services/prediction-service &
MONGO_DATABASE=f1_scoring INGESTION_SERVICE_URL=http://localhost:8001 PREDICTION_SERVICE_URL=http://localhost:8002 \
  .venv/bin/python -m uvicorn app.main:app --port 8003 --app-dir services/scoring-service &
INGESTION_SERVICE_URL=http://localhost:8001 PREDICTION_SERVICE_URL=http://localhost:8002 SCORING_SERVICE_URL=http://localhost:8003 \
  .venv/bin/python -m uvicorn app.main:app --port 8000 --app-dir services/core-api &

cd frontend/f1-react && npm run dev
```

Verify: `bash .claude/skills/run-f1-dashboard/smoke.sh`

---

## Getting data in

The corpus lives in Mongo, not in files. Ingestion runs through the service's
own pipeline so it inherits the completeness guarantee.

```bash
# Classifications + qualifying (~2s/session). What the model trains on.
.venv/bin/python services/ingestion-service/scripts/backfill.py results 2010 2026

# Practice long-run pace (~7s/session, 2018+ only).
.venv/bin/python services/ingestion-service/scripts/backfill.py practice 2018 2026

# Forward calendar — needed before the scheduler can place anything.
curl -X POST "localhost:8001/forward/refresh-calendar?from_season=2026&to_season=2027"
```

Check completeness at the depth you care about — they are different questions:

```bash
curl "localhost:8001/ingest/status?from_season=2026&to_season=2026&depth=results"
```

---

## Training

```bash
cd services/prediction-service
../../.venv/bin/python scripts/train_model.py --from-api \
  --from-season 2010 --validation-seasons 2022,2023 --write
```

Reads the corpus through ingestion-service and **refuses to train if it has
gaps**. Weights land in `app/model_weights.json`; the service will not start
serving forecasts without it.

Retraining later goes through the champion/challenger gate:

```bash
../../.venv/bin/python scripts/retrain.py --apply
```

---

## Forecasts

Lock windows fire on their own once prediction-service is running (every
`LOCK_CHECK_MINUTES`, default 15). Manual locking exists for backtests:

```bash
curl -X POST localhost:8002/predictions/lock -H 'Content-Type: application/json' \
  -d '{"season":2026,"round":13,"window":"post_quali"}'
```

A second lock for the same window returns **409**. That is the immutability
guarantee, not a bug — a locked forecast is a historical fact.

After a race:

```bash
curl -X POST localhost:8003/reconcile/2026/13
curl localhost:8000/track-record
```

---

## Bernie

Needs `TINKER_API_KEY` with credit. Without it every Bernie path degrades
cleanly and the rest of the product is unaffected — verify with:

```bash
.venv/bin/python scripts/verify_inkling.py
```

A **402** means the key and endpoint are correct and the account needs credit.

---

## Tests

```bash
for s in ingestion-service prediction-service scoring-service core-api; do
  (cd services/$s && ../../.venv/bin/python -m pytest tests/ -q)
done
cd frontend/f1-react && npm run build && npx eslint src
```
