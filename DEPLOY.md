# Deploying

One small VM running `docker-compose.prod.yml`. Caddy terminates TLS and is the
only thing listening on the public interface; everything else — including the
database — lives on the internal compose network.

**The compose path has never been executed.** There is no Docker on the machine
this was built on, so the images, the container network and the Caddy hop get
their first real test on the host. Expect the first `up --build` to be the
awkward part, and read the logs rather than assuming a healthy `docker ps` means
the thing works.

---

## 1. The box

A 2 vCPU / 4 GB instance is comfortable; 2 GB is tight once FastF1 is parsing a
session. Debian or Ubuntu.

```bash
# On the VM, as root
apt update && apt install -y docker.io docker-compose-plugin git
systemctl enable --now docker
```

Point an A record at the box **before** starting Caddy. Let's Encrypt validates
by connecting back to the name, so a certificate cannot be issued for a host
that does not yet resolve.

### Firewall

Compose publishes only 80 and 443, but a firewall is the thing that stays
correct when a later edit to a compose file is not.

```bash
ufw default deny incoming
ufw allow OpenSSH
ufw allow 80,443/tcp
ufw enable
```

Docker manipulates iptables directly and can bypass ufw for published ports.
That is precisely why `docker-compose.prod.yml` publishes nothing except Caddy —
the firewall is the second line, not the first.

---

## 2. Configuration

```bash
git clone <repo> f1 && cd f1
cp .env.example .env
```

Fill in `.env`:

```bash
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('MONGO_PASSWORD=' + secrets.token_urlsafe(32))"
```

`DOMAIN` and `ACME_EMAIL` are required. `TINKER_API_KEY` is optional — without
it Bernie degrades cleanly and the rest of the product is unaffected.

Do not carry `ANTHROPIC_API_KEY` over. Nothing reads it any more.

---

## 3. Start

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f caddy
```

Caddy's first run obtains the certificate. If it loops, the cause is almost
always DNS not yet resolving to this host.

### Verify nothing but Caddy is reachable

Do this from somewhere else, not from the VM.

```bash
curl -I https://$DOMAIN/                       # 200
curl -sS https://$DOMAIN/api/health            # {"status":"ok",...}
nc -z -w3 $DOMAIN 27017 && echo "DATABASE IS EXPOSED — STOP" || echo "database not reachable (correct)"
nc -z -w3 $DOMAIN 8000  && echo "core-api directly exposed — STOP" || echo "core-api internal (correct)"
```

---

## 4. Load the data

The app starts empty. Ingestion runs through the service's own pipeline so it
inherits the completeness guarantee.

```bash
C="docker compose -f docker-compose.prod.yml exec ingestion-service"

# Classifications and qualifying. Hours for the full range — run it in tmux.
$C python scripts/backfill.py results 2010 2026

# Practice long-run pace (2018+).
$C python scripts/backfill.py practice 2018 2026

# The forward calendar, without which no lock window can fire.
$C curl -X POST "http://localhost:8001/forward/refresh-calendar?from_season=2026&to_season=2027"
```

Then train, or copy `app/model_weights.json` from a machine that already has it:

```bash
docker compose -f docker-compose.prod.yml exec prediction-service \
  python scripts/train_model.py --from-api --from-season 2010 \
  --validation-seasons 2022,2023 --write
```

prediction-service refuses to serve forecasts without weights, so this is not
optional.

---

## 5. What runs by itself

| Job | Where | Cadence |
|---|---|---|
| Gap healing for the current season | ingestion | `AUTO_REFRESH_HOURS`, default 24 |
| Look for a published FIA starting grid | ingestion | `GRID_CHECK_MINUTES`, default 30 |
| Fire lock windows | prediction | `LOCK_CHECK_MINUTES`, default 5 |

All three tick once at startup as well as on their interval, so a restart does
not postpone an open window.

Reconciliation after a race is still manual:

```bash
docker compose -f docker-compose.prod.yml exec scoring-service \
  curl -X POST http://localhost:8003/reconcile/2026/14
```

---

## 6. Costs that can run away

**Bernie spends real money per call.** Conversations require an account and are
capped per user per day, with a global daily ceiling behind that — per-user
limits multiply by however many accounts someone registers. Explanations
(`/bernie/why`) are cached against the prediction, so they cost one call per
forecast rather than one per reader.

Set both in `.env`. `0` disables a limit, which is fine locally and reckless in
public.

**Registration is open and unthrottled.** There is no email verification and no
rate limit on `/auth/register`. For a small public deployment this is usually
survivable; if the app gets attention, this is the first thing to fix.

---

## 7. Backups

The corpus is re-ingestable from FastF1 and the model can be retrained. What is
**not** recoverable is `f1_prediction.predictions` — locked forecasts are the
product's entire claim, and a lost prediction cannot be honestly recreated
because any replacement would be written after the race.

```bash
docker compose -f docker-compose.prod.yml exec mongodb \
  mongodump -u "$MONGO_USER" -p "$MONGO_PASSWORD" --authenticationDatabase admin \
  --db f1_prediction --archive > "predictions-$(date +%F).archive"
```

Run it on a schedule and keep the copies off the box.
