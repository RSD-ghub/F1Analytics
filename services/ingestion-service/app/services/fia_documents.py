"""The FIA's official starting grid — the only pre-race source of the real grid.

Why this module exists: the model is trained on ``grid_position`` taken from
*race results*, which is the true, penalty-adjusted grid. Before a race has run
there is no such column — FastF1 exposes ``GridPosition`` on qualifying sessions
but it is empty for every session checked, historical and current alike — so
serving fell back to the qualifying classification. That is a train/serve skew,
and it is worst exactly when it matters most: at the 2024 Belgian Grand Prix
fourteen of nineteen drivers started somewhere other than where they qualified,
Verstappen by ten places.

The FIA publishes the grid as a PDF within a few hours of qualifying, comfortably
inside the post-quali lock window. Two facts make this cheap and robust:

* the document URL is *derivable* from the season and event name, so there is no
  index page to scrape — the slug rule resolved all 24 events of 2025;
* the PDF's text layer is structured (``position  car  driver  time``), with the
  car number as a stable join key that survives spelling and accent differences.

The 2024 and earlier documents use a different URL scheme and are deliberately
not chased: for any race that has already run we have the true grid from race
results, so this source is only ever needed for the current weekend.

**Nothing here degrades quietly.** A document that cannot be fetched raises
``GridDocumentUnavailable`` (an expected state — the grid is not published yet),
and one that parses into something structurally implausible raises
``GridDocumentUnreadable``. Applying a half-read grid would silently corrupt the
single largest feature in the model, which is far worse than not having it.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from io import BytesIO
from typing import List, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

FIA_DOCUMENT_ROOT = "https://www.fia.com/system/files/decision-document"

#: Preferred first. The provisional grid is published earlier but can still be
#: revised by a later stewards' decision; the final one supersedes it.
GRID_DOCUMENT_KINDS = ("final", "provisional")


class GridDocumentUnavailable(RuntimeError):
    """No starting-grid document is published for this event yet.

    An expected state for a weekend whose qualifying has just ended, not a fault.
    """


class GridDocumentUnreadable(RuntimeError):
    """A document was fetched but did not parse into a credible grid.

    Raised rather than returning partial rows: the grid is the model's largest
    single input, so a half-parsed one is worse than none at all.
    """


@dataclass(frozen=True)
class GridEntry:
    """One driver's confirmed starting slot."""

    position: int
    car_number: int
    driver_name: str
    team: str = ""
    #: True when the driver is required to start from the pit lane. They are
    #: still given a numeric position — see ``_assign_pit_lane_positions`` for
    #: why that number is what it is.
    from_pit_lane: bool = False

    @property
    def surname_key(self) -> str:
        """Fallback join key when a car number is not recorded on our side."""
        return normalise_name(self.driver_name).split()[-1] if self.driver_name else ""


@dataclass(frozen=True)
class StartingGridDocument:
    """A parsed FIA starting grid, with the provenance needed to cite it."""

    season: int
    round: int
    event_name: str
    kind: str
    url: str
    entries: Sequence[GridEntry] = field(default_factory=tuple)
    document_number: Optional[int] = None

    @property
    def is_final(self) -> bool:
        return self.kind == "final"


def normalise_name(name: str) -> str:
    """Accent- and case-insensitive form used for name comparison."""
    stripped = unicodedata.normalize("NFKD", name or "")
    stripped = stripped.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", stripped).strip().lower()


def event_slug(event_name: str) -> str:
    """FIA's URL slug for an event name.

    ``"São Paulo Grand Prix"`` becomes ``"sao_paulo_grand_prix"``. Verified
    against every 2025 event.
    """
    return re.sub(r"[^a-z0-9]+", "_", normalise_name(event_name)).strip("_")


def document_url(season: int, event_name: str, kind: str = "final") -> str:
    if kind not in GRID_DOCUMENT_KINDS:
        raise ValueError("unknown grid document kind: {}".format(kind))
    return "{}/{}_{}_-_{}_starting_grid.pdf".format(
        FIA_DOCUMENT_ROOT, season, event_slug(event_name), kind
    )


# ── Parsing ──────────────────────────────────────────────────────────────────

#: ``1 4 Lando NORRIS 1:09.511`` — grid slot, car number, driver, optional time.
_GRID_ROW = re.compile(
    r"^(\d{1,2})\s+(\d{1,2})\s+([A-Za-z][A-Za-z'\-\. ]+?)\s*\*?\s*(?:\d:\d{2}\.\d{3})?$"
)
#: ``1 Max VERSTAPPEN * 1:10.403`` — in the pit-lane block the *only* leading
#: number is the car number. Reading such a line with ``_GRID_ROW`` semantics
#: would put a pit-lane starter on pole, so the two are told apart by section
#: state rather than by pattern alone.
_PIT_ROW = re.compile(
    r"^(\d{1,2})\s+([A-Za-z][A-Za-z'\-\. ]+?)\s*\*?\s*(?:\d:\d{2}\.\d{3})?$"
)
_PIT_HEADER = re.compile(r"PIT\s*LANE", re.IGNORECASE)
_END_OF_GRID = re.compile(r"^\s*(NOTES|\*?\s*PENALTIES)\b", re.IGNORECASE)
_DOC_NUMBER = re.compile(r"^\s*Doc\s+(\d+)\b", re.IGNORECASE)


def parse_grid_pdf(data: bytes) -> Tuple[List[GridEntry], Optional[int]]:
    """Extract the starting grid from a FIA grid document.

    Returns ``(entries, document_number)``. Raises ``GridDocumentUnreadable``
    when the result is not a structurally credible grid.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise GridDocumentUnreadable("pdfplumber is required to read grid documents") from exc

    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:
        raise GridDocumentUnreadable("could not read the grid PDF: {}".format(exc)) from exc

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _parse_lines(lines)


def _parse_lines(lines: Sequence[str]) -> Tuple[List[GridEntry], Optional[int]]:
    grid: List[GridEntry] = []
    pit_lane: List[GridEntry] = []
    document_number: Optional[int] = None
    in_pit_block = False

    for index, line in enumerate(lines):
        if document_number is None:
            found = _DOC_NUMBER.match(line)
            if found:
                document_number = int(found.group(1))

        if _END_OF_GRID.match(line):
            break
        if _PIT_HEADER.search(line):
            in_pit_block = True
            continue

        team = _team_after(lines, index)
        if in_pit_block:
            match = _PIT_ROW.match(line)
            if match:
                pit_lane.append(
                    GridEntry(
                        position=0,
                        car_number=int(match.group(1)),
                        driver_name=match.group(2).strip(),
                        team=team,
                        from_pit_lane=True,
                    )
                )
            continue

        match = _GRID_ROW.match(line)
        if match:
            position, car, name = int(match.group(1)), int(match.group(2)), match.group(3)
            # The grid is strictly 1..N. Anything else means the pattern matched
            # something that is not a grid row, so stop trusting the parse.
            if position != len(grid) + 1:
                continue
            grid.append(
                GridEntry(
                    position=position,
                    car_number=car,
                    driver_name=name.strip(),
                    team=team,
                )
            )

    entries = grid + _assign_pit_lane_positions(pit_lane, len(grid))
    _validate(entries)
    return entries, document_number


def _team_after(lines: Sequence[str], index: int) -> str:
    """The team is printed on the line below the driver."""
    if index + 1 >= len(lines):
        return ""
    candidate = lines[index + 1]
    if _GRID_ROW.match(candidate) or _PIT_ROW.match(candidate) or _END_OF_GRID.match(candidate):
        return ""
    return candidate if not _PIT_HEADER.search(candidate) else ""


def _assign_pit_lane_positions(
    pit_lane: Sequence[GridEntry], grid_size: int
) -> List[GridEntry]:
    """Number pit-lane starters as the slots behind the last grid position.

    This is not a modelling convenience — it is what the training data says. At
    the 2025 São Paulo Grand Prix the grid was eighteen cars and the two pit-lane
    starters appear in race results as grid 19 and 20, in the order the FIA
    document lists them. Serving has to encode them the same way or the model
    sees a value it was never trained on.
    """
    return [
        GridEntry(
            position=grid_size + offset + 1,
            car_number=entry.car_number,
            driver_name=entry.driver_name,
            team=entry.team,
            from_pit_lane=True,
        )
        for offset, entry in enumerate(pit_lane)
    ]


def _validate(entries: Sequence[GridEntry]) -> None:
    if len(entries) < 10:
        raise GridDocumentUnreadable(
            "parsed only {} grid slots; a Formula 1 grid is ~20".format(len(entries))
        )
    positions = [entry.position for entry in entries]
    if positions != list(range(1, len(entries) + 1)):
        raise GridDocumentUnreadable("grid positions are not contiguous: {}".format(positions))
    cars = [entry.car_number for entry in entries]
    if len(set(cars)) != len(cars):
        raise GridDocumentUnreadable("the same car appears twice: {}".format(cars))


# ── Fetching ─────────────────────────────────────────────────────────────────


async def fetch_starting_grid(
    season: int,
    round_number: int,
    event_name: str,
    timeout_seconds: float = 30.0,
    kinds: Sequence[str] = GRID_DOCUMENT_KINDS,
) -> StartingGridDocument:
    """Fetch and parse the official grid, preferring the final document.

    Raises ``GridDocumentUnavailable`` when neither document is published — the
    normal state between the end of qualifying and the stewards publishing.
    """
    attempted: List[str] = []
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        for kind in kinds:
            url = document_url(season, event_name, kind)
            attempted.append(url)
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                logger.warning("grid document fetch failed for %s: %s", url, exc)
                continue
            if response.status_code == 404:
                continue
            response.raise_for_status()

            entries, document_number = parse_grid_pdf(response.content)
            logger.info(
                "read the %s starting grid for %s %s: %d slots (doc %s)",
                kind, season, event_name, len(entries), document_number,
            )
            return StartingGridDocument(
                season=season,
                round=round_number,
                event_name=event_name,
                kind=kind,
                url=url,
                entries=tuple(entries),
                document_number=document_number,
            )

    raise GridDocumentUnavailable(
        "no starting grid published for {} {} (tried {})".format(
            season, event_name, ", ".join(attempted)
        )
    )
