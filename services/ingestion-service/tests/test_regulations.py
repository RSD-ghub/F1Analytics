"""Chunking the FIA regulations by article.

The unit of storage is the numbered article, because that is simultaneously a
semantic boundary and a citation. A token-window chunker would split a rule
mid-sentence and hand Bernie half of one; article chunks arrive with the
reference that identifies them, which for a product promising that claims trace
to sources is the difference between usable retrieval and none.
"""

import pytest

from app.services.regulations import (
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
