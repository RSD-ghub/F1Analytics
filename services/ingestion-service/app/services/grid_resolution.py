"""Applying an official starting grid onto our qualifying rows.

The join is the risky part of this feature, not the download. ``grid_position``
is the single largest weight in the race model, so a row matched to the wrong
driver is worse than no grid at all: it produces a confident, precise, wrong
forecast rather than a flagged one. Everything here is therefore written to fail
loudly and apply all-or-nothing.

Two keys, in order:

* **car number** — a fact on both sides and immune to spelling. Preferred.
* **surname** — the fallback for rows ingested before car numbers were stored.
  Used only when it is unambiguous across the whole field.

If any entry in the document cannot be matched, or a surname is ambiguous, the
whole grid is rejected. A partially applied grid is the failure mode this module
exists to prevent: it would leave some drivers on their true slot and others on
their qualifying slot, which is not a grid that ever existed.
"""

import logging
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.schemas import GridSource, QualifyingRow
from app.services.transforms import stable_id
from app.services.fia_documents import GridEntry, StartingGridDocument, normalise_name

logger = logging.getLogger(__name__)


class GridApplicationError(RuntimeError):
    """The document could not be matched onto our rows with confidence."""


#: FIA document kind -> the provenance we record for it.
_KIND_TO_SOURCE = {
    "final": GridSource.OFFICIAL_FINAL,
    "provisional": GridSource.OFFICIAL_PROVISIONAL,
}


def entry_season(document) -> int:
    return document.season


def _surname(name: str) -> str:
    parts = normalise_name(name).split()
    return parts[-1] if parts else ""


def apply_starting_grid(
    rows: Sequence[QualifyingRow],
    document: StartingGridDocument,
    identities: Optional[Dict[int, tuple]] = None,
) -> List[QualifyingRow]:
    """Return ``rows`` with confirmed grid positions from ``document``.

    ``identities`` maps car number to ``(driver, team)`` and lets a driver who
    is on the grid but absent from our rows be added rather than rejected.

    That case is ordinary, not exceptional. The FIA's *classification* document
    lists only drivers who set a time; the *starting grid* lists everyone who
    starts. At the 2026 Spanish Grand Prix that was 20 against 22 — Stroll and
    Bearman set no time, started from the back, and their absence from our rows
    caused a complete and correct grid to be thrown away entirely.

    Still refuses when an entry cannot be resolved at all. A partial grid mixes
    true and assumed slots into an order that never existed, and a forecast
    locked on one cannot be taken back — the all-or-nothing rule stands, it just
    no longer treats "we are missing a row" as "the document is wrong".
    """
    if not rows:
        raise GridApplicationError("no qualifying rows to apply a grid to")

    source = _KIND_TO_SOURCE.get(document.kind)
    if source is None:
        raise GridApplicationError("unknown grid document kind: {}".format(document.kind))

    by_number: Dict[int, QualifyingRow] = {}
    for row in rows:
        if row.driver_number > 0:
            by_number.setdefault(row.driver_number, row)

    by_surname: Dict[str, List[QualifyingRow]] = {}
    for row in rows:
        by_surname.setdefault(_surname(row.driver), []).append(row)

    identities = identities or {}
    assignments: Dict[str, Tuple[GridEntry, str]] = {}
    added: List[QualifyingRow] = []
    unmatched: List[str] = []
    for entry in document.entries:
        row, how = _match(entry, by_number, by_surname)
        if row is None:
            known = identities.get(entry.car_number)
            if known is not None:
                driver, team = known
                # On the grid, absent from the classification: they set no time.
                # position 999 is exactly right — the schema already reads it as
                # "no qualifying result" — while the grid slot is confirmed.
                added.append(
                    QualifyingRow(
                        id=stable_id("quali", entry_season(document), document.round, driver),
                        season=document.season,
                        round=document.round,
                        race_name=document.event_name,
                        driver=driver,
                        team=team,
                        position=999,
                        driver_number=entry.car_number,
                        grid_position=entry.position,
                        grid_source=source,
                        starts_from_pit_lane=entry.from_pit_lane,
                    )
                )
                continue
            unmatched.append("{} (car {})".format(entry.driver_name, entry.car_number))
            continue
        if row.id in assignments:
            raise GridApplicationError(
                "two grid entries matched the same driver ({}); refusing to "
                "apply a grid built on an ambiguous join".format(row.driver)
            )
        assignments[row.id] = (entry, how)

    if unmatched:
        raise GridApplicationError(
            "could not match {} of {} grid entries to our qualifying rows, and "
            "no entry list identifies them: {}. Refusing to apply a partial "
            "grid.".format(len(unmatched), len(document.entries), ", ".join(unmatched))
        )

    applied: List[QualifyingRow] = []
    for row in rows:
        found = assignments.get(row.id)
        if found is None:
            # In the document's field but not on the grid — excluded, withdrawn,
            # or did not qualify. Left explicitly unconfirmed rather than guessed.
            applied.append(row)
            continue
        entry, _ = found
        applied.append(
            row.model_copy(
                update={
                    "grid_position": entry.position,
                    "grid_source": source,
                    "starts_from_pit_lane": entry.from_pit_lane,
                }
            )
        )

    applied.extend(added)
    if added:
        logger.info(
            "added %d driver(s) present on the grid but absent from the "
            "classification (no qualifying time set): %s",
            len(added), ", ".join(row.driver for row in added),
        )

    by_car = sum(1 for _, how in assignments.values() if how == "car")
    logger.info(
        "applied the %s starting grid for %s-%s: %d slots (%d by car number, "
        "%d by surname)",
        document.kind, document.season, document.round,
        len(assignments), by_car, len(assignments) - by_car,
    )
    return applied


def _match(entry, by_number, by_surname):
    row = by_number.get(entry.car_number)
    if row is not None:
        return row, "car"

    candidates = by_surname.get(_surname(entry.driver_name), [])
    if len(candidates) == 1:
        return candidates[0], "surname"
    if len(candidates) > 1:
        raise GridApplicationError(
            "surname '{}' matches {} drivers and no car number is stored; "
            "re-ingest qualifying to record car numbers".format(
                _surname(entry.driver_name), len(candidates)
            )
        )
    return None, ""
