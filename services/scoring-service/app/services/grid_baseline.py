"""The grid-order baseline: what the starting grid alone would have said.

The uniform baseline in ``scoring.py`` asks "did the model beat a random pick?".
Once the grid is known that bar is low: pole wins far more often than one race
in twenty, so a forecast that only copies the grid already "beats guessing" by
a wide margin. The honest question for a post-qualifying forecast is whether it
adds anything *beyond* the grid. This module builds the forecast that knows
nothing but the grid, so the model can be scored against it.

**How it is built.** For every past Grand Prix strictly before the race being
scored, count how often each starting slot won, finished on the podium and
scored points. The rate for a slot is that count over its starts, shrunk toward
the field average so a slot with few starts cannot claim certainty. Starts
behind the 20th slot and pit-lane starts share one "back of the grid" slot.

**Point in time.** Only races before the scored one are counted, so the
baseline never benefits from the result it is being compared with.

**Comparable to the forecast.** Per race, the rates are rescaled so they sum to
the number of drivers the market expects to be true (one winner, three podium
places, ten scorers), as the model's own probabilities do.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.schemas import Market

#: Slots past this share one bucket, which also holds pit-lane starts (grid 0).
BACK_SLOT = 21

#: Prior strength in starts. A slot with five starts sits halfway between its
#: own record and the field average; one with a few hundred is its own record.
PRIOR_STARTS = 5.0

#: Fewer past races than this and the rates are mostly prior, so no baseline
#: is reported rather than a weak one presented as a real benchmark.
MIN_HISTORY_RACES = 20

MARKET_TRUE_COUNT = {Market.WIN: 1, Market.PODIUM: 3, Market.POINTS: 10}
PODIUM_POSITIONS = 3


def slot_for(grid_position: int) -> int:
    if 1 <= grid_position < BACK_SLOT:
        return grid_position
    return BACK_SLOT


@dataclass
class HistoricalStart:
    """One driver's start in one past race: where they started, how it ended."""

    season: int
    round: int
    grid_position: int
    position: int
    classified: bool


@dataclass
class GridRates:
    races: int = 0
    starts: Dict[int, int] = field(default_factory=dict)
    hits: Dict[Market, Dict[int, int]] = field(
        default_factory=lambda: {market: {} for market in Market}
    )

    @classmethod
    def from_history(
        cls,
        starts: Iterable[HistoricalStart],
        before: Tuple[int, int],
        points_positions: int = 10,
    ) -> "GridRates":
        """Rates from every start strictly before ``before`` = (season, round)."""
        rates = cls()
        seen_races = set()
        for start in starts:
            if (start.season, start.round) >= before:
                continue
            seen_races.add((start.season, start.round))
            slot = slot_for(start.grid_position)
            rates.starts[slot] = rates.starts.get(slot, 0) + 1
            outcome = {
                Market.WIN: start.classified and start.position == 1,
                Market.PODIUM: start.classified
                and 1 <= start.position <= PODIUM_POSITIONS,
                Market.POINTS: start.classified
                and 1 <= start.position <= points_positions,
            }
            for market, hit in outcome.items():
                if hit:
                    by_slot = rates.hits[market]
                    by_slot[slot] = by_slot.get(slot, 0) + 1
        rates.races = len(seen_races)
        return rates

    @property
    def usable(self) -> bool:
        return self.races >= MIN_HISTORY_RACES

    def slot_rate(self, market: Market, slot: int, prior: float) -> float:
        starts = self.starts.get(slot, 0)
        hits = self.hits[market].get(slot, 0)
        return (hits + PRIOR_STARTS * prior) / (starts + PRIOR_STARTS)

    def probabilities(
        self,
        market: Market,
        grid_positions: Sequence[int],
        true_count: Optional[int] = None,
    ) -> Optional[List[float]]:
        """The grid-only forecast for one race's field, in the given order.

        ``None`` when there is too little history to be a fair benchmark.
        """
        field_size = len(grid_positions)
        if not self.usable or field_size == 0:
            return None
        count = MARKET_TRUE_COUNT[market] if true_count is None else true_count
        prior = min(count / field_size, 1.0)
        raw = [self.slot_rate(market, slot_for(g), prior) for g in grid_positions]
        return rescale(raw, float(count))


def rescale(values: Sequence[float], total: float) -> List[float]:
    """Scale to sum to ``total`` with no value above 1.

    A plain rescale can push the front row's points rate past 1, so a value that
    reaches the cap is fixed at 1 and the others share what remains.
    """
    result = list(values)
    capped = [False] * len(result)
    while True:
        free_sum = sum(v for v, c in zip(result, capped) if not c)
        remaining = total - sum(1 for c in capped if c)
        if free_sum <= 0 or remaining <= 0:
            return result
        scale = remaining / free_sum
        newly_capped = False
        for index, value in enumerate(result):
            if capped[index]:
                continue
            result[index] = value * scale
            if result[index] >= 1.0:
                result[index] = 1.0
                capped[index] = True
                newly_capped = True
        if not newly_capped:
            return result
