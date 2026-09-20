"""Chunking the FIA regulations by article.

The unit of storage is the numbered article, because that is simultaneously a
semantic boundary and a citation. A token-window chunker would split a rule
mid-sentence and hand Bernie half of one; article chunks arrive with the
reference that identifies them, which for a product promising that claims trace
to sources is the difference between usable retrieval and none.
"""

import pytest

from app.services.regulations import (
    Article,
    RegulationDocument,
    SECTIONS,
    _split,
)


def _doc(section="B", issue=8):
    return RegulationDocument(
        season=2026, section=section, issue=issue,
        published="2026-08-05", url="http://example/reg.pdf",
    )


SPORTING = """
B1.6 Pit Entry Road, Pit Lane And Pit Exit Road 8
B1.9 Incidents, Infringements & Penalties 13
4 ©2026 Fédération Internationale de l'Automobile Issue 08
SECTION B: SPORTING REGULATIONS
B1.7 Changes Of Driver
B1.7.1 During a Championship each Competitor will be permitted to use a maximum of
four drivers, and any new driver may score points in the Championship.
B1.7.2 Provided any change proposed after the end of initial scrutineering receives
the consent of the stewards, a change of driver may be made:
a. At each Competition where a sprint session is not scheduled, at any time before
the start of the qualifying session.
B1.6.4 Closing of the Pit Lane
In exceptional circumstances the Race Director may ask for the pit entry to be closed
for safety reasons. At such times drivers may only enter the Pit Lane in order for
essential repairs to be carried out to their F1 Car.
"""


def test_articles_are_split_on_their_numbers():
    articles = _split(SPORTING, _doc())
    numbers = {a.article for a in articles}
    assert "B1.7.1" in numbers
    assert "B1.7.2" in numbers


def test_an_article_keeps_its_sub_points():
    """Chunking by size would cut "a." away from the rule it qualifies."""
    articles = _split(SPORTING, _doc())
    changes = next(a for a in articles if a.article == "B1.7.2")
    assert "consent of the stewards" in changes.text
    assert "sprint session is not scheduled" in changes.text


def test_the_table_of_contents_is_not_stored_as_articles():
    """Contents lines look exactly like headings — "B1.6 Pit Entry Road ... 8",
    the trailing number being a page reference. Left in, a search for "pit lane"
    returns the contents entry rather than the rule."""
    articles = _split(SPORTING, _doc())
    pit = [a for a in articles if a.article == "B1.6"]
    # Either dropped entirely, or superseded by real text — never the bare
    # heading-plus-page-number form.
    assert all(not a.text.strip().endswith(" 8") for a in pit)


def test_page_furniture_never_lands_inside_an_article():
    articles = _split(SPORTING, _doc())
    assert all("©" not in a.text for a in articles)
    assert all("Fédération" not in a.text for a in articles)


def test_an_article_letter_must_match_its_document_section():
    """The literal "F1" in "F1 Car" appears on nearly every page of the Sporting
    regulations and would otherwise open a Section F article inside Section B."""
    articles = _split(SPORTING, _doc(section="B"))
    assert all(a.article.startswith("B") for a in articles)
    assert not any(a.article == "F1" for a in articles)


def test_every_article_carries_a_usable_citation():
    articles = _split(SPORTING, _doc())
    citation = articles[0].citation
    assert "Article" in citation and "2026" in citation and "issue 8" in citation


def test_issue_is_part_of_the_record():
    """Section B ran to eight issues during 2026. Answering today's question
    from issue 04 is quoting a rule that has since changed."""
    old = _split(SPORTING, _doc(issue=4))[0]
    new = _split(SPORTING, _doc(issue=8))[0]
    assert old.issue == 4 and new.issue == 8
    assert old.citation != new.citation


def test_known_sections_are_labelled():
    assert SECTIONS["B"] == "Sporting"
    assert SECTIONS["C"] == "Technical"


# ── Retrieval weighting ──────────────────────────────────────────────────────


def test_body_text_outweighs_headings_in_the_search_index():
    """Regression guard on a measured result.

    The original weighting favoured headings 5:1 over body text and scored 1 of
    6 on a small question set. "How many power unit elements before a grid
    penalty" returned "Power Unit Dynamometer" — a heading containing the words,
    beating the rule that answers the question. Demoting headings and promoting
    body took the same set to 6 of 6.

    Article keeps a high weight because "what does B8.2.8 say" is a lookup, not
    a search, and should return B8.2.8.
    """
    from app.services.storage import REGULATIONS_TEXT_WEIGHTS as weights

    assert weights["text"] > weights["heading"], "headings outweigh body again"
    assert weights["heading"] == 1, "heading weight was raised again"
    assert weights["text"] == 3, "body weight was changed"
    # The lookup case must keep priority over both.
    assert weights["article"] == 5


# ── The search endpoint ──────────────────────────────────────────────────────


class FakeStore:
    """Records what the route asked the store for."""

    def __init__(self, hits=None):
        self.hits = hits or []
        self.calls = []

    async def search_regulations(self, query, season=None, limit=5):
        self.calls.append({"query": query, "season": season, "limit": limit})
        return self.hits


async def test_the_search_route_passes_the_question_through_untouched():
    """The corpus is lexical: the caller's exact words are the query. Anything
    this route did to them — stripping, rewriting — would change what matched
    without the caller being able to see it."""
    from app.routers.data import regulations_search

    store = FakeStore([{"article": "B5.13.1", "score": 9.0}])
    hits = await regulations_search(
        q="when is the safety car deployed", season=2026, limit=6, store=store
    )

    assert store.calls == [
        {"query": "when is the safety car deployed", "season": 2026, "limit": 6}
    ]
    assert hits[0]["article"] == "B5.13.1"


async def test_the_search_route_returns_the_score_it_was_given():
    """core-api decides which hits are good enough to hand a language model, and
    it cannot do that if the ranking signal is dropped on the way out."""
    from app.routers.data import regulations_search

    hits = await regulations_search(
        q="parc ferme", season=None, limit=5,
        store=FakeStore([{"article": "B1.1", "score": 3.25}]),
    )

    assert hits[0]["score"] == 3.25


def test_the_search_route_is_declared_before_the_generic_dataset_handler():
    """``/{dataset}`` is a catch-all. It does not shadow this route today — two
    segments against one — but the ordering is what keeps that true if either
    path ever changes shape."""
    import inspect

    from app.routers import data

    source = inspect.getsource(data)
    assert source.index("/regulations/search") < source.index('"/{dataset}"')


# ── Naming an article is a lookup, not a search ──────────────────────────────


def test_an_article_reference_is_recognised_in_a_question():
    from app.services.storage import _article_references

    assert _article_references("what does B8.2.8 say") == ["B8.2.8"]
    assert _article_references("compare C10.7.2 and A3.3.1") == ["C10.7.2", "A3.3.1"]


def test_a_bare_section_header_counts_as_a_reference():
    from app.services.storage import _article_references

    assert _article_references("what is in C2") == ["C2"]


def test_a_lowercase_reference_is_normalised():
    """Article numbers are stored upper-cased; a question is typed however the
    reader types it."""
    from app.services.storage import _article_references

    assert _article_references("what does b8.2.8 say") == ["B8.2.8"]


def test_ordinary_prose_is_not_mistaken_for_a_reference():
    from app.services.storage import _article_references

    assert _article_references("who is quickest in practice today") == []
    assert _article_references("how many power unit elements are allowed") == []


def test_a_reference_is_not_matched_inside_a_longer_token():
    from app.services.storage import _article_references

    assert _article_references("the VF5.2 chassis") == []


def test_a_repeated_reference_is_asked_for_once():
    from app.services.storage import _article_references

    assert _article_references("does B8.2.8 contradict B8.2.8") == ["B8.2.8"]


def test_the_named_article_outranks_the_fuzzy_hits():
    """Measured against the real corpus, not assumed. The text index splits
    ``B8.2.8`` into ``b8``, ``2`` and ``8``, which match every article sharing
    those numbers — so despite ``article`` carrying the heaviest index weight,
    asking "what does B8.2.8 say" returned B8.2.5, F5.2.7 and C10.7.2 ahead of
    it, and with only the top three passages carried it was dropped entirely.
    """
    import asyncio

    from app.services.storage import IngestionStore

    fuzzy = [
        {"section": "B", "article": "B8.2.5", "score": 10.32},
        {"section": "F", "article": "F5.2.7", "score": 10.28},
    ]
    named = {"section": "B", "article": "B8.2.8", "text": "The rule."}

    ranked = asyncio.run(
        _FakeMongo(exact=[named], text=fuzzy).search("what does B8.2.8 say")
    )

    assert ranked[0]["article"] == "B8.2.8"
    assert ranked[0]["score"] > fuzzy[0]["score"]


def test_a_named_article_is_not_also_listed_among_the_fuzzy_hits():
    """It ranks twice otherwise, spending two of the three passage slots on one
    article."""
    import asyncio

    named = {"section": "B", "article": "B8.2.8", "text": "The rule."}
    ranked = asyncio.run(
        _FakeMongo(
            exact=[named],
            text=[{"section": "B", "article": "B8.2.8", "score": 9.16},
                  {"section": "B", "article": "B8.2.5", "score": 10.32}],
        ).search("what does B8.2.8 say")
    )

    assert [d["article"] for d in ranked] == ["B8.2.8", "B8.2.5"]


def test_a_question_naming_no_article_is_left_as_a_pure_text_search():
    import asyncio

    text = [{"section": "B", "article": "B5.13.1", "score": 16.85}]
    ranked = asyncio.run(
        _FakeMongo(exact=[], text=text).search("when is the safety car deployed")
    )

    assert ranked == text


class _FakeMongo:
    """The narrow slice of the motor API ``search_regulations`` drives.

    Enough to exercise the ranking, which is the part with judgement in it. The
    queries themselves are checked against the live corpus rather than here.
    """

    def __init__(self, exact, text):
        self._exact = exact
        self._text = text

    def __getitem__(self, _collection):
        return self

    def find(self, criteria, projection=None):
        rows = self._text if "$text" in criteria else self._exact
        return _FakeCursor(rows)

    async def search(self, query, season=2026, limit=6):
        from app.services.storage import IngestionStore

        return await IngestionStore(self).search_regulations(
            query, season=season, limit=limit
        )


class _FakeCursor:
    def __init__(self, rows):
        self._rows = [dict(row) for row in rows]

    def sort(self, _spec):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield row
        return gen()


# ── Cross-references that a line wrap pushed to the start of a line ──────────
#
# The regulations cite themselves constantly and pdfplumber returns the PDF's
# visual lines, so a wrap inside a citation puts an article number at the start
# of a line looking exactly like a header. Every fixture below is real text from
# the 2026 sections, kept at its original wrap points.


#: Section B issue 08. The wrap after "Articles B8.2.2," opened a B8.2.3 that
#: swallowed the rest of the sentence, leaving B8.2.8 — the rule imposing grid
#: penalties for extra power unit elements — truncated at 92 characters.
WRAPPED_CITATION = """
B8.2.7 If a driver is replaced at any time during the Championship their replacement will be deemed to be
the original driver for the purposes of assessing Power Unit usage.
B8.2.8 Should a driver use more Power Unit elements than the numbers prescribed in Articles B8.2.2,
B8.2.3 and B8.2.4 of any one of the elements during a Championship, a grid place penalty will be
imposed upon them at the first Competition during which each additional element is used.
"""


def test_a_wrapped_citation_does_not_open_an_article():
    articles = {a.article: a for a in _split(WRAPPED_CITATION, _doc())}

    assert set(articles) == {"B8.2.7", "B8.2.8"}


def test_the_article_broken_by_the_wrap_keeps_its_whole_rule():
    """The failure that motivated this: asked how many power unit elements a
    driver may use before a grid penalty, Bernie retrieved the fragment and
    said the facts did not include the penalty rule — which the corpus held all
    along, in the half of B8.2.8 that had been cut off."""
    articles = {a.article: a for a in _split(WRAPPED_CITATION, _doc())}

    text = articles["B8.2.8"].text
    assert text.startswith("Should a driver use more Power Unit elements")
    assert "a grid place penalty will be imposed upon them" in text


def test_a_wrapped_appendix_reference_does_not_open_an_article():
    """Section A: "at Appendices A4 or A5)" wrapped after "Appendices"."""
    text = """
A3.2 Entry applications
If any information given in an entry application (including in the forms at Appendices
A4 or A5) changes after the initial submission, the F1 Team must inform the FIA.
"""
    assert [a.article for a in _split(text, _doc(section="A", issue=3))] == ["A3.2"]


def test_a_wrapped_sub_point_reference_does_not_open_an_article():
    """Section C cites sub-points — "Article C3.10.10 (l) and (n)" — so the
    text after a wrapped number can open with a bracket rather than a word."""
    text = """
C3.10.11 For the Front Wing or Nose the combined range of deviation defined in
C3.10.10 (l) and (n).
"""
    assert [a.article for a in _split(text, _doc(section="C", issue=20))] == ["C3.10.11"]


def test_an_article_whose_own_text_begins_lowercase_is_kept():
    """C15.8.8 really does begin "exhaust insulation may not use..." in the
    FIA's own prose. Rejecting every number followed by a lowercase word would
    be simpler and would lose it, which is why the preceding line having
    finished its sentence is the other half of the test.
    """
    text = """
C15.8.7 The maximum dimension of this additive element for transition to TC and
Wastegate mounting in any direction is 150mm.
C15.8.8 exhaust insulation may not use ceramic matrix composite (CMC) or polymer
composite material (PMC).
"""
    articles = {a.article: a for a in _split(text, _doc(section="C", issue=20))}

    assert "C15.8.8" in articles
    assert articles["C15.8.8"].text.startswith("exhaust insulation")


def test_a_genuine_article_after_an_unfinished_line_is_still_opened():
    """A heading line ends on an ordinary word, exactly as a wrapped line does.
    So "the previous line did not finish a sentence" cannot on its own mean the
    next number is a citation — every article that follows its own heading
    would be swallowed into it."""
    text = """
B1.7 Changes Of Driver
B1.7.1 During a Championship each Competitor will be permitted to use a maximum
of four drivers, and any new driver may score points in the Championship.
"""
    articles = {a.article: a for a in _split(text, _doc())}

    assert "B1.7.1" in articles
    assert articles["B1.7.1"].text.startswith("During a Championship")


def test_the_guard_needs_both_halves():
    from app.services.regulations import _is_cross_reference

    # A citation: unfinished line, and the remainder cannot open an article.
    assert _is_cross_reference("prescribed in Articles B8.2.2,", "and B8.2.4 of any")
    # Finished sentence before it — C15.8.8's case.
    assert not _is_cross_reference("in any direction is 150mm.", "exhaust insulation")
    # A heading before it, and a remainder that reads like a rule.
    assert not _is_cross_reference("B1.7 Changes Of Driver", "During a Championship")


# ── Re-ingesting replaces, rather than accumulating ─────────────────────────


class _FakeRegulations:
    """The slice of the collection ``save_regulations`` drives."""

    def __init__(self, existing):
        self.docs = {d["_id"]: d for d in existing}
        self.deleted = []

    def __getitem__(self, _collection):
        return self

    async def bulk_write(self, operations, ordered=True):
        import types
        for op in operations:
            doc = op._doc
            self.docs[doc["_id"]] = doc
        return types.SimpleNamespace(
            upserted_count=len(operations), modified_count=0
        )

    async def delete_many(self, criteria):
        import types
        sections = set(criteria["section"]["$in"])
        keep = set(criteria["_id"]["$nin"])
        doomed = [
            _id for _id, d in self.docs.items()
            if d["season"] == criteria["season"]
            and d["section"] in sections
            and _id not in keep
        ]
        for _id in doomed:
            del self.docs[_id]
        self.deleted.extend(doomed)
        return types.SimpleNamespace(deleted_count=len(doomed))


def _stored(season, section, article):
    return {"_id": "{}-{}-{}".format(season, section, article),
            "season": season, "section": section, "article": article}


async def test_an_article_the_source_no_longer_has_is_removed():
    """A chunker fix that stops emitting a phantom article must be able to
    unpublish it. Upserting alone left C3.1.1 — a cross-reference mistaken for
    a header — searchable after the re-ingest that corrected it."""
    from app.services.storage import IngestionStore

    db = _FakeRegulations([_stored(2026, "C", "C3.1"), _stored(2026, "C", "C3.1.1")])
    await IngestionStore(db).save_regulations([
        Article(season=2026, section="C", issue=20, published="2026-08-05",
                article="C3.1", heading="H", text="T", url="u"),
    ])

    assert "2026-C-C3.1.1" in db.deleted
    assert "2026-C-C3.1" in db.docs


async def test_sections_absent_from_the_payload_are_untouched():
    """Re-ingesting Sporting alone must not delete Technical."""
    from app.services.storage import IngestionStore

    db = _FakeRegulations([_stored(2026, "B", "B1.1"), _stored(2026, "C", "C3.1")])
    await IngestionStore(db).save_regulations([
        Article(season=2026, section="B", issue=8, published="2026-08-05",
                article="B1.2", heading="H", text="T", url="u"),
    ])

    assert "2026-C-C3.1" in db.docs
    assert "2026-B-B1.1" in db.deleted


async def test_an_empty_payload_deletes_nothing():
    """A failed fetch returns no articles. Treating that as "the section is now
    empty" would wipe the corpus on an FIA outage."""
    from app.services.storage import IngestionStore

    db = _FakeRegulations([_stored(2026, "B", "B1.1")])
    assert await IngestionStore(db).save_regulations([]) == 0
    assert db.deleted == []


def test_a_table_garble_does_not_beat_the_real_article_on_length():
    """Multi-column pages extract as interleaved fragments that can run longer
    than the rule itself. C6.6.6 was stored as column salad while its actual
    text, a hundred characters shorter, lost the length comparison."""
    text = """
C6.6.6 The pressure of the fuel inside the collector may be increased relative to
the pressure in the fuel cell volume, provided this is done for safety reasons.
SECTION C: TECHNICAL REGULATIONS
C6.6.6 pressurisation system if including any fitted. pressurisation system, any
local electrical and electronic components, level sensor, filters, AV mounts and
mounting fasteners 6B Primer C6.6.2,C SSC Primer pump(s), and Primer pump(s)
"""
    articles = {a.article: a for a in _split(text, _doc(section="C", issue=20))}

    assert articles["C6.6.6"].text.startswith("The pressure of the fuel")
