"""The strategy guide: a strategist's book, chunked so every passage can be cited.

The regulations say what a team may do. This says how a pit wall decides what
it should do: build a tyre model, find the quickest race for a car on its own,
then put it among nineteen others and ask where it rejoins, who is in its
safety-car window and whether the circuit lets it overtake. The strategy engine
follows that order, and each of its calls is shown beside the passage that
argues for it.

**The book is not in the repository.** It is somebody's copyrighted work, so it
is read from a file the operator holds (``scripts/ingest_strategy_guide.py``)
and lives only in the database. Nothing here embeds its text.

**Chunked by chapter, then by sentence.** Unlike the regulations there is no
article number to split on; the chapter is the natural citation ("chapter 15,
Optimum planning"), and inside it passages are packed from whole sentences to
roughly a paragraph, overlapping by one sentence so a point made across a
boundary survives in at least one passage. A token window would cut sentences
in half and hand Bernie half an argument.

The parser is written against an ebook exported to text: chapters open with a
``Chapter N`` line and an upper-case title, the opening letter of each chapter
is a drop cap stranded on the line *before* ``Chapter N``, lines are hard
wrapped, page breaks are blank lines that fall mid-sentence, and a word broken
across a line carries a doubled hyphen (``in-‐`` then ``house``).
"""

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

#: ``Chapter 14`` on a line of its own.
_CHAPTER = re.compile(r"^Chapter\s+(\d+)\s*$")

#: A title line: upper case, digits and punctuation only. Titles can wrap
#: ("SUNDAY MORNING; GETTING" / "READY"), so consecutive ones are joined.
_TITLE_LINE = re.compile(r"^[A-Z0-9][A-Z0-9 ;:,.'’()&!?\-–—]*$")

#: Back matter that ends the last chapter.
_BACK_MATTER = {"Acknowledgements", "Picture Section"}

#: A word hyphenated across a line break: a hyphen followed by the Unicode
#: hyphen the export adds, or either one alone at the end of a line.
_LINE_HYPHEN = re.compile(r"[-‐]{1,2}\n(?=\S)")

#: Sentence ends: terminal punctuation, optional closing quote, then a space
#: and something that can open a sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])[’”'\"]?\s+(?=[A-Z‘“'\"(])")

#: Passage size, in characters. About a paragraph: long enough to carry an
#: argument with its reason, short enough that three of them sit comfortably
#: in a prompt beside the race facts.
TARGET_CHARS = 900
MAX_CHARS = 1400


class GuideUnreadable(ValueError):
    """The text does not look like a book this parser understands."""


@dataclass
class Passage:
    source: str
    title: str
    author: str
    chapter: int
    chapter_title: str
    index: int
    text: str

    @property
    def key(self) -> str:
        return "{}-{:02d}-{:03d}".format(self.source, self.chapter, self.index)

    def to_document(self) -> Dict[str, Any]:
        document = asdict(self)
        document["_id"] = self.key
        return document


#: Words a title keeps in lower case unless they open it.
_SMALL_WORDS = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "or", "the", "to"}


def _title_case(upper: str) -> str:
    """``WE’RE OFF!`` -> ``We’re Off!``; ``FREE PRACTICE 1 (FP1)`` keeps ``FP1``."""
    words = []
    for i, word in enumerate(upper.split()):
        bare = word.strip("()")
        if any(ch.isdigit() for ch in bare) and bare.isupper():
            words.append(word)
        elif i and word.lower() in _SMALL_WORDS:
            words.append(word.lower())
        else:
            words.append("-".join(part[:1].upper() + part[1:].lower()
                                  for part in word.split("-")))
    return " ".join(words)


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "guide"


def _clean(lines: List[str]) -> str:
    """Hard-wrapped lines back into prose."""
    text = "\n".join(line.rstrip() for line in lines)
    text = _LINE_HYPHEN.sub("-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _chapters(text: str) -> List[Dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    starts = [i for i, line in enumerate(lines) if _CHAPTER.match(line.strip())]
    if not starts:
        raise GuideUnreadable("no 'Chapter N' headings found")

    chapters = []
    for n, start in enumerate(starts):
        number = int(_CHAPTER.match(lines[start].strip()).group(1))
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)

        # The title: upper-case lines straight after the heading.
        cursor, title_lines = start + 1, []
        while cursor < end and lines[cursor].strip() and _TITLE_LINE.match(lines[cursor].strip()):
            title_lines.append(lines[cursor].strip())
            cursor += 1

        body = lines[cursor:end]
        # The next chapter's drop cap sits at the end of this one's body.
        while body and not body[-1].strip():
            body.pop()
        if n + 1 < len(starts) and body and len(body[-1].strip()) == 1:
            body.pop()
        for i, line in enumerate(body):
            if line.strip() in _BACK_MATTER:
                body = body[:i]
                break

        # This chapter's drop cap, stranded above its heading.
        cap = ""
        before = start - 1
        while before >= 0 and not lines[before].strip():
            before -= 1
        if before >= 0 and len(lines[before].strip()) == 1 and lines[before].strip().isalpha():
            cap = lines[before].strip()

        prose = _clean(body)
        if cap and prose and prose[0].islower():
            prose = cap + prose
        if prose:
            chapters.append({
                "number": number,
                "title": _title_case(" ".join(title_lines)) if title_lines else "Chapter {}".format(number),
                "text": prose,
            })
    return chapters


def _sentences(prose: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_END.split(prose) if s.strip()]


def _pack(sentences: List[str]) -> List[str]:
    """Whole sentences into passages of about ``TARGET_CHARS``, one overlapping."""
    passages: List[str] = []
    current: List[str] = []
    size = 0
    fresh = False  # whether ``current`` holds anything past the overlap
    for sentence in sentences:
        if fresh and size + len(sentence) > MAX_CHARS:
            passages.append(" ".join(current))
            current, size, fresh = current[-1:], len(current[-1]), False
        current.append(sentence)
        size += len(sentence) + 1
        fresh = True
        if size >= TARGET_CHARS:
            passages.append(" ".join(current))
            current, size, fresh = current[-1:], len(current[-1]), False
    if fresh:
        passages.append(" ".join(current))
    return passages


def parse_book(
    text: str, title: str, author: str, source: Optional[str] = None
) -> List[Passage]:
    """Split a book's text into citable passages, in reading order."""
    source = source or slug(title)
    passages: List[Passage] = []
    for chapter in _chapters(text):
        for index, chunk in enumerate(_pack(_sentences(chapter["text"]))):
            passages.append(Passage(
                source=source, title=title, author=author,
                chapter=chapter["number"], chapter_title=chapter["title"],
                index=index, text=chunk,
            ))
    if not passages:
        raise GuideUnreadable("chapters were found but held no text")
    return passages
