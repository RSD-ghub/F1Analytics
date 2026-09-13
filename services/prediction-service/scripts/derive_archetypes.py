#!/usr/bin/env python
"""Fit circuit archetypes and freeze them into an artifact.

Build-time, like training. Reads corner geometry from FastF1 and lap times from
the ingested corpus, clusters the circuits, and writes
``app/circuit_archetypes.json``. The serving path never runs this — it only
reads the artifact — so prediction-service keeps no FastF1 dependency.

    python scripts/derive_archetypes.py --to-season 2021 --clusters 4

``--to-season`` bounds which races contribute lap times, so the artifact can be
fitted on training seasons alone and the held-out seasons stay held out.

Two metrics per circuit, both measurable:

* **corner density** — corners per minute of lap time. Monza's eleven corners
  spread over a long lap is a very different track from Monaco's nineteen over a
  short one, and the raw count alone conflates them.
* **mean lap time** — a proxy for circuit length, which separates the long
  power tracks from short technical ones.

Top speed would be the most direct measure of power sensitivity and is
deliberately absent: FastF1 serves SpeedST on every lap frame, but ingestion
discards it, and capturing it needs a schema change plus a full re-ingest. The
metric vector is built so it can be added as another dimension without changing
anything downstream.
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from pymongo import MongoClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("archetypes")

ARTIFACT = os.path.join(os.path.dirname(__file__), "..", "app", "circuit_archetypes.json")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
CACHE = os.getenv("FASTF1_CACHE", "../../fastf1-data/cache")

#: First season with lap data, and therefore with a warm cache.
LAP_DATA_FROM = 2018

#: Labels are assigned after fitting by ordering clusters on corner density, so
#: they describe what was found rather than what was expected.
LABEL_ORDER = ["power", "balanced", "technical", "street_technical"]


def lap_time_by_circuit(db, to_season):
    """Median green-flag lap time per circuit, from the ingested corpus."""
    rounds = {}
    for row in db["results"].find(
        {"season": {"$lte": to_season}}, {"season": 1, "round": 1, "circuit": 1}
    ):
        rounds[(row["season"], row["round"])] = row.get("circuit")

    times = {}
    for lap in db["laps"].find(
        {"season": {"$lte": to_season}}, {"season": 1, "round": 1, "lap_time_seconds": 1}
    ):
        circuit = rounds.get((lap["season"], lap["round"]))
        seconds = lap.get("lap_time_seconds") or 0
        # Anything beyond a sane window is a safety car, an in-lap or a stop.
        if circuit and 45 < seconds < 240:
            times.setdefault(circuit, []).append(seconds)
    return {c: float(np.median(v)) for c, v in times.items() if len(v) >= 200}


def corner_counts(circuits, to_season):
    """Corner count per circuit from FastF1's circuit info. Cached, one call each."""
    import fastf1

    fastf1.Cache.enable_cache(CACHE)
    counts = {}
    for circuit, (season, round_number) in circuits.items():
        try:
            session = fastf1.get_session(season, round_number, "R")
            # laps=True is required, not incidental: get_circuit_info() derives
            # corner positions from the session's position data, and without it
            # every call raises "data has not been loaded yet".
            session.load(laps=True, telemetry=False, weather=False, messages=False)
            corners_df = session.get_circuit_info().corners
            counts[circuit] = (len(corners_df), _longest_straight(corners_df))
        except Exception as exc:  # noqa: BLE001
            logger.warning("  no corner data for %-28s (%s)", circuit, str(exc)[:60])
    return counts


def _longest_straight(corners) -> float:
    """Longest gap between consecutive corners, as a fraction of the lap.

    The power-sensitivity axis, and free: corner X/Y positions come with the
    circuit info we already fetch. Corner *density* cannot see a straight —
    Baku has nineteen corners and F1's longest flat-out run, and density alone
    files it next to Budapest. The gap between turns is what separates them.

    Normalised by the summed corner-to-corner distance so it is a shape, not a
    size, and does not simply re-measure lap length.
    """
    if corners is None or len(corners) < 3:
        return 0.0
    x = corners["X"].to_numpy(dtype=float)
    y = corners["Y"].to_numpy(dtype=float)
    # Wrap around: the pit straight is the gap between the last corner and the
    # first, and at several circuits it is the longest one on the lap.
    dx = np.diff(np.append(x, x[0]))
    dy = np.diff(np.append(y, y[0]))
    gaps = np.sqrt(dx ** 2 + dy ** 2)
    total = gaps.sum()
    return float(gaps.max() / total) if total > 0 else 0.0


def regulation_mastery(db, to_season, window=5):
    """Per-organisation record at handling regulation resets.

    Precomputed here rather than in ``features.py`` for a plain reason: it needs
    a decade of history and serving loads two seasons. Computed live it was zero
    for every team on every race — the feature meant to cover the cold start of
    a new rulebook could never fire.

    It is also the right shape for an artifact. Mastery is a slow-moving
    property of an organisation and does not change within an era, so
    recomputing it per race would be waste as well as impossible.

    Point-in-time safe by construction: only transitions strictly before the
    current era contribute, and ``to_season`` keeps held-out seasons out.
    """
    from app.services.team_lineage import lineage_of
    from app.training import eras

    rows = []
    for row in db["results"].find(
        {"season": {"$lte": to_season}},
        {"season": 1, "round": 1, "team": 1, "position": 1},
    ):
        position = row.get("position")
        if position and 0 < position < 100:
            rows.append((lineage_of(row.get("team", "")), row["season"],
                         row["round"], float(position)))

    by_team = {}
    for lineage, season, round_number, position in rows:
        by_team.setdefault(lineage, []).append((season, round_number, position))

    # Keyed by era, and that is the point. A single number would be either
    # stale or leaky: fitted to 2021 it misses the 2022 reset that a 2026
    # forecast may legitimately learn from, and fitted to 2025 it feeds
    # post-cutoff information into training on 2010-2021. Storing one entry per
    # era, each built only from transitions strictly *before* that era, lets a
    # forecast take the value that was knowable when it was made.
    mastery = {}
    for as_of_era in eras.era_names():
        mastery[as_of_era] = _mastery_as_of(by_team, as_of_era, window, eras)
    return mastery


def _mastery_as_of(by_team, as_of_era, window, eras):
    """Per-organisation mastery using only transitions before ``as_of_era``."""
    cutoff = eras.era_index(as_of_era)
    out = {}
    for lineage, entries in by_team.items():
        entries.sort()
        improvements = []
        for first, last, name in eras.ERAS:
            if eras.era_index(name) >= cutoff:
                break
            previous = eras.previous_era(first)
            if previous is None:
                continue
            before = [p for s_, r_, p in
                      [(e[0], e[1], e[2]) for e in entries]
                      if eras.era_for(s_) == previous]
            after = [p for s_, r_, p in
                     [(e[0], e[1], e[2]) for e in entries]
                     if first <= s_ <= last]
            if len(before) < window or len(after) < window:
                continue
            # Positive means the team came out of the reset finishing higher up.
            improvements.append(
                float(np.mean(before[-window * 2:])) - float(np.mean(after[: window * 2]))
            )
        if improvements:
            # Shrunk hard: three transitions is a thin base for a per-team
            # estimate, so a team with one counts for little.
            n = len(improvements)
            out[lineage] = round(float(np.mean(improvements)) * (n / (n + 1.5)), 4)
    return out


def kmeans(points, k, seed=0, iterations=200):
    """Small k-means. numpy only — a few dozen points does not want scikit-learn,
    and keeping the dependency out keeps the artifact reproducible."""
    rng = np.random.default_rng(seed)
    centres = points[rng.choice(len(points), k, replace=False)]
    labels = np.zeros(len(points), dtype=int)
    for _ in range(iterations):
        distances = ((points[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        new_labels = distances.argmin(axis=1)
        if (new_labels == labels).all():
            break
        labels = new_labels
        for index in range(k):
            member = points[labels == index]
            if len(member):
                centres[index] = member.mean(axis=0)
    return labels, centres


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to-season", type=int, default=2021,
                        help="last season contributing data (keep holdouts out)")
    parser.add_argument("--clusters", type=int, default=3)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)["f1_ingestion"]

    lap_times = lap_time_by_circuit(db, args.to_season)
    logger.info("circuits with enough lap data: %d", len(lap_times))

    # One representative race per circuit for the corner lookup, taking the most
    # recent within the cutoff. Recency matters for cost, not accuracy: corner
    # geometry barely moves, but lap data from 2018 onward is already in the
    # FastF1 cache from the backfill, so recent picks avoid re-downloading — and
    # avoid FastF1's 500-calls-per-hour limit turning a 43-circuit job into a
    # multi-hour one.
    representative = {}
    for row in db["results"].find(
        {"season": {"$lte": args.to_season, "$gte": LAP_DATA_FROM}},
        {"season": 1, "round": 1, "circuit": 1},
    ).sort([("season", -1), ("round", -1)]):
        circuit = row.get("circuit")
        if circuit in lap_times and circuit not in representative:
            representative[circuit] = (row["season"], row["round"])

    corners = corner_counts(representative, args.to_season)
    # A failed corner fetch yields zeros, and zeros cluster: Mugello came back
    # with no geometry and was filed as the most technical circuit in the sport
    # purely because 0 corners and a 0% straight sit at one extreme of both
    # axes. Missing data must be excluded, not clustered.
    usable = sorted(
        c for c in (set(lap_times) & set(corners))
        if corners[c][0] > 0 and corners[c][1] > 0
    )
    dropped = sorted((set(lap_times) & set(corners)) - set(usable))
    if dropped:
        logger.warning("excluded %d circuit(s) with no corner geometry: %s",
                       len(dropped), ", ".join(dropped))
    logger.info("circuits with both lap times and corner data: %d", len(usable))
    if len(usable) < args.clusters * 3:
        logger.error("not enough circuits (%d) to fit %d clusters", len(usable), args.clusters)
        return 1

    # Two dimensions, both *shape*: how often you turn, and how much of the lap
    # is spent flat out.
    #
    # Lap time was a third and has been removed. It measures how big a circuit
    # is, not what it asks of a car, and being the highest-variance axis it
    # dominated the fit — the clusters came out sorted by lap length, with every
    # long circuit labelled "power" and Monte Carlo grouped with Barcelona.
    # Median lap time is still recorded in the artifact for reference; it just
    # does not decide membership.
    raw = np.array([
        [
            corners[c][0] / (lap_times[c] / 60.0),   # corners per minute
            corners[c][1] * 100.0,                   # longest straight, % of lap
        ]
        for c in usable
    ], dtype=float)
    standardised = (raw - raw.mean(axis=0)) / (raw.std(axis=0) + 1e-9)

    labels, centres = kmeans(standardised, args.clusters)

    # Name clusters by how much of the lap is flat out, most first. The ordering
    # is derived, not assumed: which circuits land in "power" is whatever the
    # geometry says, and the label only describes the cluster that was found.
    # Ordered by corner density, fewest first. Straight fraction was tried and
    # mislabels: Barcelona has a longer straight than Monza as a share of the
    # lap, but eleven corners a minute against Monza's eight — it is a
    # high-downforce circuit with one straight attached, not a power track. How
    # often a car has to turn is what separates the two demands.
    order = np.argsort(centres[:, 0])
    naming = {int(cluster): LABEL_ORDER[rank] for rank, cluster in enumerate(order)}

    mapping = {circuit: naming[int(labels[i])] for i, circuit in enumerate(usable)}

    logger.info("")
    for label in LABEL_ORDER[: args.clusters]:
        members = [c for c in usable if mapping[c] == label]
        logger.info("%-18s (%d): %s", label, len(members), ", ".join(sorted(members)[:6]))

    payload = {
        "version": "archetypes-v1-to{}-k{}".format(args.to_season, args.clusters),
        "fitted_to_season": args.to_season,
        "clusters": args.clusters,
        "metrics": {
            c: {"corners_per_minute": round(raw[i][0], 4),
                "longest_straight_pct": round(raw[i][1], 3),
                "median_lap_seconds": round(lap_times[c], 3)}
            for i, c in enumerate(usable)
        },
        "circuits": mapping,
        "team_mastery": regulation_mastery(db, args.to_season),
    }
    if args.write:
        with open(ARTIFACT, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        logger.info("\nwrote %s", ARTIFACT)
    else:
        logger.info("\n(dry run; pass --write to save)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
