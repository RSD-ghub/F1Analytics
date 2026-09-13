"""Which constructor entries are the same organisation.

The one asserted input in the circuit-character work, and unavoidably so. A
team's record at adapting to regulation resets is only measurable across
transitions, and Formula 1 constructors are renamed, rebranded and sold
constantly — Racing Point and Aston Martin are the same factory, the same wind
tunnel and largely the same engineers, but nothing in a results table says so.
Treat a rename as a new team and the feature measures nothing, because a new
team has no prior transitions to average.

There is no way to derive corporate continuity from lap times, so this is a
hand-maintained map. It is small, it changes about once a year, and it is
deliberately kept in one obvious place rather than smeared through the feature
code.

**Continuity here means the organisation, not the name or the owner.** Sauber
has been Sauber, BMW Sauber, Alfa Romeo, Kick Sauber and now Audi while staying
the same operation in Hinwil, so it is one lineage. Lotus is the awkward case
and is split: the 2010-2011 Lotus that became Caterham is a different entry from
the 2012-2015 Lotus F1 that was Renault's Enstone team, and merging them would
attribute one organisation's history to another.
"""

from typing import Dict

#: Constructor name as it appears in results -> stable lineage id.
#: Names are matched case-insensitively after whitespace collapsing; anything
#: absent falls back to its own normalised name, so a new entrant is simply its
#: own lineage rather than an error.
_LINEAGE: Dict[str, str] = {
    # Enstone: Renault -> Lotus F1 -> Renault -> Alpine
    "lotus f1": "enstone",
    "renault": "enstone",
    "alpine": "enstone",
    # Hinwil: Sauber -> BMW -> Sauber -> Alfa Romeo -> Kick Sauber -> Audi
    "sauber": "hinwil",
    "alfa romeo": "hinwil",
    "alfa romeo racing": "hinwil",
    "kick sauber": "hinwil",
    "audi": "hinwil",
    # Silverstone: Force India -> Racing Point -> Aston Martin
    "force india": "silverstone_am",
    "racing point": "silverstone_am",
    "aston martin": "silverstone_am",
    # Faenza: Toro Rosso -> AlphaTauri -> RB -> Racing Bulls
    "toro rosso": "faenza",
    "alphatauri": "faenza",
    "rb": "faenza",
    "racing bulls": "faenza",
    # Milton Keynes
    "red bull": "red_bull",
    "red bull racing": "red_bull",
    # Banbury: Virgin -> Marussia -> Manor
    "virgin": "manor",
    "marussia": "manor",
    "manor marussia": "manor",
    # Leafield: the 2010-2011 Lotus entry, which became Caterham. Deliberately
    # NOT merged with "lotus f1" above — different organisation, same name.
    "lotus": "caterham",
    "caterham": "caterham",
    # Unchanged through the corpus, listed so the map is a complete picture
    # rather than a list of exceptions.
    "ferrari": "ferrari",
    "mclaren": "mclaren",
    "mercedes": "mercedes",
    "williams": "williams",
    "haas f1 team": "haas",
    "hrt": "hrt",
    "cadillac": "cadillac",
}


def normalise(team: str) -> str:
    return " ".join((team or "").split()).lower()


def lineage_of(team: str) -> str:
    """The organisation a constructor entry belongs to.

    An unknown name becomes its own lineage rather than raising: a genuinely new
    entrant has no history to inherit, which is the correct answer, and a
    misspelling should cost one team's continuity rather than a whole ingest.
    """
    key = normalise(team)
    return _LINEAGE.get(key, key or "unknown")


def same_organisation(left: str, right: str) -> bool:
    return lineage_of(left) == lineage_of(right)
