"""scripts/bernie_eval.py without the services: the checks, the question set,
and a run against fake services. No network, no Mongo, no model."""

import argparse
import json
import os
import re
import sys

import httpx

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

import bernie_eval as ev  # noqa: E402

CORE_BERNIE = os.path.join(HERE, "..", "..", "services", "core-api", "app", "services", "bernie.py")
CORE_ROUTER = os.path.join(HERE, "..", "..", "services", "core-api", "app", "routers", "bernie.py")

PU_HITS = [
    {"article": "B8.2.8", "heading": "Penalties", "score": 15.0,
     "text": "Should a driver use more than the number of power unit elements permitted ..."},
    {"article": "B8.2.1", "heading": "Power units", "score": 9.0, "text": "Each driver may use ..."},
    {"article": "B3.1", "heading": "Other", "score": 2.0, "text": "Unrelated."},
]


def test_the_question_set_is_valid():
    cases = ev.load_cases()
    assert len(cases) >= 20
    assert any(case.get("known_gap") for case in cases)


def test_validate_catches_typos():
    problems = ev.validate([
        {"id": "a", "turns": [{"question": "q", "expect": {"answer_anyy": ["x"]}}]},
        {"id": "a", "turns": [{"question": "", "expect": {"answer_none": ["("]}}]},
    ])
    text = "\n".join(problems)
    assert "unknown checks" in text and "duplicate id" in text
    assert "no question" in text and "bad pattern" in text


def test_mirrored_constants_match_core_api():
    source = open(CORE_BERNIE, encoding="utf-8").read()
    assert re.search(r"^RELATIVE_SCORE_FLOOR = {}$".format(ev.RELATIVE_SCORE_FLOOR), source, re.M)
    assert 'REGULATIONS_KEY = "{}"'.format(ev.REGULATIONS_KEY) in source
    assert re.search(r"^MAX_REFERENCED_PASSAGES = {}$".format(ev.MAX_REFERENCED_PASSAGES), source, re.M)
    assert re.search(r"def regulation_facts\(\s*hits[^)]*limit: int = {}".format(ev.PASSAGE_LIMIT), source)
    router = open(CORE_ROUTER, encoding="utf-8").read()
    assert '"/regulations/search",\n            {{"q": question, "season": season, "limit": {}}}'.format(
        ev.SEARCH_LIMIT) in router


def test_select_passages_applies_floor_and_cut():
    assert [h["article"] for h in ev.select_passages(PU_HITS)] == ["B8.2.8", "B8.2.1"]
    many = [{"article": "A{}".format(i), "text": "t", "score": 10.0} for i in range(6)]
    assert len(ev.select_passages(many)) == 3
    assert ev.select_passages([{"article": "A", "text": ""}]) == []


def test_select_passages_adds_referred_articles_of_kept_hits_only():
    hits = PU_HITS + [
        {"article": "B8.2.2", "text": "numbers", "referred_by": "B8.2.8"},
        {"article": "B3.2", "text": "x", "referred_by": "B3.1"},  # B3.1 fell below the floor
    ]
    assert [h["article"] for h in ev.select_passages(hits)] == ["B8.2.8", "B8.2.1", "B8.2.2"]


def test_passage_article_reads_rendered_passages():
    assert ev.passage_article("Article B8.2.8 — Penalties (2026 Section B, issue 8): ...") == "B8.2.8"
    assert ev.passage_article("Article B1.2 [truncated] — X (2026 Section B, issue 8): ...") == "B1.2"
    assert ev.passage_article("something else") is None
    assert ev.passage_article(
        "Article B8.2.2 — Numbers (2026 Section B, issue 8; referred to by B8.2.8): ...") == "B8.2.2"


def test_entry_driver():
    assert ev.entry_driver("Q1 Max Verstappen (Red Bull)") == "Max Verstappen"
    assert ev.entry_driver("Lando Norris (McLaren) · win 30% · podium 60%") == "Lando Norris"


def test_retrieval_checks():
    passages = ev.passages_from_hits(PU_HITS)
    results = ev.check_turn({"retrieved_articles": ["B8.2.8", "B8.2.2"], "passage_terms": ["power unit"]}, passages)
    assert [s for _, s, _ in results] == [ev.PASS, ev.FAIL, ev.PASS]
    assert ev.turn_status(results) == ev.FAIL


def test_answer_checks_are_skipped_without_an_answer():
    results = ev.check_turn({"answer_any": ["x"], "names_entry": {"fact": "f", "index": 0}}, None)
    assert {s for _, s, _ in results} == {ev.SKIP}


def test_answer_checks():
    facts = {"qualifying classification": ["Q1 Nico Hülkenberg (Audi)", "Q2 Lando Norris (McLaren)"]}
    expect = {"requires_facts": ["qualifying classification"],
              "names_entry": {"fact": "qualifying classification", "index": 0},
              "answer_any": [r"\bpole\b"], "answer_none": [r"\d+%"]}
    good = ev.check_turn(expect, None, "Hulkenberg took pole.", facts)
    assert ev.turn_status(good) == ev.PASS
    bad = ev.check_turn(expect, None, "Norris, with a 40% chance.", facts)
    assert [s for _, s, _ in bad] == [ev.FAIL, ev.FAIL, ev.FAIL]
    skipped = ev.check_turn(expect, None, "anything", {})
    assert ev.turn_status(skipped) == ev.SKIP


def test_parse_sse():
    body = [
        "event: delta", 'data: {"text": "Hel"}', "",
        "event: delta", 'data: {"text": "lo"}', "",
        "event: done", 'data: {"answer": "Hello", "thread_id": "t1"}', "",
    ]
    deltas, done, error = ev.parse_sse(body)
    assert deltas == 2 and done["answer"] == "Hello" and error is None
    deltas, done, error = ev.parse_sse(["event: error", 'data: {"reason": "budget"}'])
    assert done is None and error == {"reason": "budget"}


def fake_services():
    threads = {}

    def handler(request):
        path = request.url.path
        if path == "/regulations/search":
            return httpx.Response(200, json=PU_HITS)
        if path == "/auth/login":
            return httpx.Response(200, json={"access_token": "tok"})
        assert request.headers["Authorization"] == "Bearer tok"
        body = json.loads(request.content)
        thread_id = path.rsplit("/", 1)[-1] if path != "/bernie/threads" else "t{}".format(len(threads) + 1)
        threads.setdefault(thread_id, []).append(body["question"])
        facts = {"qualifying classification": ["Q1 Max Verstappen (Red Bull)", "Q2 Lando Norris (McLaren)"],
                 ev.REGULATIONS_KEY: ["Article B8.2.8 — Penalties (2026 Section B, issue 8): power unit ..."]}
        answer = "Verstappen is on pole." if len(threads[thread_id]) == 1 else "Norris was second."
        if request.url.params.get("stream") == "true":
            sse = 'event: delta\ndata: {"text": "x"}\n\nevent: done\ndata: ' + json.dumps(
                {"answer": answer, "thread_id": thread_id, "grounded_on": facts}) + "\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(201, json={"answer": answer, "thread_id": thread_id, "grounded_on": facts})

    return httpx.Client(transport=httpx.MockTransport(handler)), threads


def args(**kw):
    base = dict(ask=False, stream=False, season=2026, round=21,
                core_api="http://core", ingestion="http://ingest")
    base.update(kw)
    return argparse.Namespace(**base)


def case(cid, *turns, **extra):
    return dict(id=cid, turns=[{"question": q, "expect": e} for q, e in turns], **extra)


def test_retrieval_run_reports_handed_articles_and_rank():
    client, _ = fake_services()
    result = ev.run_case(case("pu", ("q?", {"retrieved_articles": ["B8.2.8", "B8.2.2"]})), args(), client, None)
    turn = result["turns"][0]
    assert turn["handed"] == ["B8.2.8", "B8.2.1"]
    assert turn["search_rank"] == {"B8.2.8": 1, "B8.2.2": None}
    assert result["status"] == ev.FAIL


def test_ask_run_keeps_one_thread_across_turns():
    for stream in (False, True):
        client, threads = fake_services()
        token = ev.login(client, "http://core", "a@b.c", "pw")
        result = ev.run_case(case(
            "pole",
            ("Who is on pole?", {"names_entry": {"fact": "qualifying classification", "index": 0}}),
            ("And second?", {"names_entry": {"fact": "qualifying classification", "index": 1}}),
        ), args(ask=True, stream=stream), client, token)
        assert result["status"] == ev.PASS, result
        assert list(threads.values()) == [["Who is on pole?", "And second?"]]


def test_summary_separates_known_gaps_and_flags_changes():
    results = [
        {"id": "a", "category": "x", "known_gap": False, "status": ev.PASS, "turns": []},
        {"id": "gap", "category": "x", "known_gap": True, "status": ev.FAIL,
         "turns": [{"checks": [("retrieved B8.2.2", ev.FAIL, "handed B8.2.8")]}]},
    ]
    lines, code = ev.summarise(results, previous={"a": ev.FAIL})
    text = "\n".join(lines)
    assert code == ev.EXIT_OK
    assert "(was fail)" in text and "known gap" in text and "0 of 1 closed" in text
    results[0]["status"] = ev.FAIL
    assert ev.summarise(results)[1] == ev.EXIT_FAILED
