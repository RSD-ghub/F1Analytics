"""Track geometry, derived from a real lap rather than drawn.

The layout on the page is the racing line an actual car took: FastF1 carries
X/Y position samples for every lap, and the fastest qualifying lap of a
weekend traces the circuit more faithfully than any hand-drawn outline, with
speed attached at every point. Corner numbers and their apex positions come
from the same source, so the labels cannot drift away from the shape.

Derivation is expensive — one session load, telemetry included — and a circuit
does not change between visits. So it runs once per circuit and is stored;
callers read the stored document.

What this deliberately does not do is invent. Where FastF1 has no position
data for a session, the circuit simply has no map, and the page says so rather
than falling back to a generic oval.
"""

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Points kept in the stored outline.
#:
#: A qualifying lap yields 700-900 position samples. At the size this renders —
#: a panel a few hundred pixels across — anything past about 300 is detail no
#: screen can show, and the document is fetched by every visitor to the home
#: page. Chosen by eye against Baku and Monaco, the two shapes in the calendar
#: most likely to lose something: Monaco's hairpin is the tightest radius we
#: have to keep, and it survives.
OUTLINE_POINTS = 300

#: The coordinate box the outline is normalised into, with a small margin so
#: corner labels sitting outside the line still land inside the viewBox.
VIEWBOX = 1000.0
MARGIN = 60.0


class CircuitMapUnavailable(RuntimeError):
    """No position data for this circuit. The page shows stats without a map."""


#: Venues upstream has called by more than one name.
#:
#: The schedule's ``Location`` is not stable across seasons. Abu Dhabi is
#: filed under "Abu Dhabi" to 2017, "Yas Marina" for 2018 and "Yas Island"
#: since — three names, one track. Left alone, the 2026 Abu Dhabi Grand Prix
#: found a single prior visit, drew its layout from 2018, and reported a
#: circuit record built from one race.
#:
#: Accent and punctuation variants are *not* listed. "Montreal"/"Montréal" and
#: "Spa"/"Spa-Francorchamps" already collapse in ``slug``; only genuine
#: renames need naming here.
#:
#: Note what this map deliberately does not do: merge two different circuits
#: that share a race name. The Spanish Grand Prix is Barcelona through 2025
#: and Madrid in 2026, and the European Grand Prix has been both Valencia and
#: Baku. Keying on the race name would have quietly fused them.
CIRCUIT_ALIASES = {
    "abu dhabi": "yas island",
    "yas marina": "yas island",
    "monaco": "monte carlo",
    "miami gardens": "miami",
    "singapore": "marina bay",
    "spa": "spa francorchamps",
    "nurburg": "nurburgring",
}


def slug(circuit: str) -> str:
    """A stable key for a circuit name.

    Lowercase and punctuation-stripped so "Monte Carlo", "monte-carlo" and
    "Monte-Carlo" are one circuit.

    Accents are folded rather than dropped. Stripping them turned "Montréal"
    into "montr al", which works — both writing and reading go through this
    function — but reads as corruption in the database and would collide with
    any circuit whose name differs only where the accent was.
    """
    folded = unicodedata.normalize("NFKD", circuit or "")
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    key = re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()
    return CIRCUIT_ALIASES.get(key, key)


def _rotate(x: float, y: float, degrees: float) -> Tuple[float, float]:
    """Turn a point about the origin.

    FastF1 publishes a per-circuit ``rotation`` because its raw coordinates sit
    in the timing system's frame, not the one every broadcast and track map
    uses. Without applying it Baku arrives on its side.
    """
    radians = math.radians(degrees)
    cos, sin = math.cos(radians), math.sin(radians)
    return x * cos - y * sin, x * sin + y * cos


def _downsample(rows: Sequence[Any], keep: int) -> List[int]:
    """Indices of ``keep`` points spread evenly through ``rows``.

    Evenly by index rather than by distance: position samples arrive at a fixed
    rate, so the car is already sampled more densely where it is slow, which is
    exactly where the shape carries the most information.
    """
    total = len(rows)
    if total <= keep:
        return list(range(total))
    step = total / float(keep)
    return [int(i * step) for i in range(keep)]


@dataclass
class CircuitMap:
    """A circuit's shape, its corners, and where the car is quick."""

    circuit: str
    slug: str
    source_season: int
    source_session: str
    #: Outline points in a 0..1000 box, y already flipped for SVG.
    outline: List[List[float]] = field(default_factory=list)
    #: Speed in kph at each outline point, same length and order as ``outline``.
    speeds: List[int] = field(default_factory=list)
    corners: List[Dict[str, Any]] = field(default_factory=list)
    lap_distance_m: int = 0
    top_speed_kph: int = 0
    slowest_kph: int = 0

    def as_document(self) -> Dict[str, Any]:
        return {
            "id": self.slug,
            "circuit": self.circuit,
            "slug": self.slug,
            "source_season": self.source_season,
            "source_session": self.source_session,
            "outline": self.outline,
            "speeds": self.speeds,
            "corners": self.corners,
            "lap_distance_m": self.lap_distance_m,
            "top_speed_kph": self.top_speed_kph,
            "slowest_kph": self.slowest_kph,
        }


def build_map(
    circuit: str,
    season: int,
    session_name: str,
    telemetry,
    corners,
    rotation: float,
) -> CircuitMap:
    """Turn a lap's telemetry and the circuit's corner table into a drawable map.

    Separated from the FastF1 call so the geometry can be tested on synthetic
    coordinates without a session load or a network.
    """
    if telemetry is None or len(telemetry) < 50:
        raise CircuitMapUnavailable(
            "{}: {} position samples is not a lap".format(
                circuit, 0 if telemetry is None else len(telemetry)
            )
        )

    xs_raw = list(telemetry["X"])
    ys_raw = list(telemetry["Y"])
    speeds_raw = list(telemetry["Speed"])

    rotated = [_rotate(x, y, rotation) for x, y in zip(xs_raw, ys_raw)]
    corner_points = [
        _rotate(float(row.X), float(row.Y), rotation) for row in corners.itertuples()
    ] if corners is not None and len(corners) else []

    # One scale for the line and the corner markers, or the numbers drift off
    # the track they label.
    every_x = [p[0] for p in rotated] + [p[0] for p in corner_points]
    every_y = [p[1] for p in rotated] + [p[1] for p in corner_points]
    min_x, max_x = min(every_x), max(every_x)
    min_y, max_y = min(every_y), max(every_y)
    span = max(max_x - min_x, max_y - min_y) or 1.0
    scale = (VIEWBOX - 2 * MARGIN) / span
    # Centre the shorter axis so a long thin circuit is not pinned to one edge.
    offset_x = MARGIN + ((VIEWBOX - 2 * MARGIN) - (max_x - min_x) * scale) / 2
    offset_y = MARGIN + ((VIEWBOX - 2 * MARGIN) - (max_y - min_y) * scale) / 2

    def place(x: float, y: float) -> List[float]:
        # SVG's y grows downward; the timing frame's grows up. Flipping here
        # rather than in the component keeps the stored document the thing that
        # gets drawn, with no per-renderer convention to remember.
        return [
            round((x - min_x) * scale + offset_x, 1),
            round(VIEWBOX - ((y - min_y) * scale + offset_y), 1),
        ]

    keep = _downsample(rotated, OUTLINE_POINTS)
    outline = [place(*rotated[i]) for i in keep]
    speeds = [int(speeds_raw[i] or 0) for i in keep]

    corner_rows: List[Dict[str, Any]] = []
    for point, row in zip(corner_points, corners.itertuples()):
        placed = place(*point)
        corner_rows.append({
            "number": int(row.Number),
            "letter": (getattr(row, "Letter", "") or "").strip(),
            "x": placed[0],
            "y": placed[1],
            # Sign is direction, magnitude is severity. Kept raw: a component
            # deciding "left or right" is a different job from measuring it.
            "angle": round(float(row.Angle), 1),
            "distance_m": int(row.Distance),
        })

    measured = [s for s in speeds if s > 0]
    return CircuitMap(
        circuit=circuit,
        slug=slug(circuit),
        source_season=season,
        source_session=session_name,
        outline=outline,
        speeds=speeds,
        corners=corner_rows,
        lap_distance_m=int(max(telemetry["Distance"])) if len(telemetry) else 0,
        top_speed_kph=max(measured) if measured else 0,
        slowest_kph=min(measured) if measured else 0,
    )
