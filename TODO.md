# What's left

Rewritten 15 Nov 2026 from the code on `main` (15cd43e) and the project's
notes. The September version had gone stale: the promotion gate cleared on
5 Oct, Baku was reconciled, rate limits and password reset exist, and CI runs
on every pull request. The full list with effort and benefit is the
"F1 platform enhancement list" doc; the deploy path is in the
"Production readiness grade" doc.

## Blocking a public deploy

- [ ] **Rotate secrets on the server.** Generate `JWT_SECRET` (32+ bytes) and
      `MONGO_PASSWORD` on the box, never through a transcript. Revoke the old
      Anthropic key even though nothing reads it any more.
- [ ] **Provision the VM and point DNS at it** before Caddy's first start, or
      ACME fails and backs off.
- [ ] **First `docker-compose.prod.yml` run.** Never executed. Assume it breaks.
      Known before it runs: the live feed writes its recording into a
      root-owned folder while the service runs as a normal user, and the F1TV
      token is kept outside any volume, so a redeploy signs F1TV out.
- [ ] **Security policy.** Add OpenF1, F1's image server and jsdelivr to the
      CSP in `Caddyfile`, then switch it from report-only to enforcing.
- [ ] **Limit race replays and simulations.** They need no sign-in and each new
      combination runs tens of seconds in the process that locks forecasts.
- [ ] **Hide /docs in production, send index.html no-cache, upgrade Vite**
      (two dev-server advisories).

## Running on the Mac

- [ ] **Set `ALERT_WEBHOOK_URL`** (an ntfy topic) so the watchdog reaches a phone.
- [ ] **Install the launchd agents** (`scripts/mac/install_launchd.sh`): services
      restart on their own, backups and the retrain report are scheduled.
- [ ] **Backups off the machine.** Point `BACKUP_DIR` at iCloud Drive or another disk.
- [ ] **An outside uptime check**, so a dead Mac is noticed.

## Verification owed

- [ ] **Bernie against the question set.** `python scripts/bernie_eval.py`, then
      `--ask --stream` (streaming has never run against live Tinker).
- [ ] **Regulation retrieval misses referred-to articles.** "How many power unit
      elements before a penalty" returns B8.2.8, which refers to B8.2.2-B8.2.4
      without containing them. Marked as a known gap in the question set.
- [ ] **Paddock news against the live feeds** after any deploy:
      `curl -X POST localhost:8001/news/refresh`, both outlets `new`, not `error`.

## Forecast quality, known

- [ ] Late in a race the live forecast is too wide: "order holds" beats its
      podium probabilities at 90%.
- [ ] The live NN corrector is off in the wet (5 wet races in its data).
- [ ] Gaps to lapped cars are estimated.
- [ ] The linear lap model (PR #31) failed replay and stays off.

## Decided — do not reopen

- Weather as a model input: backtested, no gain; display only.
- TUM timing database and undercut-f1: no gain.
- Circuit-character features: measured twice, withheld twice.
- Bookmaker odds: a Track record benchmark at most, never a model input;
  parked until a usable feed exists.
