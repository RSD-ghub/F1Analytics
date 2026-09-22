# What's left

Ordered by what blocks what. Checked items are verified in the tree, not assumed.

## Blocking a public deploy

- [ ] **Rotate `ANTHROPIC_API_KEY`.** It was printed into a session transcript. Removing it
      from `.env` (done) is not rotation. Owner: you, at console.anthropic.com.
- [ ] **Generate production `JWT_SECRET` and `MONGO_PASSWORD` on the box.** Never through a
      transcript. Minimum 32 bytes for the JWT secret (RFC 7518 §3.2).
- [ ] **Provision the VM and point DNS at it.** The A record must resolve *before* Caddy
      first starts, or ACME fails and retries with backoff.
- [ ] **First `docker-compose.prod.yml` run.** This path has never been executed once. Assume
      it breaks the first time.

## Approved roadmap items, not yet built

- [x] **Surface grid penalties in Bernie.** Done 23 Sep. Penalties reach the facts pack worst
      first with the stewards' wording verbatim. Note the stored rows predated the fields and
      carried nothing — R13 and R14 grids were re-resolved to populate them.
- [ ] **Surface grid penalties in One Blog.** Same data, still not on the page.
- [ ] **Paddock news in One Blog**, and the same feed into Bernie so she can correlate.
      Not started. Needs a source decision first.

## Verification owed

- [x] **Bernie ↔ regulations, end to end.** Verified live 22 Sep. The wiring was correct in
      source but dead in the running system: ingestion and core-api were processes from 8-10
      days ago, predating `ca06b3f`, so `/regulations/search` 404'd and the facts pack carried
      no rules at all. Both restarted; retrieval and citation now confirmed working.
- [ ] **Restart prediction (8002) and scoring (8003).** Still running code from 10 and 9 days
      ago. They predate the speed-trap capture and the retrain-gate record. 8002 is what locks
      the Baku forecast on the 26th — restart it before then.
- [ ] **Retrieval misses the article that answers the question.** "How many power unit elements
      before a penalty" returns B8.2.8, which *states the penalty* and refers to the allowances
      in B8.2.2-B8.2.4 without containing them. The corpus has those articles; lexical search
      does not reach them. Bernie handles it correctly — cites B8.2.8, refuses to invent the
      numbers — but the answer was in the corpus and we did not retrieve it. This is the
      reranking gap.
- [ ] **Azerbaijan R15, 26 September.** First race where all three lock windows fire with
      every fix in place. Watch it, then reconcile.

## Waiting on data, no action

- [ ] **Retrain / promotion gate.** Holding at 13 unseen races against a threshold of 15.
      Clears after two more races. Running it also clears the train/serve skew left by the
      circuit-alias fix (Marina Bay/Singapore and friends counted twice).

## Decided — close these out

- [x] Lap backfill 2018–2026: complete, 205,681 laps, every one carrying a speed trap.
- [x] Circuit-character features: built, measured, withheld. Then refitted on real trap
      speed, measured again, withheld again — worse by almost exactly the same margin.
      The plan at `~/.claude/plans/mossy-nibbling-catmull.md` is answered. The answer is no.
