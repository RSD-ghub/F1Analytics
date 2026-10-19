"""Ranking guide passages against a question: BM25 with a strategist's vocabulary.

Why not the Mongo text index the regulations use. Regulation vocabulary is
precise and repeated — the question says "parc fermé" and so does the rule —
which is where a lexical index shines. A strategist's book is prose: the
question says "undercut" and the passage says "stopping a lap earlier"; the
question says "SC" and the book says "safety car". So the ranking here expands
the question with the pit wall's synonyms before scoring, which a text index
cannot be told to do.

Why not embeddings. The corpus is one book, about five hundred passages; BM25
over it runs in milliseconds in-process, needs no provider, no vector store and
no model download on a 4 GB VM, and its ranking can be explained by pointing at
the words that matched. If a second or third book arrives and recall becomes
the limit, the passages are already the unit an embedding would be computed
over.

Pure functions over plain dicts, so the store can cache an index and the tests
can rank without a database.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

#: BM25 parameters, the textbook defaults. Passages are near-uniform in length
#: by construction, so ``B`` matters less here than usual.
K1 = 1.4
B = 0.6

#: Expansions applied to the *question* only. Each key, when it appears in a
#: question, adds its values as extra query terms. Multi-word phrases on either
#: side are fine: both are tokenised.
SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "sc": ("safety car",),
    "vsc": ("virtual safety car",),
    "deg": ("degradation",),
    "degradation": ("deg", "wear"),
    "wear": ("degradation",),
    "undercut": ("stop earlier", "fresh tyres", "pit window"),
    "overcut": ("going long", "long first stint", "stay out"),
    "long": ("long first stint",),
    "lap one": ("first lap",),
    "lap 1": ("first lap",),
    "pit": ("stop", "box"),
    "box": ("pit", "stop"),
    "stop": ("pit",),
    "strategy": ("plan",),
    "plan": ("strategy",),
    "traffic": ("clear air", "rejoin"),
    "rejoin": ("traffic", "emerge"),
    "overtake": ("overtaking", "track position"),
    "overtaking": ("track position",),
    "position": ("track position",),
    "start": ("launch", "first lap", "start tyre"),
    "soft": ("tyre",),
    "medium": ("tyre",),
    "hard": ("tyre",),
    "compound": ("tyre",),
    "rain": ("wet", "intermediate", "weather"),
    "wet": ("rain", "intermediate"),
    "inter": ("intermediate",),
    "teammate": ("two drivers", "both cars"),
    "stack": ("stacking", "both cars"),
    "quali": ("qualifying",),
    "q3": ("qualifying",),
    "one-stop": ("one stop",),
    "two-stop": ("two stop",),
}

_STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because
been before being below between both but by can could did do does doing down
during each few for from further had has have having he her here hers him his
how i if in into is it its itself just me more most my no nor not now of off on
once only or other our ours out over own same she should so some such than that
the their theirs them then there these they this those through to too under
until up very was we were what when where which while who whom why will with
would you your yours one two get got go going say said think would like really
lot much way thing things well even still yes know
""".split())

#: Numbers carry meaning here ("lap 20", "two-stop"), so tokens keep digits.
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _stem(word: str) -> str:
    """A light suffix stripper: enough to meet "stopping" with "stop".

    Not Porter. Over-stemming merges strategy terms that mean different things
    ("tyres" -> "tyre" is wanted; "safety" -> "safeti" helps no one), so only
    the commonest inflections are removed.
    """
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        # "pushes" -> "push", but "tyres" -> "tyre", not "tyr".
        if suffix == "es" and not word.endswith(("ses", "xes", "zes", "ches", "shes")):
            continue
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)] + keep
            # "stopping" -> "stopp" -> "stop"
            if suffix in ("ing", "ed") and len(stem) > 3 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            return stem
    return word


def tokens(text: str) -> List[str]:
    found = []
    for raw in _TOKEN.findall((text or "").lower().replace("’", "'")):
        parts = [raw] + (raw.split("-") if "-" in raw else [])
        for part in parts:
            if part and part not in _STOPWORDS:
                found.append(_stem(part))
    return found


def expand(question: str) -> List[str]:
    """The question's terms plus the pit wall's synonyms for them.

    Expansion terms count once each and are capped near the question's own
    length: a two-word question should still be mostly about its two words,
    while a one-word one ("overcut") needs its synonyms to find anything.
    """
    base = tokens(question)
    lowered = " {} ".format(" ".join(_TOKEN.findall((question or "").lower())))
    extra: List[str] = []
    for key, values in SYNONYMS.items():
        if " {} ".format(key) in lowered:
            for value in values:
                extra.extend(t for t in tokens(value) if t not in base and t not in extra)
    return base + extra[: max(4, len(base))]


@dataclass
class Index:
    """BM25 statistics over a fixed set of passages."""

    passages: List[Dict[str, Any]]
    frequencies: List[Counter] = field(default_factory=list)
    lengths: List[int] = field(default_factory=list)
    document_frequency: Counter = field(default_factory=Counter)
    average_length: float = 0.0

    @classmethod
    def build(cls, passages: Iterable[Dict[str, Any]]) -> "Index":
        index = cls(passages=list(passages))
        for passage in index.passages:
            terms = tokens(" ".join([passage.get("chapter_title", ""), passage.get("text", "")]))
            counts = Counter(terms)
            index.frequencies.append(counts)
            index.lengths.append(len(terms))
            index.document_frequency.update(counts.keys())
        index.average_length = (
            sum(index.lengths) / len(index.lengths) if index.lengths else 0.0
        )
        return index

    def _idf(self, term: str) -> float:
        n, df = len(self.passages), self.document_frequency.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(
        self, question: str, limit: int = 5, chapters: Sequence[int] = ()
    ) -> List[Dict[str, Any]]:
        """Passages best matching a question, each carrying its ``score``.

        Ties keep reading order, so the earlier statement of a point wins over a
        later callback to it.
        """
        terms = expand(question)
        if not terms or not self.passages:
            return []
        weights = Counter(terms)
        scored: List[Tuple[float, int]] = []
        for i, counts in enumerate(self.frequencies):
            if chapters and self.passages[i].get("chapter") not in chapters:
                continue
            norm = K1 * (1 - B + B * self.lengths[i] / (self.average_length or 1))
            score = 0.0
            for term, weight in weights.items():
                tf = counts.get(term, 0)
                if tf:
                    score += weight * self._idf(term) * tf * (K1 + 1) / (tf + norm)
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        hits = []
        for score, i in scored[:limit]:
            hit = {k: v for k, v in self.passages[i].items() if k != "_id"}
            hit["score"] = round(score, 4)
            hits.append(hit)
        return hits
