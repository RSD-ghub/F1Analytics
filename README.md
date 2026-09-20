# F1 Forecasting Platform

Calibrated probabilities for Formula 1 race outcomes, published with the track
record that says how good they have been.

The product commitment is narrower than "predicts races": every forecast is
**locked before the event it describes**, scored against the result afterwards,
and shown beside its own history. A forecast that cannot be checked is not
published, and a market the model cannot beat a base rate on is not published
either — the pre-qualifying window deliberately carries no win probability,
because on two held-out seasons its win-market skill was indistinguishable from
guessing.

Three surfaces sit on top of that: **forecasts** for the next race, the **track
record** of everything forecast so far, and **Bernie**, an AI pit-wall
strategist who may rephrase the facts he is given and may never introduce one.

> **Note:** this replaced a Helidon/Java + Oracle JET application in August 2026.
> Nothing of that stack remains. If you find instructions mentioning `mvn`,
> GraalVM or `/f1/ingest`, they are stale.

---

## Architecture

Four FastAPI services over MongoDB, with a React frontend.

| Service | Port | Database | Public | Responsibility |
|---|---|---|---|---|
| mongodb | 27017 | — | no | — |
| ingestion-service | 8001 | `f1_ingestion` | no | FastF1 + FIA acquisition, completeness, the regulations corpus |
| prediction-service | 8002 | `f1_prediction` | no | Feature snapshots, training, locked forecasts |
| scoring-service | 8003 | `f1_scoring` | no | Reconciliation and Brier scoring against baselines |
| core-api | 8000 | `f1_core` | **yes** | Auth, dashboard aggregation, Bernie, One Blog |
| frontend (Vite) | 5173 | — | yes | React 18, React Router, Recharts |

**Only `core-api` is publicly reachable.** The other three have no published
host port in production compose and are reached solely over the compose network.
That is what makes core-api the single place authentication is enforced — if an
internal service ever starts publishing a host port, that property is quietly
gone. `compose-preflight.sh` checks for exactly that.

Shared code lives in `shared/f1_common` (settings, Mongo, health, LLM client)
and is installed into each service as an editable dependency.

---

## Running it

Prerequisites: MongoDB on 27017, a Python venv at `.venv`, `npm`.

```bash
export $(grep -E '^(JWT_SECRET|TINKER_API_KEY|TINKER_BASE_URL|INKLING_MODEL)=' .env | sed 's/"//g' | xargs)

.venv/bin/python -m uvicorn app.main:app --port 8001 --app-dir services/ingestion-service &
INGESTION_SERVICE_URL=http://localhost:8001 \
  .venv/bin/python -m uvicorn app.main:app --port 8002 --app-dir services/prediction-service &
MONGO_DATABASE=f1_scoring INGESTION_SERVICE_URL=http://localhost:8001 PREDICTION_SERVICE_URL=http://localhost:8002 \
  .venv/bin/python -m uvicorn app.main:app --port 8003 --app-dir services/scoring-service &
INGESTION_SERVICE_URL=http://localhost:8001 PREDICTION_SERVICE_URL=http://localhost:8002 SCORING_SERVICE_URL=http://localhost:8003 \
  .venv/bin/python -m uvicorn app.main:app --port 8000 --app-dir services/core-api &

cd frontend/f1-react && npm run dev
```

Compose hostnames do not resolve outside Docker, which is why each service is
handed the others' URLs explicitly above.

Verify the whole product path — not just liveness:

```bash
bash .claude/skills/run-f1-dashboard/smoke.sh
```

26 checks, each stating what it proves. It asserts the guarantees that would
otherwise fail silently: re-locking a forecast returns 409, a pre-quali forecast
carries no win probability, wrong-password and unknown-account responses are
byte-identical, every blog entry cites sources, Bernie's reply contains no
reasoning-trace markers.

---

## Getting data in

```bash
# Classifications + qualifying, all seasons. What the model trains on.
.venv/bin/python services/ingestion-service/scripts/backfill.py results 2010 2026

# Practice long-run pace (2018+ only).
.venv/bin/python services/ingestion-service/scripts/backfill.py practice 2018 2026

# Laps, stints, pit stops, weather, race control, speed traps and sector times.
# 2018+ only — the first season with lap data.
.venv/bin/python services/ingestion-service/scripts/backfill.py full 2018 2025

# Re-read sessions already marked complete — after a schema change, which makes
# "complete" mean something new with no gap left to heal.
BACKFILL_SESSIONS_PER_HOUR=0 \
  .venv/bin/python services/ingestion-service/scripts/backfill.py full 2018 2026 --refresh

# The FIA regulations, chunked by article. Re-run when a section is reissued.
.venv/bin/python services/ingestion-service/scripts/ingest_regulations.py 2026
```

Two things come in over the running service rather than from a script — the
forward calendar, which the scheduler needs before it can place anything, and
the penalty-adjusted starting grid, which is normally automatic and only forced
here when you want it early:

```bash
curl -X POST "localhost:8001/forward/refresh-calendar?from_season=2026&to_season=2027"

# 200 = applied · 409 = FIA has not published yet · 502 = published but unreadable
curl -X POST "localhost:8001/forward/starting-grid/2026/14"
```

FastF1 allows 500 calls per hour. A full 2018–2025 pass exceeds it and stops
partway with the reason logged; re-run after the hour rolls over, as completed
sessions are not re-fetched. Depth is tracked per session, so "complete" always
means complete *at the depth requested*.

Once the services are up this mostly runs itself: finished races are ingested,
starting grids are confirmed from FIA PDFs as they publish, and forecasts are
locked and scored on schedule.

---

## Forecasts

Three lock windows, each conditioned on what is actually known at the time:

| Window | Conditioned on | Publishes |
|---|---|---|
| `pre_quali` | Form and history only | podium, points |
| `post_quali` | Qualifying classification | win, podium, points |
| `final_grid` | Penalty-adjusted starting grid | win, podium, points |

A locked forecast is immutable — re-locking returns 409 — because a forecast you
can edit after the fact is not a forecast. Sampling is seeded on the prediction
id, so a backtest re-run reproduces the published numbers exactly.

Current model: `race-plackett-luce-v4`, 16 features, 20,000 sampled runs.
Training and promotion live in `services/prediction-service/app/training/`;
promotion guards against holdout burn as well as against a worse model.

---

## Tests

```bash
for s in ingestion-service prediction-service scoring-service core-api; do
  (cd services/$s && ../../.venv/bin/python -m pytest tests/ -q)
done
```

522 tests. Many assert measured results rather than behaviour — the regulation
index weights, the withheld circuit features, the market policy — so that a
future change that quietly undoes a measurement fails loudly.

---

## Deploying

See [DEPLOY.md](DEPLOY.md). One small VM running `docker-compose.prod.yml`, with
Caddy terminating TLS as the only thing on the public interface.

**The compose path has never been executed.** There is no Docker on the machine
this was built on, so the images, the container network and the Caddy hop get
their first real test on the host. `compose-preflight.sh` catches what can be
caught statically — dangling service references, missing build contexts, env
vars compose expects that `.env` does not supply — and cannot replace running it.

---

## The rules this codebase keeps

- **A forecast is locked before the event and scored after it.** No edits, no
  retroactive windows, no scoring a forecast locked after the race started.
- **A market is published only where skill was measured.** Pre-quali publishes
  no win probability, and the reason is recorded next to the policy.
- **Bernie may rephrase facts he is given; he may never introduce one.** Facts
  are re-derived and re-injected every turn, and prior replies are explicitly
  not a fact source.
- **Every claim traces to a source.** Blog entries cite theirs; regulation
  answers cite the article number, which is why the corpus is chunked on article
  boundaries rather than by size.
- **A measurement that did not justify a feature is recorded, not deleted.**
  Circuit archetypes were built, evaluated and withheld; the numbers are in
  `prediction-service/app/services/model.py`.
