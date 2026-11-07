"""Does rain change who wins, in a way the model could use? A measurement.

A race-level number like "it rained" cannot enter a Plackett-Luce model on
its own: adding the same amount to every driver's strength changes nobody's
odds. Rain can only matter through what it does *differently* to drivers, so
every candidate here is an interaction, zero in a dry race:

* ``wet_x_grid``  - rain times starting position: does the grid count for less
  when it is wet (safety cars, spins, strategy lottery)?
* ``wet_x_form``  - rain times recent average finish: does form count for less?
* ``wet_skill``   - rain times the driver's own record in earlier wet races
  (places gained versus their dry races), shrunk toward zero when they have
  few wet races behind them.

Wetness is measured from tyres, not the rain gauge: the share of the race's
laps run on intermediates or full wets. A shower that never forced a tyre
change did not make it a wet race for strategy purposes, and the gauge reads
rain in the hour before the start too. The gauge's share of samples is the
fallback where stint data is missing.

This module only *measures*. The backtest feeds it the rain that actually
fell, which is an upper bound on what a forecast could deliver: if knowing
the real weather does not help, a forecast of it cannot.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from app.training.plackett_luce import RaceObservation

RaceKey = Tuple[int, int]

WET_COMPOUNDS = {"INTERMEDIATE", "WET"}
#: A race counts as wet for building a driver's wet record above this share.
WET_RACE = 0.2
#: Shrinkage: a driver's wet record counts for n / (n + PRIOR_RACES).
PRIOR_RACES = 3.0

CANDIDATES = ("wet_x_grid", "wet_x_form", "wet_skill")


def wetness_from_stints(stints: Iterable[Mapping]) -> Dict[RaceKey, float]:
    laps: Dict[RaceKey, float] = defaultdict(float)
    wet: Dict[RaceKey, float] = defaultdict(float)
    for row in stints:
        key = (int(row["season"]), int(row["round"]))
        n = float(row.get("laps") or 0)
        laps[key] += n
        if str(row.get("compound", "")).upper() in WET_COMPOUNDS:
            wet[key] += n
    return {key: wet[key] / total for key, total in laps.items() if total > 0}


def wetness_from_gauge(weather: Iterable[Mapping]) -> Dict[RaceKey, float]:
    samples: Dict[RaceKey, List[bool]] = defaultdict(list)
    for row in weather:
        samples[(int(row["season"]), int(row["round"]))].append(bool(row.get("rainfall")))
    return {key: sum(v) / len(v) for key, v in samples.items() if v}


def race_wetness(stints: Iterable[Mapping], weather: Iterable[Mapping]) -> Dict[RaceKey, float]:
    """Tyre-based where we have stints, gauge-based otherwise; absent = unknown."""
    merged = wetness_from_gauge(weather)
    merged.update(wetness_from_stints(stints))
    return merged


def wet_records(results: Sequence, wetness: Mapping[RaceKey, float]) -> Dict[str, List[Tuple[RaceKey, float, bool]]]:
    """Per driver: (race, places gained, was it wet) for classified finishes
    from a real grid slot, in race order."""
    out: Dict[str, List[Tuple[RaceKey, float, bool]]] = defaultdict(list)
    for row in sorted(results, key=lambda r: (r.season, r.round)):
        key = (row.season, row.round)
        if key not in wetness or not row.classified or row.grid_position <= 0:
            continue
        out[row.driver].append((key, float(row.grid_position - row.position), wetness[key] >= WET_RACE))
    return out


def wet_skill(records: Sequence[Tuple[RaceKey, float, bool]], before: RaceKey) -> float:
    """Mean places gained in wet races minus dry ones, before ``before``, shrunk."""
    prior = [r for r in records if r[0] < before]
    wet = [g for _, g, is_wet in prior if is_wet]
    dry = [g for _, g, is_wet in prior if not is_wet]
    if not wet or not dry:
        return 0.0
    delta = float(np.mean(wet) - np.mean(dry))
    return delta * len(wet) / (len(wet) + PRIOR_RACES)


def augment(
    observations: Sequence[RaceObservation],
    names: Sequence[str],
    wetness: Mapping[RaceKey, float],
    records: Mapping[str, Sequence[Tuple[RaceKey, float, bool]]],
    extra: Sequence[str],
) -> List[RaceObservation]:
    """Copies of ``observations`` with the ``extra`` columns appended."""
    grid_col = names.index("grid_modern") if "grid_modern" in names else None
    form_col = names.index("avg_finish_recent")
    out = []
    for obs in observations:
        key = (obs.season, obs.round)
        w = float(wetness.get(key, 0.0))
        columns = []
        for name in extra:
            if name == "wet_x_grid":
                col = w * (obs.features[:, grid_col] if grid_col is not None else 0.0)
            elif name == "wet_x_form":
                col = w * obs.features[:, form_col]
            elif name == "wet_skill":
                col = np.array([w * wet_skill(records.get(d, ()), key) for d in obs.drivers])
            else:
                raise ValueError("unknown weather feature: " + name)
            columns.append(np.broadcast_to(col, (obs.features.shape[0],)).astype(float))
        features = np.column_stack([obs.features] + columns) if columns else obs.features
        out.append(RaceObservation(features=features, order=obs.order, season=obs.season,
                                   round_number=obs.round, weight=obs.weight, drivers=obs.drivers))
    return out
