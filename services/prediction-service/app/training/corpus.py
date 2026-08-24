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
