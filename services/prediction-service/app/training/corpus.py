"""Loading the training corpus from ingestion-service.

Training used to read JSONL files produced by ad-hoc pull scripts. That was a
mistake with a measurable cost: those scripts reimplemented a worse version of
the completeness layer, hit two silent-failure bugs it would have caught, and
produced a corpus in which two whole seasons had a single qualifying session
each. Nothing noticed until the fitted weights looked wrong.

Reading through the service fixes three things at once:

* **Durability.** Mongo survives; a temp directory does not, and one copy of the
  corpus was already lost to a cleanup.
* **One data path.** Serving reads features through this client. Training now
  does too, so the two cannot drift — the data-layer equivalent of the
  train/serve skew that ``feature_vector`` prevents at the feature level.
* **Completeness as a precondition.** The service already knows exactly which
  sessions are missing. Training can therefore *refuse* to run on a corpus with
  holes, instead of silently fitting a worse model.
"""

import logging
from typing import List, Optional, Sequence, Tuple

from app.services.ingestion_client import (
    IngestionClient,
    QualiResult,
    RaceResult,
)

logger = logging.getLogger(__name__)


class CorpusIncomplete(RuntimeError):
    """The corpus has known gaps and the caller demanded a whole one.

    Deliberately an error rather than a warning. A model fitted on a corpus with
    silent holes is not obviously worse — it trains, it validates, it ships, and
    it is simply less good than it should be, permanently and invisibly.
    """


#: Lap timing starts here; earlier races have no laps to fetch.
FIRST_LAP_SEASON = 2018


async def load_race_pace(client: IngestionClient, results: List[RaceResult]):
    """Corrected race-pace gaps for every race in ``results`` that has laps."""
    from app.services import race_pace
    from app.services.model import BASE_FEATURES

    if not {"race_pace_recent_pct", "team_race_pace_pct"} & set(BASE_FEATURES):
        # Withheld from the model (see WITHHELD_FEATURES): nothing to fetch.
        return {}
    races = sorted({(r.season, r.round) for r in results if r.season >= FIRST_LAP_SEASON})
    laps = []
    for season, round_number in races:
        laps.extend(await client.race_laps(season, round_number))
    gaps = race_pace.gaps_by_race(laps)
    logger.info("race pace: %s laps, %s of %s races usable",
                len(laps), len(gaps), len(races))
    return gaps


async def load_race_contexts(client: IngestionClient, results: List[RaceResult]):
    """Every race since lap timing began, summarised for the lap simulator."""
    from collections import defaultdict

    from app.training import lap_profiles

    by_race = defaultdict(list)
    for row in results:
        if row.season >= FIRST_LAP_SEASON:
            by_race[(row.season, row.round)].append(row)
    contexts = []
    for season in sorted({season for season, _ in by_race}):
        stints, stops, messages = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in await client.season_stints(season):
            stints[row.round].append(row)
        for row in await client.season_pit_stops(season):
            stops[row.round].append(row)
        for row in await client.season_race_control(season):
            messages[row.round].append(row)
        for round_number in sorted(r for s, r in by_race if s == season):
            laps = await client.race_laps(season, round_number)
            if not laps:
                continue
            contexts.append(lap_profiles.summarise_race(
                season, round_number, laps, stints[round_number],
                stops[round_number], messages[round_number],
                by_race[(season, round_number)]))
    logger.info("race context: %s races with laps", len(contexts))
    return contexts


async def load(
    client: IngestionClient,
    from_season: int,
    to_season: int,
    require_complete: bool = True,
) -> Tuple[List[RaceResult], List[QualiResult]]:
    """Fetch results and qualifying for a season range, gated on completeness."""
    status = await client.completeness(from_season, to_season, depth="results")
    if status.open_gaps:
        message = "corpus has {} gap(s): {}".format(
            len(status.open_gaps), ", ".join(status.open_gaps[:10])
        )
        if require_complete:
            raise CorpusIncomplete(
                message
                + ". Heal them with POST /ingest/backfill "
                '{"depth": "results", "only_gaps": true}, or pass '
                "require_complete=False to train on what exists."
            )
        logger.warning("training on an incomplete corpus — %s", message)

    results: List[RaceResult] = []
    quali: List[QualiResult] = []
    for season in range(from_season, to_season + 1):
        results.extend(await client.season_results(season))
        quali.extend(await client.season_qualifying(season))

    logger.info(
        "corpus %s-%s: %s result rows, %s qualifying rows, %s gap(s)",
        from_season, to_season, len(results), len(quali), len(status.open_gaps),
    )
    return results, quali
