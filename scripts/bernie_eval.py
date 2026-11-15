#!/usr/bin/env python
"""Ask Bernie a fixed set of questions and check what she was given and said.

    # Free: regulation retrieval only, no model calls, needs ingestion (:8001)
    python scripts/bernie_eval.py

    # Full: real conversations through core-api (:8000), uses Bernie's budget
    python scripts/bernie_eval.py --ask --round 21 --email you@example.com
    python scripts/bernie_eval.py --ask --stream --round 21 --email you@example.com

    # Save a run, then compare a later run with it
    python scripts/bernie_eval.py --json before.json
    python scripts/bernie_eval.py --json after.json --compare before.json

The questions live in ``scripts/bernie_questions.json``. Each turn's ``expect``
block can hold:

* ``retrieved_articles`` — articles that must be among the regulation passages
  Bernie is handed (not just returned by search: the same score floor and cut
  core-api applies);
* ``passage_terms`` — patterns that must each appear in some passage handed to
  her, for rules whose numbering has not been pinned;
* ``facts_present`` — facts-pack keys that must be there;
* ``requires_facts`` — keys without which the turn is skipped, not failed
  (no qualifying yet, a forecast already locked, ...);
* ``names_entry`` — ``{"fact": key, "index": n}``: the answer must name the
  driver in that entry of that fact list;
* ``answer_any`` / ``answer_none`` — patterns of which one must / none may
  match the answer.

A case marked ``known_gap`` is expected to fail today. It is reported apart,
and it says so when it starts passing.

Without ``--ask`` only the retrieval checks run, straight against
ingestion-service's search, so a retrieval change can be measured without
spending anything. With ``--ask`` every turn is a real Bernie turn: about 23
model calls for the whole set, counted against the account's daily budget, so
``--only`` takes a comma-separated list of case ids. ``--stream`` asks for the
answers as server-sent events, which also checks that streaming works against
the live model.

The password comes from ``BERNIE_EVAL_PASSWORD`` or a prompt, never an
argument. Exit code 0 when every check that ran passed (known gaps aside), 1
otherwise, 2 when a service could not be reached. Reads only; the conversations
it opens are saved under the account like any other.
"""

import argparse
import getpass
import json
import os
import re
import sys
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

QUESTIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bernie_questions.json")

#: Mirrors of core-api's choices, so the retrieval-only mode judges what Bernie
#: would actually be handed. tests/test_bernie_eval.py checks them against
#: core-api's source, so they cannot drift apart silently.
REGULATIONS_KEY = "possibly relevant FIA regulation passages"
RELATIVE_SCORE_FLOOR = 0.4
SEARCH_LIMIT = 6  # core-api over-fetches six ...
PASSAGE_LIMIT = 3  # ... and renders at most three

EXPECT_KEYS = {
    "retrieved_articles", "passage_terms", "facts_present", "requires_facts",
    "names_entry", "answer_any", "answer_none",
}
RETRIEVAL_KEYS = {"retrieved_articles", "passage_terms"}

PASS, FAIL, SKIP = "pass", "fail", "skip"
EXIT_OK, EXIT_FAILED, EXIT_UNREACHABLE = 0, 1, 2


# ── The question set ────────────────────────────────────────────────────────

def load_cases(path: str = QUESTIONS) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    cases = data["cases"]
    problems = validate(cases)
    if problems:
        raise ValueError("bad question set:\n  " + "\n  ".join(problems))
    return cases


def validate(cases: Sequence[Dict[str, Any]]) -> List[str]:
    """Everything wrong with a question set, so a typo fails loudly."""
    problems: List[str] = []
    seen = set()
    for case in cases:
        cid = case.get("id")
        if not cid:
            problems.append("a case has no id")
            continue
        if cid in seen:
            problems.append("{}: duplicate id".format(cid))
        seen.add(cid)
        turns = case.get("turns") or []
        if not turns:
            problems.append("{}: no turns".format(cid))
        for n, turn in enumerate(turns, 1):
            if not (turn.get("question") or "").strip():
                problems.append("{} turn {}: no question".format(cid, n))
            expect = turn.get("expect") or {}
            unknown = set(expect) - EXPECT_KEYS
            if unknown:
                problems.append("{} turn {}: unknown checks {}".format(cid, n, sorted(unknown)))
            for key in ("passage_terms", "answer_any", "answer_none"):
                for pattern in expect.get(key) or []:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        problems.append("{} turn {}: bad pattern {!r}: {}".format(cid, n, pattern, exc))
            entry = expect.get("names_entry")
            if entry is not None and not (isinstance(entry, dict) and "fact" in entry
                                          and isinstance(entry.get("index"), int)):
                problems.append("{} turn {}: names_entry needs fact and index".format(cid, n))
    return problems


# ── What Bernie is handed ───────────────────────────────────────────────────

def select_passages(hits: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The search hits core-api would turn into passages, best first.

    The same rule as ``regulation_facts``: hits with an article and text, those
    scoring at least the floor times the best, at most three.
    """
    scored = [hit for hit in hits if hit.get("article") and hit.get("text")]
    if not scored:
        return []
    best = max((hit.get("score") or 0.0) for hit in scored)
    if best > 0:
        scored = [hit for hit in scored if (hit.get("score") or 0.0) >= best * RELATIVE_SCORE_FLOOR]
    return scored[:PASSAGE_LIMIT]


_PASSAGE_ARTICLE = re.compile(r"^Article (\S+?)(?: \[truncated\])? — ")


def passage_article(passage: str) -> Optional[str]:
    """The article number at the head of a rendered passage."""
    match = _PASSAGE_ARTICLE.match(passage)
    return match.group(1) if match else None


def passages_from_hits(hits: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """``(article, searchable text)`` for each passage Bernie would get."""
    return [
        (hit["article"], "{} {}".format(hit.get("heading") or "", hit.get("text") or ""))
        for hit in select_passages(hits)
    ]


def passages_from_facts(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    """``(article, text)`` for each passage in a real turn's ``grounded_on``."""
    out = []
    for passage in facts.get(REGULATIONS_KEY) or []:
        out.append((passage_article(passage) or "?", passage))
    return out


# ── Checks ──────────────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    """Lower case without accents, so Hülkenberg matches Hulkenberg."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def entry_driver(entry: str) -> str:
    """The driver named by a facts entry.

    ``"Q1 Max Verstappen (Red Bull)"`` and
    ``"Max Verstappen (Red Bull) · win 30% · ..."`` both give ``Max Verstappen``.
    """
    text = re.sub(r"^Q\d+\s+", "", (entry or "").strip())
    return text.split(" (", 1)[0].split(" · ", 1)[0].strip()


def check_turn(
    expect: Dict[str, Any],
    passages: Optional[List[Tuple[str, str]]],
    answer: Optional[str] = None,
    facts: Optional[Dict[str, Any]] = None,
) -> List[Tuple[str, str, str]]:
    """``(check, status, detail)`` for every check in ``expect``.

    ``answer`` and ``facts`` are ``None`` in retrieval-only mode, where every
    check that needs them is skipped. ``passages`` is ``None`` when nothing
    about regulations was looked at.
    """
    results: List[Tuple[str, str, str]] = []
    live = answer is not None

    missing = [key for key in expect.get("requires_facts") or [] if live and key not in (facts or {})]
    if missing:
        return [("requires_facts", SKIP, "no {} in her facts".format(", ".join(missing)))]

    articles = [article for article, _ in passages or []]
    for article in expect.get("retrieved_articles") or []:
        if passages is None:
            results.append(("retrieved " + article, SKIP, "no search run"))
        elif article in articles:
            results.append(("retrieved " + article, PASS, "handed passage {}".format(articles.index(article) + 1)))
        else:
            results.append(("retrieved " + article, FAIL, "handed {}".format(", ".join(articles) or "nothing")))

    for pattern in expect.get("passage_terms") or []:
        if passages is None:
            results.append(("passage /{}/".format(pattern), SKIP, "no search run"))
            continue
        found = [article for article, text in passages if re.search(pattern, text, re.IGNORECASE)]
        if found:
            results.append(("passage /{}/".format(pattern), PASS, "in " + ", ".join(found)))
        else:
            results.append(("passage /{}/".format(pattern), FAIL, "handed {}".format(", ".join(articles) or "nothing")))

    if not live:
        for key in ("facts_present", "names_entry", "answer_any", "answer_none"):
            if expect.get(key):
                results.append((key, SKIP, "needs --ask"))
        return results

    for key in expect.get("facts_present") or []:
        results.append(("fact '{}'".format(key), PASS if key in facts else FAIL,
                        "present" if key in facts else "missing"))

    entry = expect.get("names_entry")
    if entry:
        rows = (facts or {}).get(entry["fact"]) or []
        if entry["index"] >= len(rows):
            results.append(("names entry", SKIP, "{} has {} rows".format(entry["fact"], len(rows))))
        else:
            driver = entry_driver(rows[entry["index"]])
            surname = driver.split()[-1] if driver else ""
            ok = bool(surname) and _fold(surname) in _fold(answer)
            results.append(("names " + (driver or "?"), PASS if ok else FAIL,
                            "named" if ok else "answer does not name them"))

    patterns = expect.get("answer_any") or []
    if patterns:
        hit = next((p for p in patterns if re.search(p, answer)), None)
        results.append(("answer matches one of {}".format(patterns), PASS if hit else FAIL,
                        "matched /{}/".format(hit) if hit else "none matched"))

    for pattern in expect.get("answer_none") or []:
        match = re.search(pattern, answer)
        results.append(("answer avoids /{}/".format(pattern), FAIL if match else PASS,
                        "said {!r}".format(match.group(0)) if match else "clean"))
    return results


def turn_status(results: Iterable[Tuple[str, str, str]]) -> str:
    statuses = [status for _, status, _ in results]
    if FAIL in statuses:
        return FAIL
    if PASS in statuses:
        return PASS
    return SKIP


# ── Talking to the services ─────────────────────────────────────────────────

class Unreachable(Exception):
    pass


def parse_sse(lines: Iterable[str]) -> Tuple[int, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """``(delta count, done payload, error payload)`` from an SSE body."""
    deltas, done, error = 0, None, None
    event, data = None, []
    for raw in list(lines) + [""]:
        line = raw.rstrip("\r\n")
        if not line:
            if event:
                payload = json.loads("\n".join(data)) if data else {}
                if event == "delta":
                    deltas += 1
                elif event == "done":
                    done = payload
                elif event == "error":
                    error = payload
            event, data = None, []
        elif line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].strip())
    return deltas, done, error


def search(client, ingestion: str, question: str, season: int, limit: int) -> List[Dict[str, Any]]:
    import httpx

    try:
        response = client.get(ingestion.rstrip("/") + "/regulations/search",
                              params={"q": question, "season": season, "limit": limit})
    except httpx.HTTPError as exc:
        raise Unreachable("ingestion-service: {}".format(exc))
    response.raise_for_status()
    return response.json()


def login(client, core: str, email: str, password: str) -> str:
    import httpx

    try:
        response = client.post(core.rstrip("/") + "/auth/login", json={"email": email, "password": password})
    except httpx.HTTPError as exc:
        raise Unreachable("core-api: {}".format(exc))
    if response.status_code != 200:
        raise SystemExit("login failed: HTTP {} {}".format(response.status_code, response.text[:200]))
    return response.json()["access_token"]


def ask(client, core: str, token: str, season: int, round_number: int, question: str,
        thread_id: Optional[str], stream: bool) -> Dict[str, Any]:
    """One Bernie turn: ``{"answer", "facts", "thread_id", "deltas", "error"}``."""
    import httpx

    url = core.rstrip("/") + ("/bernie/threads/{}".format(thread_id) if thread_id else "/bernie/threads")
    body = {"question": question} if thread_id else {"season": season, "round": round_number, "question": question}
    params = {"stream": "true"} if stream else None
    headers = {"Authorization": "Bearer " + token}
    try:
        if stream:
            with client.stream("POST", url, json=body, params=params, headers=headers, timeout=180) as response:
                if response.status_code >= 400:
                    response.read()
                    return {"error": "HTTP {} {}".format(response.status_code, response.text[:200])}
                deltas, done, error = parse_sse(response.iter_lines())
            if error or not done:
                return {"error": "stream ended with {}".format(error or "no done event"), "deltas": deltas}
            payload = done
        else:
            response = client.post(url, json=body, headers=headers, timeout=180)
            if response.status_code >= 400:
                return {"error": "HTTP {} {}".format(response.status_code, response.text[:200])}
            payload, deltas = response.json(), None
    except httpx.HTTPError as exc:
        raise Unreachable("core-api: {}".format(exc))
    return {
        "answer": payload.get("answer") or "",
        "facts": payload.get("grounded_on") or {},
        "thread_id": payload.get("thread_id"),
        "deltas": deltas,
        "error": None,
    }


# ── A run ───────────────────────────────────────────────────────────────────

def needs_search(case: Dict[str, Any]) -> bool:
    return any(RETRIEVAL_KEYS & set(turn.get("expect") or {}) for turn in case["turns"])


def run_case(case, args, client, token) -> Dict[str, Any]:
    season = case.get("season", args.season)
    round_number = case.get("round", args.round)
    turns_out = []
    thread_id = None
    for turn in case["turns"]:
        expect = turn.get("expect") or {}
        record: Dict[str, Any] = {"question": turn["question"]}
        if args.ask:
            reply = ask(client, args.core_api, token, season, round_number, turn["question"], thread_id, args.stream)
            if reply.get("error"):
                record.update(status=FAIL, checks=[("turn", FAIL, reply["error"])])
                turns_out.append(record)
                break
            thread_id = reply["thread_id"]
            passages = passages_from_facts(reply["facts"]) if RETRIEVAL_KEYS & set(expect) else None
            results = check_turn(expect, passages, reply["answer"], reply["facts"])
            if args.stream and not reply.get("deltas"):
                results.append(("streamed in pieces", FAIL, "no delta events before done"))
            record["answer"] = reply["answer"]
            record["deltas"] = reply.get("deltas")
        else:
            passages = None
            if RETRIEVAL_KEYS & set(expect):
                hits = search(client, args.ingestion, turn["question"], season, SEARCH_LIMIT)
                passages = passages_from_hits(hits)
                record["handed"] = [article for article, _ in passages]
                wanted = expect.get("retrieved_articles") or []
                if wanted:
                    wide = search(client, args.ingestion, turn["question"], season, 25)
                    ranks = [hit.get("article") for hit in wide]
                    record["search_rank"] = {a: (ranks.index(a) + 1 if a in ranks else None) for a in wanted}
            results = check_turn(expect, passages)
        record["checks"] = results
        record["status"] = turn_status(results)
        turns_out.append(record)
    statuses = [t["status"] for t in turns_out]
    status = FAIL if FAIL in statuses else PASS if PASS in statuses else SKIP
    return {"id": case["id"], "category": case.get("category"), "known_gap": bool(case.get("known_gap")),
            "status": status, "turns": turns_out}


def summarise(results: Sequence[Dict[str, Any]], previous: Optional[Dict[str, str]] = None) -> Tuple[List[str], int]:
    """Report lines and the exit code."""
    lines = []
    failed = 0
    for result in results:
        label = result["status"].upper()
        if result["known_gap"]:
            label = "GAP CLOSED" if result["status"] == PASS else "known gap" if result["status"] == FAIL else label
        elif result["status"] == FAIL:
            failed += 1
        change = ""
        if previous and result["id"] in previous and previous[result["id"]] != result["status"]:
            change = "  (was {})".format(previous[result["id"]])
        lines.append("{:<11} {:<26} {}{}".format(label, result["id"], result.get("category") or "", change))
        for n, turn in enumerate(result["turns"], 1):
            for check, status, detail in turn["checks"]:
                if status != PASS:
                    lines.append("    turn {} {:<5} {}: {}".format(n, status, check, detail))
            if turn.get("search_rank"):
                ranks = ", ".join("{} at {}".format(a, r or ">25") for a, r in turn["search_rank"].items())
                lines.append("    turn {} search rank: {}".format(n, ranks))
    counts = {s: sum(1 for r in results if r["status"] == s and not r["known_gap"]) for s in (PASS, FAIL, SKIP)}
    gaps = [r for r in results if r["known_gap"]]
    closed = sum(1 for r in gaps if r["status"] == PASS)
    lines.append("")
    lines.append("{} passed, {} failed, {} skipped; known gaps: {} of {} closed".format(
        counts[PASS], counts[FAIL], counts[SKIP], closed, len(gaps)))
    return lines, EXIT_FAILED if failed else EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ask", action="store_true", help="real Bernie turns through core-api (uses budget)")
    parser.add_argument("--stream", action="store_true", help="with --ask: request server-sent events")
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--round", type=int, help="race the conversations are about (needed with --ask)")
    parser.add_argument("--email", help="account to ask as (with --ask)")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--questions", default=QUESTIONS)
    parser.add_argument("--core-api", default="http://localhost:8000")
    parser.add_argument("--ingestion", default="http://localhost:8001")
    parser.add_argument("--json", help="write the full results here")
    parser.add_argument("--compare", help="an earlier --json file to compare with")
    args = parser.parse_args(argv)

    import httpx

    cases = load_cases(args.questions)
    if args.only:
        wanted = {cid.strip() for cid in args.only.split(",")}
        unknown = wanted - {case["id"] for case in cases}
        if unknown:
            parser.error("unknown case ids: {}".format(", ".join(sorted(unknown))))
        cases = [case for case in cases if case["id"] in wanted]
    if args.ask:
        if args.round is None or not args.email:
            parser.error("--ask needs --round and --email")
    else:
        if args.stream:
            parser.error("--stream needs --ask")
        cases = [case for case in cases if needs_search(case)]

    previous = None
    if args.compare:
        with open(args.compare, encoding="utf-8") as handle:
            previous = {r["id"]: r["status"] for r in json.load(handle)["results"]}

    with httpx.Client(timeout=30) as client:
        token = None
        try:
            if args.ask:
                password = os.environ.get("BERNIE_EVAL_PASSWORD") or getpass.getpass("Password for {}: ".format(args.email))
                token = login(client, args.core_api, args.email, password)
                turns = sum(len(case["turns"]) for case in cases)
                print("Asking Bernie {} questions ({} cases).".format(turns, len(cases)), file=sys.stderr)
            results = [run_case(case, args, client, token) for case in cases]
        except Unreachable as exc:
            print("could not reach {}".format(exc), file=sys.stderr)
            return EXIT_UNREACHABLE

    lines, code = summarise(results, previous)
    print("\n".join(lines))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"mode": "ask" if args.ask else "retrieval", "stream": args.stream,
                       "season": args.season, "round": args.round, "results": results},
                      handle, indent=2, ensure_ascii=False)
    return code


if __name__ == "__main__":
    sys.exit(main())
