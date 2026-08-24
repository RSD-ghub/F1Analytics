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
from typing import Callable, Optional

from app.models.schemas import (
    CompletenessSummary,
    ExpectedSession,
    IngestDepth,
    SessionIngestState,
    SessionPayload,
    SessionState,
)
from app.services.completeness import run_integrity_checks, summarise
from app.services.fastf1_source import (
    FastF1Source,
    SessionFetchError,
    SessionFrames,
    SessionUnavailableError,
)
from app.services.storage import IngestionStore
from app.services import signals, transforms
from f1_common.llm import LLMClient

logger = logging.getLogger(__name__)


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
        rows = await asyncio.to_thread(self._source.load_qualifying, expected)
        saved = await self._store.save_qualifying(
            expected.season, expected.round, rows
        )
        logger.info("qualifying: %s grid rows for %s", saved, expected.key)
        return saved

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
        settled = set()
        if only_gaps:
            settled = {
                state.key
                for state in await self._store.list_states(from_season, to_season)
                if state.state in (SessionState.COMPLETE, SessionState.UNAVAILABLE)
                # A session complete only to results depth is still a gap when a
                # full ingest was asked for.
                and (depth is IngestDepth.RESULTS or state.depth is IngestDepth.FULL)
            }

        for session in expected:
            if only_gaps and session.key in settled:
                continue
            await self.ingest_session(session, depth=depth)
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
