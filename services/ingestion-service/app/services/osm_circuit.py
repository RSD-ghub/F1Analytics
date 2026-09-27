"""Circuit layouts from OpenStreetMap, for tracks telemetry cannot reach.

The primary source is a real lap: position samples traced from the fastest
qualifying lap on record. That fails for two kinds of circuit — one returning
after a long absence, because car telemetry only exists from 2018 (Sepang last
raced in 2017), and one brand new, because it has not been driven yet.

OpenStreetMap has many circuits mapped in detail, under the ODbL, which permits
reuse with attribution. What it does not have is a lap: there is no speed and
no corner numbering, so a map from here is a plain outline beside the same
statistics, and the page says where it came from.

**The hard part is that OSM does not store a circuit, it stores roads.** Sepang
arrives as thirty separate ``highway=raceway`` ways — the grand prix layout,
the north and south sub-circuits, a handling course, the pit lane, and access
roads — with no relation ordering them and no tag saying which belong to the
lap. Picking wrongly draws a confident, wrong map.

So the layout is not guessed, it is **verified**: the ways are searched for a
closed circuit whose measured length matches the official figure formula1.com
publishes for that track. Sepang's stitched loop comes to 5,546 metres against
a stated 5,543. A circuit with no official length to check against gets no map
from here at all, because there would be no way to know we had drawn the right
roads.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"

#: Identifies us to two volunteer-run services. Both ask for it, and both are
#: within their rights to refuse an anonymous client.
USER_AGENT = "F1Forecast/0.1 (hobby project; +https://github.com/f1-forecast)"

#: Ways whose names put them outside the grand prix lap. Sub-circuits and the
#: pit lane connect to the layout at both ends, so a search that can walk down
#: them will happily return a lap that uses one as a shortcut.
NOT_THE_LAP = (
    "pit lane", "pitlane", "north circuit", "south circuit", "east circuit",
    "west circuit", "handling circuit", "paddock", "access", "service",
    "karting", "kart",
)

#: How close the stitched loop must come to the official length to be believed.
#:
#: Tight on purpose. The whole point of the check is that it can fail: a wrong
#: set of roads is off by a factor, not by a percent, so anything near the
#: stated figure is the lap and anything else is not. Sepang lands at 0.1%.
LENGTH_TOLERANCE = 0.05

#: Ceiling on the search, as a multiple of the target length. A partial lap can
#: always be extended; this stops the walk exploring routes that are already
#: too long to be the answer.
SEARCH_CEILING = 1.25

#: Seeds tried before giving up. The lap contains the longest ways, so starting
#: from the longest few finds it quickly where it exists at all.
SEED_WAYS = 6


class OSMUnavailable(RuntimeError):
    """No verifiable layout from OpenStreetMap. The circuit shows no map."""


def metres_between(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Great-circle distance in metres between two (lat, lon) points."""
    radius = 6371000.0
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = lat2 - lat1
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def path_length(points: Sequence[Tuple[float, float]]) -> float:
    return sum(metres_between(points[i - 1], points[i]) for i in range(1, len(points)))


def _coords(way: Dict[str, Any]) -> List[Tuple[float, float]]:
    return [(p["lat"], p["lon"]) for p in way.get("geometry") or []]


def usable_ways(elements: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Raceway ways that could plausibly be part of the lap."""
    kept = []
    for way in elements:
        if not way.get("geometry") or len(way.get("nodes") or []) < 2:
            continue
        name = (way.get("tags") or {}).get("name") or ""
        if any(token in name.lower() for token in NOT_THE_LAP):
            continue
        kept.append(way)
    return kept


def stitch_lap(
    ways: Sequence[Dict[str, Any]], target_metres: float
) -> List[Tuple[float, float]]:
    """Find the closed loop through ``ways`` that measures ``target_metres``.

    A depth-first walk over ways joined at shared end nodes, bounded by length,
    keeping the closed route whose total is nearest the target. Exhaustive
    search over all cycles would be exponential; the length ceiling and the
    handful of seeds keep it to a few thousand steps on a real circuit.

    Raises rather than returning the best effort. A loop that is not the right
    length is not the lap, and half a circuit drawn confidently is worse than
    an honest blank.
    """
    if not ways or target_metres <= 0:
        raise OSMUnavailable("nothing to stitch, or no length to check against")

    lengths = {way["id"]: path_length(_coords(way)) for way in ways}
    joins: Dict[int, List[Dict[str, Any]]] = {}
    for way in ways:
        for node in (way["nodes"][0], way["nodes"][-1]):
            joins.setdefault(node, []).append(way)

    best: Optional[Tuple[float, List[Tuple[Dict[str, Any], bool]]]] = None

    def walk(node, start_node, used, run, path):
        nonlocal best
        if run > target_metres * SEARCH_CEILING:
            return
        if node == start_node:
            error = abs(run - target_metres)
            if best is None or error < best[0]:
                best = (error, list(path))
            return
        for way in joins.get(node, []):
            if way["id"] in used:
                continue
            ahead = way["nodes"][0] == node
            used.add(way["id"])
            path.append((way, ahead))
            walk(
                way["nodes"][-1] if ahead else way["nodes"][0],
                start_node, used, run + lengths[way["id"]], path,
            )
            path.pop()
            used.discard(way["id"])

    for seed in sorted(ways, key=lambda w: -lengths[w["id"]])[:SEED_WAYS]:
        walk(seed["nodes"][-1], seed["nodes"][0], {seed["id"]},
             lengths[seed["id"]], [(seed, True)])
        if best is not None and best[0] <= target_metres * LENGTH_TOLERANCE:
            break

    if best is None:
        raise OSMUnavailable("no closed circuit found among the raceway ways")

    error, path = best
    points: List[Tuple[float, float]] = []
    for way, ahead in path:
        segment = _coords(way)
        if not ahead:
            segment.reverse()
        points.extend(segment if not points else segment[1:])

    measured = path_length(points)
    if error > target_metres * LENGTH_TOLERANCE:
        raise OSMUnavailable(
            "best loop measures {:.0f}m against an official {:.0f}m; that is "
            "not the lap".format(measured, target_metres)
        )
    logger.info(
        "stitched %d way(s) into %.0fm against an official %.0fm",
        len(path), measured, target_metres,
    )
    return points


# ── Talking to the two services ──────────────────────────────────────────────


def _get_json(url: str, params: Dict[str, Any], timeout: float) -> Any:
    import httpx

    response = httpx.get(
        url, params=params, timeout=timeout,
        headers={"User-Agent": USER_AGENT}, follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


def locate(name: str, timeout: float = 30.0) -> Tuple[float, float]:
    """Where a circuit is, by name. Nominatim asks for at most one call a second."""
    try:
        hits = _get_json(
            NOMINATIM, {"q": name, "format": "json", "limit": 1}, timeout
        )
    except Exception as exc:
        raise OSMUnavailable("could not geocode {}: {}".format(name, exc)) from exc
    if not hits:
        raise OSMUnavailable("OpenStreetMap has no place called {}".format(name))
    return float(hits[0]["lat"]), float(hits[0]["lon"])


def raceways(lat: float, lon: float, radius_m: int = 3000, timeout: float = 90.0):
    """Every mapped raceway near a point."""
    query = (
        '[out:json][timeout:60];'
        'way["highway"="raceway"](around:{},{},{});'
        'out geom;'.format(radius_m, lat, lon)
    )
    try:
        payload = _get_json(OVERPASS, {"data": query}, timeout)
    except Exception as exc:
        raise OSMUnavailable("Overpass refused: {}".format(exc)) from exc
    return payload.get("elements") or []


def build_outline(circuit: str, official_name: str, length_km: float):
    """A verified layout for one circuit, or an exception.

    ``length_km`` is the official figure and is not optional: it is the only
    thing distinguishing the grand prix lap from the other roads on the site.
    """
    from app.services.circuit_map import CircuitMap, placement, slug

    if not length_km:
        raise OSMUnavailable(
            "{}: no official length to verify a layout against".format(circuit)
        )

    lat, lon = locate(official_name or circuit)
    ways = usable_ways(raceways(lat, lon))
    if not ways:
        raise OSMUnavailable("{}: no raceway mapped near {}".format(circuit, official_name))

    points = stitch_lap(ways, length_km * 1000.0)

    # Latitude/longitude projected flat before placing. Over a few kilometres
    # the error is far below a pixel, and it keeps one placement routine
    # serving both sources of geometry.
    mid_lat = sum(p[0] for p in points) / len(points)
    scale_x = math.cos(math.radians(mid_lat))
    flat = [(p[1] * scale_x, p[0]) for p in points]
    place = placement(flat)

    return CircuitMap(
        circuit=circuit,
        slug=slug(circuit),
        source_season=0,
        source_session="openstreetmap",
        outline=[place(x, y) for x, y in flat],
        # No lap means no speeds. The component draws a plain outline and the
        # legend says where the shape came from instead of what it is coloured
        # by.
        speeds=[],
        corners=[],
        lap_distance_m=int(path_length(points)),
        top_speed_kph=0,
        slowest_kph=0,
    )
