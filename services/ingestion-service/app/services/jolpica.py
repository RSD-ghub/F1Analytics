"""Qualifying from jolpica, the Ergast successor. The third source, and the
only one that shares no machinery with the other two.

Why a third at all. FastF1 is preferred and is the slowest to publish a
classification. The FIA is authoritative and fastest — but it is reached by
constructing a URL from a slug rule, and that rule is *known to change*: the
2024 documents use a different scheme, which is why the slug resolves cleanly
for 2025 and 2026 and 404s for 2024. Grid documents and qualifying documents
share that rule, so a scheme change takes both down at once.

jolpica fails differently. Different host, different format, different
maintainers, and a JSON contract rather than a text layer extracted from a PDF.
It is slower to publish than the FIA — it had nothing for Spain three hours
after the session, when the FIA document was already out — so it sits last. It
is here for the case where the FIA path breaks rather than for speed.

Like the FIA path, this supplies *order and times only*. Driver identity keeps
coming from FastF1's entry list, joined on car number: a name that does not
match the corpus creates a driver with no history rather than an error, which is
the kind of damage that spreads.
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)


class JolpicaUnavailable(RuntimeError):
    """No qualifying classification published here, or the host is unreachable."""


@dataclass(frozen=True)
class JolpicaQualifyingEntry:
    position: int
    car_number: int
    driver_name: str
    team: str = ""
    q1_seconds: float = 0.0
    q2_seconds: float = 0.0
    q3_seconds: float = 0.0


_LAP = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d{1,3})$")


def _seconds(value: Optional[str]) -> float:
    """``"1:15.582"`` -> 75.582. Absent or unparseable becomes 0.0.

    A driver eliminated in Q1 genuinely has no Q2 time, and the schema already
    reads 0.0 as "no time set" — so a missing segment is not an error here.
    """
    if not value:
        return 0.0
    match = _LAP.match(value.strip())
    if not match:
        logger.warning("unparseable lap time from jolpica: %r", value)
        return 0.0
    minutes = int(match.group(1) or 0)
    return minutes * 60 + int(match.group(2)) + int(match.group(3).ljust(3, "0")) / 1000.0


async def fetch_qualifying(
    season: int,
    round_number: int,
    base_url: str = "https://api.jolpi.ca/ergast",
    timeout_seconds: float = 30.0,
) -> List[JolpicaQualifyingEntry]:
    """Fetch one round's qualifying classification.

    Raises ``JolpicaUnavailable`` when the round is absent — which is the normal
    state for a session that has just run, not a fault.
    """
    url = "{}/f1/{}/{}/qualifying.json?limit=100".format(
        base_url.rstrip("/"), season, round_number
    )
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise JolpicaUnavailable("jolpica unreachable for {}-{}: {}".format(
            season, round_number, exc)) from exc

    races = (
        payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    )
    if not races or not races[0].get("QualifyingResults"):
        raise JolpicaUnavailable(
            "jolpica has no qualifying classification for {}-{} yet".format(
                season, round_number
            )
        )

    entries = _parse(races[0]["QualifyingResults"])
    logger.info(
        "read qualifying for %s-%s from jolpica: %d rows", season, round_number, len(entries)
    )
    return entries


def _parse(rows: Sequence[dict]) -> List[JolpicaQualifyingEntry]:
    entries: List[JolpicaQualifyingEntry] = []
    for row in rows:
        driver = row.get("Driver") or {}
        # `number` is the car number and the whole reason this source is usable
        # as a fallback: it is the same join key the other two provide.
        raw_number = row.get("number") or driver.get("permanentNumber")
        try:
            car_number = int(raw_number)
            position = int(row["position"])
        except (TypeError, ValueError, KeyError):
            logger.warning("skipping a jolpica row with no usable number/position: %r", row)
            continue
        entries.append(
            JolpicaQualifyingEntry(
                position=position,
                car_number=car_number,
                driver_name=" ".join(
                    p for p in (driver.get("givenName"), driver.get("familyName")) if p
                ),
                team=(row.get("Constructor") or {}).get("name", ""),
                q1_seconds=_seconds(row.get("Q1")),
                q2_seconds=_seconds(row.get("Q2")),
                q3_seconds=_seconds(row.get("Q3")),
            )
        )

    if len(entries) < 10:
        raise JolpicaUnavailable(
            "jolpica returned only {} usable rows; a Formula 1 field is ~20".format(
                len(entries)
            )
        )
    cars = [entry.car_number for entry in entries]
    if len(set(cars)) != len(cars):
        raise JolpicaUnavailable("the same car appears twice: {}".format(cars))
    return entries
