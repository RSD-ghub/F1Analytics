"""Ingest orchestration — fetch, extract, verify, persist, record.

This is where the completeness guarantee is actually enforced. The rule the whole
module is built around:

    **Every expected session ends with a recorded state. Always.**

The old pipeline's failure mode was structural, not accidental: a failed session
printed a warning and hit ``continue``, so the only evidence it ever existed was
stdout of a process that had already exited. Here the manifest is written first,
every attempt updates ``session_ingest_state``, and the state write happens in a
``finally``-shaped path so even an unexpected crash mid-session leaves a record
behind rather than a hole.

FastF1 is synchronous and slow, so every call into it is pushed to a worker
thread; blocking the event loop would stall the health endpoint and make the
service look dead during a backfill.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.models.schemas import (
    CompletenessSummary,
    ExpectedSession,
    IngestDepth,
    QualifyingRow,
    SessionIngestState,
    SessionPayload,
    SessionState,
)
from app.services.completeness import run_integrity_checks, summarise
from app.services.fastf1_source import (
    FastF1Source,
    RateLimited,
    SessionFetchError,
    SessionFrames,
    SessionUnavailableError,
)
from app.services.storage import IngestionStore
from app.services import fia_documents, grid_resolution, jolpica
from app.services.transforms import stable_id
from app.services.storage import DATA_COLLECTIONS, QUALIFYING
from app.services import signals, transforms
from f1_common.llm import LLMClient

logger = logging.getLogger(__name__)

#: FIA document kind -> the ``GridSource`` value we record for it.
_KIND_TO_SOURCE_NAME = {
    "final": "official_final",
    "provisional": "official_provisional",
}



def _scheduled_start(expected: ExpectedSession) -> Optional[datetime]:
    """When this session is due, in UTC, if the calendar says."""
    start = getattr(expected, "session_start_utc", None)
    if start is None:
        return None
    return start if start.tzinfo else start.replace(tzinfo=timezone.utc)


def _has_run(when: Any, now: Optional[datetime] = None) -> bool:
    """Has a scheduled session's start time passed?

    False when the calendar has no time for it — an unknown start is not an
    invitation to fetch something that may not exist yet.
    """
    if when is None:
        return False
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= when


def _due_for_retry(
    state: SessionIngestState,
    now: Optional[datetime] = None,
    scheduled: Optional[datetime] = None,
) -> bool:
    """Has an ``UNAVAILABLE`` session's moment arrived?

    ``scheduled`` is the calendar's start time, used when the stored row has no
    ``retry_after`` of its own — rows written before that field existed. Those
    are precisely the races that have since run, so falling back to the manifest
    is what heals them without a migration.

    Still false when neither is known: a session with no scheduled time is
    genuinely absent rather than merely early — a sprint weekend's FP3 is not
    going to appear later — and re-fetching those on every pass would hammer
    upstream for data that does not exist.
    """
    due = state.retry_after or scheduled
    if due is None:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= due


class IngestRunner:
    def __init__(
        self,
        source: FastF1Source,
        store: IngestionStore,
        max_attempts: int = 3,
        backoff_seconds: float = 2.0,
        sleep: Optional[Callable] = None,
        llm: Optional[LLMClient] = None,
        extract_with_llm: bool = False,
    ) -> None:
        self._source = source
        self._store = store
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        # Injectable so retry tests don't actually wait.
        self._sleep = sleep or asyncio.sleep
        self._llm = llm
        self._extract_with_llm = extract_with_llm

    # ── Manifest ─────────────────────────────────────────────────────────────

    async def refresh_manifest(self, from_season: int, to_season: int) -> int:
        """Record what the calendar says should exist, before ingesting anything.

        A season whose schedule cannot be fetched raises rather than being
        skipped: an absent manifest entry means the gap detector has nothing to
        compare against, which is exactly the invisible-failure mode this design
        exists to prevent.
        """
        total = 0
        for season in range(from_season, to_season + 1):
            sessions = await asyncio.to_thread(self._source.fetch_schedule, season)
            await self._store.upsert_expected_sessions(sessions)
            total += len(sessions)
            logger.info("manifest: season %s has %s races", season, len(sessions))
        return total

    async def refresh_weekends(self, from_season: int, to_season: int) -> int:
        """Refresh session timetables — the forward-looking calendar.

        Cheap and worth running often: F1 moves session times, and a stale
        timetable silently shifts every lock window derived from it.
        """
        total = 0
        for season in range(from_season, to_season + 1):
            weekends = await asyncio.to_thread(self._source.fetch_weekends, season)
            await self._store.upsert_weekends(weekends)
            total += len(weekends)
        return total

    async def ingest_qualifying(self, expected: ExpectedSession) -> int:
        """Fetch and store the grid for one round.

        Failures propagate rather than being recorded as a completeness gap:
        qualifying for an upcoming race not being available yet is the normal
        case, not a hole in the historical record. The caller decides whether a
        missing grid matters — which, for a post-quali forecast, it does.
        """
        try:
            rows = await asyncio.to_thread(self._source.load_qualifying, expected)
        except SessionFetchError as exc:
            # FastF1 is the preferred source but not the only one, and for the
            # hours right after a session it is the slowest. Measured before the
            # 2026 Spanish Grand Prix: the FIA had published the classification
            # while FastF1 and jolpica both still returned nothing. Since the
            # grid-aware lock windows cannot fire without this, one upstream
            # being slow should not decide whether a race gets forecast.
            logger.info(
                "FastF1 has no qualifying classification for %s (%s); trying "
                "the FIA document", expected.key, exc,
            )
            rows = await self._qualifying_from_fallbacks(expected)

        rows = await self._with_official_grid(expected, rows)
        saved = await self._store.save_qualifying(
            expected.season, expected.round, rows
        )
        logger.info("qualifying: %s grid rows for %s", saved, expected.key)
        return saved

    async def _qualifying_from_fallbacks(self, expected: ExpectedSession):
        """Order and times from whichever source has them; identity from FastF1.

        Tried in order of how quickly each publishes, which is not the order of
        how much they are trusted:

        * the **FIA**, authoritative and fastest — it had the 2026 Spanish
          classification while the other two had nothing;
        * **jolpica**, slower but sharing no machinery with the FIA. The FIA
          path builds a URL from a slug rule that is known to change (the 2024
          documents use a different scheme), and grid and qualifying documents
          share that rule, so one change takes both down together. jolpica is
          insurance against that, not against slowness.

        Identity never comes from either. Both print names their own way, and a
        name that does not match the corpus produces a driver with no history
        rather than an error — damage that spreads into every future feature
        join. So both supply (position, car number, times) and the car number is
        joined to FastF1's entry list, which publishes long before any
        classification does.
        """
        identities = await asyncio.to_thread(
            self._source.load_qualifying_entry_list, expected
        )
        if not identities:
            raise SessionFetchError(
                "no entry list for {}; cannot attribute a classification to "
                "drivers without one".format(expected.key)
            )

        failures = []
        for name, loader in (
            ("FIA", self._fia_qualifying),
            ("jolpica", self._jolpica_qualifying),
        ):
            try:
                entries, provenance = await loader(expected)
            except Exception as exc:
                failures.append("{}: {}".format(name, exc))
                continue
            rows = self._rows_from_entries(expected, entries, identities, provenance)
            if rows:
                return rows

        raise SessionFetchError(
            "no source has a qualifying classification for {} yet ({})".format(
                expected.key, "; ".join(failures)
            )
        )

    async def _fia_qualifying(self, expected: ExpectedSession):
        document = await fia_documents.fetch_qualifying_classification(
            expected.season, expected.round, expected.race_name
        )
        return document.entries, "FIA {} classification (doc {})".format(
            document.kind, document.document_number
        )

    async def _jolpica_qualifying(self, expected: ExpectedSession):
        entries = await jolpica.fetch_qualifying(expected.season, expected.round)
        return entries, "jolpica"

    def _rows_from_entries(self, expected, entries, identities, provenance):
        """Attribute a classification to drivers, or refuse it outright.

        All-or-nothing: a half-matched classification is a wrong grid, and a
        forecast locked on one is immutable.
        """
        rows, unmatched = [], []
        for entry in entries:
            known = identities.get(entry.car_number)
            if known is None:
                unmatched.append("car {} ({})".format(entry.car_number, entry.driver_name))
                continue
            driver, team = known
            rows.append(
                QualifyingRow(
                    id=stable_id("quali", expected.season, expected.round, driver),
                    season=expected.season,
                    round=expected.round,
                    race_name=expected.race_name,
                    driver=driver,
                    team=team,
                    position=entry.position,
                    driver_number=entry.car_number,
                    q1_seconds=entry.q1_seconds,
                    q2_seconds=entry.q2_seconds,
                    q3_seconds=entry.q3_seconds,
                )
            )

        if unmatched:
            raise SessionFetchError(
                "{} for {} lists {} car(s) the entry list does not: {}".format(
                    provenance, expected.key, len(unmatched), ", ".join(unmatched)
                )
            )
        logger.info(
            "qualifying for %s recovered from %s: %d rows",
            expected.key, provenance, len(rows),
        )
        return rows

    async def refresh_starting_grid(self, expected: ExpectedSession) -> dict:
        """Re-read the official grid for a round we already have qualifying for.

        Separate from ``ingest_qualifying`` because the two become available at
        different times: the classification exists the moment the session ends,
        the FIA grid document lands hours later. Polling this is cheap — one
        PDF — where re-running qualifying ingest is not, so a lock window
        waiting on penalties can check often.

        Unlike the overlay inside ``ingest_qualifying`` this reports what
        happened rather than swallowing it, because here the grid *is* the thing
        that was asked for.
        """
        rows = [
            QualifyingRow(**row)
            for row in await self._store.find_rows(
                QUALIFYING,
                {"season": expected.season, "round": expected.round},
                sort=[("position", 1)],
            )
        ]
        if not rows:
            raise SessionUnavailableError(
                "no qualifying classification stored for {}; ingest qualifying "
                "before asking for the grid".format(expected.key)
            )

        document = await fia_documents.fetch_starting_grid(
            expected.season, expected.round, expected.race_name
        )
        applied = grid_resolution.apply_starting_grid(rows, document)
        saved = await self._store.save_qualifying(
            expected.season, expected.round, applied
        )
        return {
            "season": expected.season,
            "round": expected.round,
            "grid_source": _KIND_TO_SOURCE_NAME[document.kind],
            "document_number": document.document_number,
            "document_url": document.url,
            "rows_updated": saved,
            "pit_lane_starts": [
                row.driver for row in applied if row.starts_from_pit_lane
            ],
        }

    async def refresh_pending_grids(self, season: int) -> dict:
        """Try to confirm the grid for any round still running on a stand-in.

        The gap-healing pass cannot do this. A round whose qualifying ingested
        cleanly is not a gap, so healing skips it forever — but its grid stays
        provisional until the FIA publishes, which happens hours *after*
        qualifying. Something has to come back and look again, and this is it.

        Scope is deliberately narrow: rounds that have a qualifying
        classification, have no confirmed grid yet, and whose race has not run.
        A finished race is not chased, both because its grid is already settled
        and because re-reading two dozen documents every tick would be rude to
        an upstream that is doing us a favour.
        """
        confirmed, pending, failed = [], [], []
        for weekend in await self._store.list_weekends(season, season):
            round_number = weekend["round"]
            if await self._store.count_matching(
                DATA_COLLECTIONS["results"], {"season": season, "round": round_number}
            ):
                continue  # the race has run; the grid is history now
            rows = await self._store.find_rows(
                QUALIFYING, {"season": season, "round": round_number}
            )
            expected = ExpectedSession(
                season=season,
                round=round_number,
                race_name=weekend.get("race_name", ""),
                circuit=weekend.get("circuit", ""),
            )

            # Nothing else will ingest this weekend's qualifying in time. The
            # healing pass ingests qualifying inside its per-round loop, but
            # that loop skips any round whose *race* has not run — which is
            # every round we still care about forecasting. So qualifying would
            # not land until after the race, and both grid-aware windows would
            # find no grid and never fire.
            # "No usable rows", not "no rows". A round can hold an entry list
            # with no classified positions, which is not a grid and must not
            # stop us fetching the real one.
            usable = any((row.get("position") or 999) < 999 for row in rows)
            if not usable and _has_run(weekend.get("qualifying_start_utc")):
                try:
                    saved = await self.ingest_qualifying(expected)
                    logger.info(
                        "ingested qualifying for %s-%s (%s rows) ahead of the "
                        "grid-aware lock windows", season, round_number, saved,
                    )
                    rows = await self._store.find_rows(
                        QUALIFYING, {"season": season, "round": round_number}
                    )
                except Exception as exc:
                    logger.warning(
                        "qualifying for %s-%s not ingestable yet: %s",
                        season, round_number, exc,
                    )

            if not any((row.get("position") or 999) < 999 for row in rows):
                continue
            if any(QualifyingRow(**row).has_confirmed_grid for row in rows):
                continue

            try:
                result = await self.refresh_starting_grid(expected)
                confirmed.append("{}-{} ({})".format(
                    season, round_number, result["grid_source"]))
            except fia_documents.GridDocumentUnavailable:
                pending.append("{}-{}".format(season, round_number))
            except Exception:
                logger.exception(
                    "could not confirm the grid for %s-%s", season, round_number
                )
                failed.append("{}-{}".format(season, round_number))

        if confirmed:
            logger.info("confirmed official grids: %s", ", ".join(confirmed))
        return {"confirmed": confirmed, "still_pending": pending, "failed": failed}

    async def _with_official_grid(self, expected: ExpectedSession, rows):
        """Overlay the FIA's starting grid onto the qualifying classification.

        Best-effort *by design*, and the only place in ingestion where that is
        the right call. The classification is already a usable forecast input;
        the official grid makes it correct rather than making it exist. So a
        document that is not published yet, or that will not parse, must leave
        us with a flagged-but-working grid rather than failing the whole
        qualifying ingest — which would turn a missing nicety into a missing
        session and block the lock window entirely.

        What it must never do is apply a grid it is not sure of. That failure is
        loud and total, and lives in ``grid_resolution``.
        """
        if not rows:
            return rows
        try:
            document = await fia_documents.fetch_starting_grid(
                expected.season, expected.round, expected.race_name
            )
            return grid_resolution.apply_starting_grid(rows, document)
        except fia_documents.GridDocumentUnavailable as exc:
            logger.info(
                "no official grid for %s yet; forecasts will use the qualifying "
                "classification and be flagged provisional (%s)", expected.key, exc
            )
        except (fia_documents.GridDocumentUnreadable,
                grid_resolution.GridApplicationError):
            logger.exception(
                "official grid for %s was published but could not be applied; "
                "falling back to the qualifying classification", expected.key
            )
        except Exception:
            logger.exception(
                "unexpected failure reading the official grid for %s; falling "
                "back to the qualifying classification", expected.key
            )
        return rows

    async def ingest_practice(
        self,
        expected: ExpectedSession,
        session_codes=("FP2", "FP3", "FP1"),
    ) -> int:
        """Store long-run pace for a weekend's practice sessions.

        FP2 first because it is the race-simulation session; FP3 is a fallback
        for weekends where FP2 was washed out or is a sprint-format session.

        A missing session is not a gap — sprint weekends genuinely have fewer
        practice hours — so an unavailable session is logged and skipped rather
        than recorded as a failure.
        """
        total = 0
        for code in session_codes:
            rows = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    rows = await asyncio.to_thread(
                        self._source.load_practice, expected, code
                    )
                    break
                except SessionUnavailableError as exc:
                    # Never scheduled, or genuinely empty. Retrying cannot help.
                    logger.debug("%s unavailable for %s: %s", code, expected.key, exc)
                    break
                except SessionFetchError as exc:
                    if attempt == self._max_attempts:
                        logger.warning(
                            "%s failed for %s after %s attempts: %s",
                            code, expected.key, attempt, exc,
                        )
                    else:
                        await self._sleep(self._backoff_seconds * attempt)
            if not rows:
                continue
            total += await self._store.save_practice(
                expected.season, expected.round, code, rows
            )
            # One good session per weekend is enough; FP2 is preferred and the
            # others exist only as fallbacks for sprint or washed-out weekends.
            break
        if total:
            logger.info("practice: %s pace rows for %s", total, expected.key)
        return total

    # ── One session ──────────────────────────────────────────────────────────

    async def ingest_session(
        self,
        expected: ExpectedSession,
        depth: IngestDepth = IngestDepth.FULL,
    ) -> SessionIngestState:
        """Ingest one race, always returning (and persisting) its outcome state.

        ``depth=RESULTS`` fetches only the classification and qualifying, which
        is ~2s per session against ~30s for a full load. That is what makes a
        multi-decade training corpus practical to build through this pipeline —
        and building it here rather than in a side script means it inherits the
        completeness guarantee instead of reinventing a worse one.
        """
        previous = await self._store.get_state(expected.season, expected.round)
        state = SessionIngestState(
            season=expected.season,
            round=expected.round,
            depth=depth,
            attempts=previous.attempts if previous else 0,
        )

        try:
            frames = await self._fetch_with_retry(expected, state, depth)
        except SessionUnavailableError as exc:
            state.state = SessionState.UNAVAILABLE
            state.reason = str(exc)
            # A session that has not happened yet is unavailable *for now*.
            # Dating that makes the difference between "no data" and "no data
            # yet" survive into the gap accounting, instead of both collapsing
            # into a permanent excuse not to look again.
            state.retry_after = _scheduled_start(expected)
        except SessionFetchError as exc:
            # Retries are exhausted. This is now an open gap, not a warning.
            state.state = SessionState.FAILED
            state.reason = str(exc)
            logger.error("ingest failed for %s: %s", expected.key, exc)
        except Exception as exc:  # noqa: BLE001 - unexpected, but must still be recorded
            state.state = SessionState.FAILED
            state.reason = "unexpected error: {}".format(exc)
            logger.exception("unexpected ingest failure for %s", expected.key)
        else:
            payload = build_payload(frames)
            report = run_integrity_checks(payload, depth)
            state.row_counts = await self._store.save_payload(payload, depth=depth)
            state.integrity = report
            await self._extract_signals(payload)

            if report.passed:
                state.state = SessionState.COMPLETE
                state.completed_at = _now()
            else:
                # The data is stored — partial data still beats none — but the
                # session stays an open gap so a re-run will revisit it.
                state.state = SessionState.PARTIAL
                state.reason = "; ".join(
                    "{}: {}".format(check.name, check.detail)
                    for check in report.failures
                )
                logger.warning("partial ingest for %s: %s", expected.key, state.reason)

        await self._store.record_state(state)
        return state

    async def _extract_signals(self, payload: SessionPayload) -> None:
        """Derive structured signals from race control text. Best-effort.

        Runs after the session is already stored and its state decided, and every
        failure is swallowed with a log line. That is deliberate: extraction
        quality must never be able to turn a complete session into a gap, and the
        LLM half of it depends on a third-party API.
        """
        session = payload.session
        try:
            rows = signals.extract_rule_based(
                session.season, session.round, payload.race_control
            )
            if self._llm is not None and self._extract_with_llm:
                rows = rows + await signals.extract_with_llm(
                    self._llm, session.season, session.round, payload.race_control
                )
            await self._store.save_signals(session.season, session.round, rows)
            logger.debug("signals: %s extracted for %s", len(rows), session.key)
        except Exception:
            logger.exception("signal extraction failed for %s (ignored)", session.key)

    async def _fetch_with_retry(
        self,
        expected: ExpectedSession,
        state: SessionIngestState,
        depth: IngestDepth = IngestDepth.FULL,
    ) -> SessionFrames:
        """Retry transient failures with exponential backoff.

        ``SessionUnavailableError`` is re-raised immediately — a race that has not
        happened yet will not start existing because we asked three times.
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, self._max_attempts + 1):
            state.attempts += 1
            state.last_attempt_at = _now()
            try:
                if depth is IngestDepth.RESULTS:
                    return await asyncio.to_thread(
                        self._source.load_race_frames_results_only, expected
                    )
                return await asyncio.to_thread(self._source.load_race, expected)
            except SessionUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified by the caller
                last_error = exc
                if attempt < self._max_attempts:
                    delay = self._backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "ingest attempt %s/%s failed for %s (%s); retrying in %.1fs",
                        attempt,
                        self._max_attempts,
                        expected.key,
                        exc,
                        delay,
                    )
                    await self._sleep(delay)

        raise SessionFetchError(
            "{} attempts failed for {}: {}".format(
                self._max_attempts, expected.key, last_error
            )
        )

    # ── Backfill ─────────────────────────────────────────────────────────────

    async def run_backfill(
        self,
        from_season: int,
        to_season: int,
        only_gaps: bool = False,
        depth: IngestDepth = IngestDepth.FULL,
    ) -> CompletenessSummary:
        """Ingest a season range, then report what is still missing.

        ``only_gaps=True`` is the healing pass: it skips sessions already complete
        and races that have not run, which is what makes a scheduled re-run cheap
        enough to do routinely.
        """
        await self.refresh_manifest(from_season, to_season)
        expected = await self._store.list_expected_sessions(from_season, to_season)

        # Read every state once rather than per session; a 2010-2026 backfill is
        # ~350 races and the per-session query would dominate the skip path.
        # When a session is *due* comes from the calendar, not only from the
        # stored state. Rows written before ``retry_after`` existed carry no
        # date, and deriving it here means they heal on the next pass instead of
        # needing a migration — which matters because the rows in that position
        # are exactly the races that already ran.
        due_at = {
            session.key: _scheduled_start(session) for session in expected
        }

        settled = set()
        if only_gaps:
            settled = {
                state.key
                for state in await self._store.list_states(from_season, to_season)
                if (
                    state.state is SessionState.COMPLETE
                    or (
                        state.state is SessionState.UNAVAILABLE
                        and not _due_for_retry(state, scheduled=due_at.get(state.key))
                    )
                )
                # A session complete only to results depth is still a gap when a
                # full ingest was asked for.
                and (depth is IngestDepth.RESULTS or state.depth is IngestDepth.FULL)
            }

        for session in expected:
            if only_gaps and session.key in settled:
                continue
            try:
                await self.ingest_session(session, depth=depth)
            except RateLimited as exc:
                # Stop, do not soldier on. An hourly quota cannot be waited out
                # inside a retry loop, so continuing would mark every remaining
                # session as incomplete for a reason that has nothing to do with
                # them — turning one recoverable pause into a hundred spurious
                # gaps. The run is resumable; the next pass picks up here.
                logger.error(
                    "stopping the backfill at %s: %s. Re-run once the quota "
                    "resets; completed sessions are not re-fetched.",
                    session.key, exc,
                )
                break
            if depth is IngestDepth.RESULTS:
                # Qualifying is part of "results depth": it is the other half of
                # what the model trains on, and it loads just as cheaply.
                try:
                    await self.ingest_qualifying(session)
                except Exception as exc:
                    logger.warning("qualifying unavailable for %s: %s", session.key, exc)

        summary = await self.status(from_season, to_season, depth=depth)
        if summary.open_gaps:
            logger.warning(
                "backfill %s-%s finished with %s open gap(s): %s",
                from_season,
                to_season,
                len(summary.open_gaps),
                ", ".join(summary.open_gaps[:10]),
            )
        else:
            logger.info("backfill %s-%s complete, no gaps", from_season, to_season)
        return summary

    # ── Status ───────────────────────────────────────────────────────────────

    async def status(
        self,
        from_season: int,
        to_season: int,
        depth: IngestDepth = IngestDepth.FULL,
    ) -> CompletenessSummary:
        expected = await self._store.list_expected_sessions(from_season, to_season)
        states = await self._store.list_states(from_season, to_season)
        return summarise(
            from_season, to_season, [s.key for s in expected], states, depth=depth
        )


def build_payload(frames: SessionFrames) -> SessionPayload:
    """Turn raw session frames into typed rows.

    Driver names are resolved once from the results table and threaded through
    every other extractor, so the whole dataset is keyed on full names rather than
    a mix of names and three-letter codes.
    """
    session = frames.session
    driver_names = transforms.build_driver_name_map(frames.results)

    return SessionPayload(
        session=session,
        results=transforms.extract_results(session, frames.results),
        laps=transforms.extract_laps(session, frames.laps, driver_names),
        stints=transforms.extract_stints(session, frames.laps, driver_names),
        pit_stops=transforms.extract_pit_stops(session, frames.laps, driver_names),
        weather=transforms.extract_weather(session, frames.weather),
        race_control=transforms.extract_race_control(session, frames.race_control),
        telemetry=list(frames.telemetry),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
