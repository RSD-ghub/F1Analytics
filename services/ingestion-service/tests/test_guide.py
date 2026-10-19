"""The strategy guide: reading a book into citable passages, and finding them.

The text here is written for the tests in the shape of an ebook export — the
real book is copyrighted and never enters the repository.
"""

import pytest

from app.guide.retrieval import Index, expand, tokens
from app.guide.source import GuideUnreadable, MAX_CHARS, parse_book

BOOK = """
Front matter that is not part of any chapter.
Copyright © 2024 Somebody

T
Chapter 1
OPTIMUM
PLANNING
he tyre model is the foundation for all strategy. Degradation and the life of
each tyre are built into it. We would work out the fastest race for a single
car, then set that aside.

The next step is a twenty-car race. Track position is key where it is
difficult to overtake, and you will do a sub-optimal race to keep it. The in-‐
house view was that a stop fewer is worth it at Monaco.

W
Chapter 2
WE’RE OFF!
hen the safety car window opens, you box straight away. Under a safety car
the time lost in the pits is about half, so fewer positions are lost.
If you stay out but everybody else makes a stop, what does that look like?
Acknowledgements
Thanks to everyone who is not a strategy passage.
"""


def _passages():
    return parse_book(BOOK, "A Strategy Book", "A. Strategist")


# ── Reading the book ─────────────────────────────────────────────────────────


def test_chapters_are_found_with_their_titles():
    chapters = {(p.chapter, p.chapter_title) for p in _passages()}
    assert chapters == {(1, "Optimum Planning"), (2, "We’re Off!")}


def test_the_drop_cap_is_put_back_on_the_first_word():
    """The export strands each chapter's first letter above its heading: left
    alone, chapter one would open "he tyre model"."""
    first = [p for p in _passages() if p.index == 0]
    assert first[0].text.startswith("The tyre model")
    assert first[1].text.startswith("When the safety car window opens")


def test_front_and_back_matter_are_not_passages():
    text = " ".join(p.text for p in _passages())
    assert "Copyright" not in text
    assert "Front matter" not in text
    assert "Thanks to everyone" not in text


def test_the_next_chapters_drop_cap_does_not_end_this_one():
    assert not any(p.text.rstrip().endswith(" W") for p in _passages())


def test_lines_are_rejoined_and_hyphenated_words_mended():
    text = " ".join(p.text for p in _passages())
    assert "single car, then" in text
    assert "in-house" in text
    assert "\n" not in text


def test_passages_are_whole_sentences_and_bounded():
    for passage in _passages():
        assert passage.text[-1] in ".!?’”'\"", passage.text[-30:]
        assert len(passage.text) <= MAX_CHARS + 400


def test_a_long_chapter_is_split_with_overlap():
    sentences = " ".join(
        "Sentence {} is about tyre degradation and pit windows here.".format(i)
        for i in range(60)
    )
    book = "X\nChapter 3\nTYRE TALK\n" + sentences
    passages = parse_book(book, "T", "A")
    assert len(passages) > 2
    # One sentence carried over, so an argument spanning a boundary survives.
    last_of_first = passages[0].text.rsplit(". ", 1)[-1]
    assert passages[1].text.startswith(last_of_first.rstrip("."))


def test_passage_keys_are_stable_and_unique():
    keys = [p.key for p in _passages()]
    assert len(keys) == len(set(keys))
    assert keys[0] == "a-strategy-book-01-000"


def test_a_file_with_no_chapters_is_refused():
    with pytest.raises(GuideUnreadable):
        parse_book("just some notes\nwith no chapters", "T", "A")


# ── Finding passages ─────────────────────────────────────────────────────────


def _index():
    return Index.build([p.to_document() for p in _passages()])


def test_the_pit_wall_shorthand_finds_the_book_words():
    """The book says "safety car"; a reader types "SC"."""
    assert "safety" in expand("when do we box under an SC")
    hits = _index().search("SC")
    assert hits and hits[0]["chapter"] == 2


def test_inflections_meet():
    assert tokens("stopping")[0] == tokens("stop")[0]
    assert tokens("tyres")[0] == tokens("tyre")[0]


def test_the_best_passage_answers_the_question():
    hits = _index().search("is it hard to overtake, should we keep track position")
    assert hits[0]["chapter"] == 1
    assert "Track position is key" in hits[0]["text"]


def test_hits_carry_a_score_and_their_citation():
    hit = _index().search("safety car window")[0]
    assert hit["score"] > 0
    assert hit["title"] == "A Strategy Book"
    assert hit["chapter_title"] == "We’re Off!"
    assert "_id" not in hit


def test_a_question_the_book_does_not_touch_finds_nothing():
    assert _index().search("qwertyuiop zxcvbnm") == []


def test_search_can_be_restricted_to_chapters():
    hits = _index().search("safety car", chapters=[1])
    assert all(h["chapter"] == 1 for h in hits)


# ── The route ────────────────────────────────────────────────────────────────


class FakeStore:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    async def search(self, query, limit=5, chapters=()):
        self.calls.append({"query": query, "limit": limit, "chapters": chapters})
        return self.hits


async def test_the_search_route_passes_the_question_and_returns_scores():
    from app.guide.router import search

    store = FakeStore([{"chapter": 15, "text": "Track position is key.", "score": 7.5}])
    hits = await search(q="track position", limit=3, chapter=[15], store=store)
    assert store.calls == [{"query": "track position", "limit": 3, "chapters": [15]}]
    assert hits[0]["score"] == 7.5
