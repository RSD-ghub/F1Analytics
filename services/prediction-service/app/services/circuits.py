"""Circuit archetypes — grouping tracks by what they demand of a car.

Monza is eleven corners and long straights; Monaco is nineteen slow corners and
walls. A team that builds a low-drag car is strong at Monza, Spa and Baku alike,
and the model previously had no way to know those three belong together. Its
only circuit input was ``circuit_avg_finish`` — a driver's mean finish at one
exact track, resting on roughly five observations because a driver visits a
circuit about once a year.

Archetypes fix the sparsity: "tracks like this one" is a third of a career
rather than a handful of races.

**These are derived, not declared.** Clustering is fitted offline by
``scripts/derive_archetypes.py`` from corner geometry and lap times and frozen
into ``circuit_archetypes.json``, exactly as model weights are. Serving only
reads the artifact, so nothing here depends on FastF1 and a stored prediction
stays reproducible: the archetype a forecast used is pinned by the artifact
version, not recomputed from whatever the corpus looks like today.

A hand-written taxonomy was the obvious alternative and was rejected for the
same reason the model's weights are fitted rather than chosen — a curated label
is one person's opinion that the model then treats as measurement.
"""

import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

ARTIFACT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "circuit_archetypes.json"
)

#: Returned for a circuit the artifact has never seen — a new venue, or a name
#: the corpus spells differently. Neutral rather than a guess: an unrecognised
#: track should cost the archetype features, not silently assign a wrong one.
UNKNOWN = "unknown"


def normalise(circuit: str) -> str:
    return " ".join((circuit or "").split()).lower()


class CircuitArchetypes:
    """Circuit name -> archetype label, loaded from the frozen artifact."""

    def __init__(self, mapping: Dict[str, str], version: str = "", metrics=None,
                 team_mastery=None):
        self._mapping = mapping
        self.version = version
        self.metrics = metrics or {}
        # era name -> {lineage: mastery}. Keyed by era so a forecast can take
        # the value that was knowable when it was made; see the derive script.
        self._mastery = team_mastery or {}

    @classmethod
    def load(cls, path: str = ARTIFACT_PATH) -> "CircuitArchetypes":
        """Read the artifact, or return an empty mapping.

        Deliberately does *not* raise when the file is absent, unlike
        ``RaceModel.load``. Missing weights mean the model cannot make a
        forecast at all; missing archetypes mean two features go neutral and
        every other one still works. Refusing to serve over an enrichment would
        be the wrong trade — but it is logged loudly, because silently serving a
        degraded model is how a feature quietly stops contributing.
        """
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            logger.warning(
                "no circuit archetype artifact at %s; archetype features will be "
                "neutral. Run scripts/derive_archetypes.py to build it.", path
            )
            return cls({})
        except (ValueError, OSError) as exc:
            logger.error("circuit archetype artifact unreadable (%s); features "
                         "will be neutral", exc)
            return cls({})

        mapping = {
            normalise(name): label
            for name, label in (payload.get("circuits") or {}).items()
        }
        logger.info(
            "loaded %d circuit archetypes (artifact %s)",
            len(mapping), payload.get("version", "?"),
        )
        return cls(mapping, payload.get("version", ""), payload.get("metrics"),
                   payload.get("team_mastery"))

    def archetype_of(self, circuit: Optional[str]) -> str:
        if not circuit:
            return UNKNOWN
        return self._mapping.get(normalise(circuit), UNKNOWN)

    def mastery_for(self, lineage: str, era: str) -> float:
        """This organisation's regulation-reset record as of ``era``.

        Zero for an organisation with no prior transitions — a genuinely new
        entrant has no record, and inventing one from the field average would
        assert something we do not know.
        """
        return float((self._mastery.get(era) or {}).get(lineage, 0.0))

    def labels(self):
        return sorted(set(self._mapping.values()))

    def __len__(self) -> int:
        return len(self._mapping)
