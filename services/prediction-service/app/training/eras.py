"""F1 technical eras, and how much each should inform a modern forecast.

**Why era cannot be a plain feature.** Plackett-Luce probabilities depend only on
*differences* in strength between drivers within one race. Era is identical for
every driver in a given race, so an additive era term cancels exactly — it is
unidentifiable, and fitting one would return an arbitrary weight that changes no
prediction. Era has to enter the model as something that varies *between*
drivers, or not at all.

So it enters two ways, both real:

**1. Sample weighting.** How much should a 1998 race teach us about 2026? Some,
but less than a 2024 race. Weighting by era relevance replaces the crude
train-from-year-X cutoff with a smooth one, and lets old data contribute what it
can without dragging the fit toward a sport that no longer exists.

**2. Interaction with grid position.** This is the physically meaningful one.
How much starting position determines finishing position varies enormously by
era: refuelling, tyre rules, DRS and ground-effect aero all changed how possible
overtaking is. A single grid weight averaged across thirty years is wrong in
both directions — too weak for processional eras, too strong for modern ones.

Boundaries follow actual technical regulation resets rather than round decades.
"""

from typing import Dict, List, Optional, Tuple

#: (first_season, last_season, name). Boundaries are real regulation changes:
#: 1994 refuelling/safety, 1998 narrow track + grooved tyres, 2009 OWG aero and
#: the return of slicks, 2011 DRS, 2014 hybrid V6, 2017 wide-body high-downforce,
#: 2022 ground effect, 2026 new power unit and active aero.
ERAS: List[Tuple[int, int, str]] = [
    (1950, 1993, "historic"),
    (1994, 1997, "refuelling"),
    (1998, 2008, "grooved"),
    (2009, 2010, "owg_aero"),
    (2011, 2013, "drs_v8"),
    (2014, 2016, "hybrid_v6"),
    (2017, 2021, "wide_body"),
    (2022, 2025, "ground_effect"),
    (2026, 2099, "new_regs"),
]

#: The era a forecast is being made for. Everything else is judged by distance
#: from here.
CURRENT_ERA = "new_regs"


def era_for(season: int) -> str:
    for first, last, name in ERAS:
        if first <= season <= last:
            return name
    return "historic"


def era_index(name: str) -> int:
    for index, (_, _, era_name) in enumerate(ERAS):
        if era_name == name:
            return index
    return 0


def era_names() -> List[str]:
    return [name for _, _, name in ERAS]


def era_distance(season: int, target_season: int) -> int:
    """How many era boundaries separate a race from the season being forecast.

    Counted in eras rather than years on purpose: 2021 and 2022 are one year
    apart but separated by the largest aerodynamic reset in a decade, while 1999
    and 2007 are eight years apart under essentially the same rules.
    """
    return abs(era_index(era_for(season)) - era_index(era_for(target_season)))


def sample_weight(
    season: int, target_season: int, decay: float = 0.6, floor: float = 0.02
) -> float:
    """How much one historical race should count when fitting for a target season.

    Geometric decay in era distance. ``decay=0.6`` means each era back counts
    about 60% as much as the one after it, so the current era dominates while a
    decade-old race still contributes real signal instead of being discarded by
    an arbitrary cutoff.

    The floor keeps very old races from reaching exactly zero — they carry weak
    but genuine information about how racing works, and a hard zero is the same
    hard cutoff this exists to avoid.
    """
    return max(decay ** era_distance(season, target_season), floor)


#: The season from which the modern overtaking regime begins. DRS is the single
#: largest change to how much a starting position is worth.
MODERN_FROM = 2011


def era_bucket(season: int, target_season: int) -> str:
    """Coarse grouping used for the grid interaction.

    Two buckets, not nine. Fitting a separate grid weight per era would spend
    parameters on eras with a handful of races — and worse, it did real damage:
    an earlier three-way split (legacy / DRS / ground-effect-2022+) put the
    serving bucket's entire training set inside the validation and test holdouts,
    so the fitted modern grid weight came back as exactly zero and the served
    model ignored grid position altogether.

    The split that survives is pre- and post-DRS. Ground effect did change
    racing, but the measured difference in grid value was small (-0.36 vs -0.31),
    which does not justify a bucket that can be starved of data.
    """
    return "modern" if season >= MODERN_FROM else "legacy"


ERA_BUCKETS = ("legacy", "modern")


def era_bucket_indicators(season: int, target_season: int) -> Dict[str, float]:
    """One-hot over era buckets, for building interaction features."""
    bucket = era_bucket(season, target_season)
    return {name: 1.0 if name == bucket else 0.0 for name in ERA_BUCKETS}
