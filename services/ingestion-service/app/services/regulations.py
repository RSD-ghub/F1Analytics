"""The FIA regulations, chunked by article so every answer can cite one.

The one corpus here that genuinely wants retrieval. Event documents are forms —
driver, fact, decision, reason in labelled fields — and parsing a form beats
searching it. The regulations are prose: several hundred pages across six
sections, where answering "may a team change a driver before qualifying" means
*finding* the passage rather than reading a field.

**Chunked on article boundaries, not by character count.** Regulations are
numbered ``B1.7.2``, and that numbering is both a natural semantic boundary and
a citation. Chunking by token window would split a rule mid-sentence and leave
Bernie quoting half of one; chunking by article means every passage she is given
arrives with the reference that identifies it. For a product whose promise is
that claims trace to sources, retrieval without citation would be worse than no
retrieval.

**Issue-aware.** The FIA reissues sections constantly — Section B Sporting ran to
eight issues during 2026 alone — so a chunk carries its section, issue and date.
Answering today's question from issue 04 would be quoting a rule that has since
changed.

Search is MongoDB's text index rather than embeddings, deliberately and for now.
Regulation vocabulary is precise and unusual — parc fermé, power unit element,
Stop-and-Go — which is exactly where lexical matching is strong and where a
semantic model's paraphrases are least needed. It also needs no embedding
provider, no vector store, and no new dependency. If retrieval quality turns out
to be the limit, embeddings can be layered on later over these same chunks; the
chunking is the part that would have to be right either way.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

REGULATIONS_INDEX = "https://www.fia.com/regulation/category/110"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125"

#: Section letter -> what it governs. Used to label chunks readably and to skip
#: the sections nobody will ask Bernie about.
SECTIONS = {
    "A": "General Provisions",
    "B": "Sporting",
    "C": "Technical",
    "D": "Financial (Teams)",
    "E": "Financial (PU Manufacturers)",
    "F": "Operational",
}

#: The sections worth ingesting for a strategist. Financial regulations govern
#: cost caps and reporting, which no race question touches.
DEFAULT_SECTIONS = ("A", "B", "C", "F")

#: ``B1.7.2 Provided any change proposed...`` — the section letter is part of the
#: article number, which is why a bare-digit pattern finds nothing here.
_ARTICLE = re.compile(r"^([A-F]\d+(?:\.\d+)*)\s+(.*)$")

#: What follows the number, when the line is not an article header at all.
#:
#: An article opens with a heading or a sentence, so it begins with a capital.
#: A lowercase word — or a bracketed sub-point — means the "number" is a
#: cross-reference that a line wrap happened to push to the start of a line.
_REST_CONTINUES = re.compile(r"^[a-z(),]")

#: A line that has not finished its sentence: it ends on a comma or on one of
#: the words that introduce a cross-reference, which is where the wrap lands.
_PREV_CONTINUES = re.compile(
    r"(,|;|\b(and|or|in|of|to|under|with|from|Articles?|Appendix|Appendices)\b)$"
)

#: Running headers and footers repeated on every page. They carry no rule text
#: and, left in, attach a copyright line to whichever article spans the break.
_PAGE_FURNITURE = re.compile(
    r"©\s*\d{4}|Fédération Internationale|^Issue \d+$|^SECTION [A-F]:|^\d{1,3}$",
    re.IGNORECASE,
)

#: ``fia_2026_f1_regulations_-_section_b_sporting_-_iss_08_-_2026-08-05_7.pdf``
_FILENAME = re.compile(
    r"fia_(\d{4})_f1_regulations_-_section_([a-f])_[a-z_()]*?-_iss[_]?(\d+)_-_(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


class RegulationsUnavailable(RuntimeError):
    """The index or a document could not be read."""


@dataclass(frozen=True)
class RegulationDocument:
    season: int
    section: str
    issue: int
    published: str
    url: str

    @property
    def label(self) -> str:
        return "{} Section {} ({}) issue {}".format(
            self.season, self.section, SECTIONS.get(self.section, "?"), self.issue
        )


@dataclass(frozen=True)
class Article:
    """One numbered article. The unit of both storage and citation."""

    season: int
    section: str
    issue: int
    published: str
    article: str
    heading: str
    text: str
    url: str

    @property
    def citation(self) -> str:
        return "Article {} ({} Section {}, issue {})".format(
            self.article, self.season, self.section, self.issue
        )


async def discover(season: int, timeout_seconds: float = 60.0) -> List[RegulationDocument]:
    """Latest issue of each section for a season.

    The index lists every issue ever published — Section B had eight in 2026 —
    so this keeps only the highest per section. Quoting a superseded issue is
    not a smaller mistake than quoting nothing.
    """
    async with httpx.AsyncClient(
        timeout=timeout_seconds, follow_redirects=True,
        headers={"User-Agent": BROWSER_UA},
    ) as client:
        try:
            response = await client.get(REGULATIONS_INDEX)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RegulationsUnavailable(
                "could not read the FIA regulations index: {}".format(exc)
            ) from exc

    best: Dict[str, RegulationDocument] = {}
    for href in re.findall(r'href="([^"]+\.pdf)"', response.text):
        found = _FILENAME.search(href)
        if not found:
            continue
        year, section, issue, published = (
            int(found.group(1)), found.group(2).upper(),
            int(found.group(3)), found.group(4),
        )
        if year != season:
            continue
        url = href if href.startswith("http") else "https://www.fia.com" + href
        current = best.get(section)
        if current is None or issue > current.issue:
            best[section] = RegulationDocument(
                season=season, section=section, issue=issue,
                published=published, url=url,
            )

    if not best:
        raise RegulationsUnavailable(
            "no {} regulation sections found in the index".format(season)
        )
    return [best[key] for key in sorted(best)]


async def fetch_articles(
    document: RegulationDocument, timeout_seconds: float = 120.0
) -> List[Article]:
    """Download one section and split it into articles."""
    async with httpx.AsyncClient(
        timeout=timeout_seconds, follow_redirects=True,
        headers={"User-Agent": BROWSER_UA},
    ) as client:
        response = await client.get(document.url)
        response.raise_for_status()
        payload = response.content

    try:
        import pdfplumber
        from io import BytesIO

        with pdfplumber.open(BytesIO(payload)) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:
        raise RegulationsUnavailable(
            "could not read {}: {}".format(document.label, exc)
        ) from exc

    articles = _split(text, document)
    logger.info("%s -> %d articles", document.label, len(articles))
    return articles


def _is_cross_reference(previous: str, rest: str) -> bool:
    """Is this "article number" really a reference that a line wrap moved?

    The regulations cite themselves constantly, and ``pdfplumber`` returns the
    PDF's visual lines, so a wrap inside "prescribed in Articles B8.2.2, B8.2.3
    and B8.2.4" puts B8.2.3 at the start of a line looking exactly like a
    header. Left unguarded that truncated B8.2.8 — the rule imposing grid
    penalties for extra power unit elements — and stored its tail under B8.2.3.

    Two conditions, and both are needed. The text after the number must be
    unable to open an article: articles begin with a heading or a sentence, so a
    lowercase word or a bracketed sub-point means the number is being cited
    rather than declared. And the previous line must not have finished its
    sentence.

    Requiring both is what keeps C15.8.8 — whose text genuinely begins "exhaust
    insulation may not use..." in the FIA's own prose — while still rejecting
    every real wrap. Measured across all four 2026 sections, this drops 21
    false boundaries and no true ones.
    """
    return bool(_REST_CONTINUES.match(rest)) and bool(_PREV_CONTINUES.search(previous))


def _split(text: str, document: RegulationDocument) -> List[Article]:
    """Break a section into numbered articles.

    Everything before the first article number is front matter — contents pages,
    headers — and is dropped rather than attached to article one.
    """
    articles: List[Article] = []
    number: Optional[str] = None
    heading = ""
    body: List[str] = []

    def flush() -> None:
        if number is None:
            return
        content = " ".join(part.strip() for part in body if part.strip())
        if not content and not heading:
            return
        articles.append(
            Article(
                season=document.season, section=document.section,
                issue=document.issue, published=document.published,
                article=number, heading=heading,
                text=(heading + " " + content).strip(), url=document.url,
            )
        )

    previous = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) == 1 or _PAGE_FURNITURE.search(line):
            continue
        # The line before this one, skipped furniture ignored, so that a wrap
        # across a page break still reads as the wrap it is. Rebound here rather
        # than at each exit so a later `continue` cannot silently stale it.
        previous, line_before = line, previous
        match = _ARTICLE.match(line)
        # The article letter must be this document's section. Without that check
        # the literal "F1" in "F1 Car" opens a Section F article inside the
        # Sporting regulations — the phrase appears on nearly every page.
        if match and match.group(1)[0] != document.section:
            match = None
        if match and _is_cross_reference(line_before, match.group(2).strip()):
            match = None
        if match:
            flush()
            number = match.group(1)
            rest = match.group(2).strip()
            # A short Title Case remainder is the article's heading; anything
            # longer is the rule itself starting on the same line.
            if rest and len(rest) < 70 and rest[:1].isupper() and not rest.endswith("."):
                heading, body = rest, []
            else:
                heading, body = "", [rest]
            continue
        if number is not None:
            body.append(line)

    flush()
    return _drop_contents_entries(articles)


#: A contents line: a heading followed by the page it appears on, sometimes with
#: the next section's heading run onto the end. Real articles do not end in a
#: bare page number.
_CONTENTS_LINE = re.compile(r"\s\d{1,3}$")


def _drop_contents_entries(articles: Sequence[Article]) -> List[Article]:
    """Discard the table of contents, which parses as articles but is not.

    Every section opens with a contents listing whose lines look exactly like
    article headings — "B1.6 Pit Entry Road, Pit Lane And Pit Exit Road 8" — the
    trailing number being a page reference. Left in, they duplicate real article
    numbers with text that is a heading and a page number, so a search for "pit
    lane" could return the contents entry rather than the rule.

    Both problems are solved by keeping, for each article number, the fullest
    version seen: the real article always carries more text than its own
    contents line. See ``preference`` for why that is ordered on prose before
    length.
    """
    def preference(article: Article):
        # Prose first, then length.
        #
        # Length alone is usually right — a real article carries more text than
        # its own contents line — but it loses when a page of multi-column
        # tables extracts as interleaved fragments, which can run longer than
        # the rule itself. C6.6.6 was stored as "pressurisation system if
        # including any fitted. pressurisation system, any local electrical..."
        # while its actual text, "The pressure of the fuel inside the collector
        # may be increased...", sat a hundred characters shorter and lost.
        #
        # A rule opens with a capital; column salad starts wherever the column
        # was cut. That is enough to separate them when both versions exist. It
        # does not repair table extraction, and where every version of an
        # article is garbled this changes nothing.
        return (article.text[:1].isupper(), len(article.text))

    longest: Dict[str, Article] = {}
    for article in articles:
        if len(article.text) <= 40:
            continue
        current = longest.get(article.article)
        if current is None or preference(article) > preference(current):
            longest[article.article] = article

    def sort_key(article: Article):
        # Numeric ordering, so B10.2 follows B9 rather than B1.
        return [int(part) for part in article.article[1:].split(".") if part.isdigit()]

    kept = [a for a in longest.values() if not _CONTENTS_LINE.search(a.text.strip())]
    return sorted(kept, key=sort_key)
