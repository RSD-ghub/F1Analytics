"""The watchdog: a missed lock or an unscored race reaches a person, once."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models.schemas import HeldPrediction, NameMismatch, RaceOutcome
from app.services import alerts as alerts_module
from app.services.alerts import Notifier
from app.services.clients import CalendarWeekend, LockedPrediction, UpstreamUnavailable
from app.services.watchdog import LOOKBACK, SCORE_GRACE, Watchdog

RACE_START = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)


def _weekend(start=RACE_START, sprint=None):
    return CalendarWeekend(
        season=2026, round=19, race_name="Mexico City Grand Prix",
        race_start_utc=start, is_sprint_weekend=sprint is not None,
        sprint_start_utc=sprint,
    )


def _prediction(window, pid=None, start=RACE_START):
    return LockedPrediction(
        prediction_id=pid or window, season=2026, round=19, window=window,
        locked_at=start - timedelta(hours=2), race_start_utc=start,
    )


class _Ingestion:
    def __init__(self, weekends, fail=False):
        self.weekends, self.fail = weekends, fail

    async def calendar(self, season):
        if self.fail:
            raise UpstreamUnavailable("ingestion down")
        return [w for w in self.weekends if w.season == season]


class _Predictions:
    def __init__(self, predictions):
        self.predictions = predictions

    async def predictions_for(self, season, round_number, event="race"):
        return list(self.predictions)


class _Store:
    def __init__(self, scored=(), outcome=None):
        self.scored, self.outcome, self.sent = set(scored), outcome, {}

    async def get_score(self, prediction_id):
        return object() if prediction_id in self.scored else None

    async def get_outcome(self, season, round_number, event="race"):
        return self.outcome if event == "race" else None

    async def alert_sent(self, key):
        return key in self.sent

    async def mark_alert_sent(self, key, message, sent_at):
        self.sent[key] = message


class _Notifier:
    def __init__(self, deliver=True, configured=True):
        self.deliver, self.configured, self.messages = deliver, configured, []

    async def send(self, title, message):
        self.messages.append((title, message))
        return self.deliver


ALL = ["pre_quali", "post_quali", "final_grid"]


def _watchdog(predictions, store=None, weekends=None, notifier=None, fail=False):
    return Watchdog(
        _Ingestion(weekends or [_weekend()], fail=fail),
        _Predictions(predictions),
        store or _Store(scored=[p.prediction_id for p in predictions]),
        notifier or _Notifier(),
    )


async def test_a_weekend_with_every_window_locked_and_scored_is_quiet():
    preds = [_prediction(w) for w in ALL]
    assert await _watchdog(preds).check(RACE_START + timedelta(days=2)) == []


async def test_a_missing_window_is_reported_once_the_race_has_started():
    preds = [_prediction("pre_quali"), _prediction("post_quali")]
    [alert] = await _watchdog(preds).check(RACE_START + timedelta(hours=1))
    assert "Missed forecast lock" in alert.title
    assert "final_grid" in alert.message


async def test_nothing_is_reported_before_the_race_starts():
    assert await _watchdog([]).check(RACE_START - timedelta(hours=1)) == []


async def test_old_races_are_not_reported():
    """A first run must not page about the whole season."""
    assert await _watchdog([]).check(RACE_START + LOOKBACK + timedelta(hours=1)) == []


async def test_a_sprint_weekend_expects_the_sprint_lock_too():
    sprint = RACE_START - timedelta(days=1)
    preds = [_prediction(w) for w in ALL]
    [alert] = await _watchdog(preds, weekends=[_weekend(sprint=sprint)]).check(
        RACE_START + timedelta(hours=1)
    )
    assert "sprint" in alert.message


async def test_an_unscored_race_is_reported_after_the_grace():
    preds = [_prediction(w) for w in ALL]
    store = _Store(scored=["pre_quali"])
    early = await _watchdog(preds, store).check(RACE_START + timedelta(hours=2))
    assert early == []

    [alert] = await _watchdog(preds, store).check(RACE_START + SCORE_GRACE + timedelta(minutes=1))
    assert alert.title.startswith("Unscored race")
    assert "2 locked forecast(s)" in alert.message
    assert "no results" in alert.message


async def test_a_held_race_says_which_names_clash():
    preds = [_prediction("final_grid")]
    outcome = RaceOutcome(
        season=2026, round=19, reconciled_at=RACE_START,
        held=[HeldPrediction(prediction_id="final_grid", window="final_grid", mismatches=[
            NameMismatch(forecast_name="Kimi Antonelli", result_name="Andrea Kimi Antonelli")
        ])],
    )
    watchdog = Watchdog(_Ingestion([_weekend()]), _Predictions(preds),
                        _Store(outcome=outcome), _Notifier(),
                        expected_windows=["final_grid"])
    [alert] = await watchdog.check(RACE_START + timedelta(days=2))
    assert "Kimi Antonelli / Andrea Kimi Antonelli" in alert.message


async def test_late_locks_are_not_reported_as_unscored():
    late = _prediction("final_grid")
    late.locked_at = RACE_START + timedelta(minutes=5)
    watchdog = Watchdog(_Ingestion([_weekend()]), _Predictions([late]), _Store(),
                        _Notifier(), expected_windows=["final_grid"])
    assert await watchdog.check(RACE_START + timedelta(days=2)) == []


async def test_each_alert_is_sent_once():
    notifier = _Notifier()
    store = _Store()
    watchdog = _watchdog([], store=store, notifier=notifier)
    now = RACE_START + timedelta(hours=1)

    assert len(await watchdog.run(now)) == 1
    assert await watchdog.run(now) == []
    assert len(notifier.messages) == 1


async def test_a_failed_delivery_is_retried_next_time():
    notifier = _Notifier(deliver=False)
    store = _Store()
    watchdog = _watchdog([], store=store, notifier=notifier)
    now = RACE_START + timedelta(hours=1)

    await watchdog.run(now)
    assert store.sent == {}
    notifier.deliver = True
    assert len(await watchdog.run(now)) == 1


async def test_without_a_webhook_the_alert_is_logged_once_not_hourly():
    notifier = _Notifier(deliver=False, configured=False)
    store = _Store()
    watchdog = _watchdog([], store=store, notifier=notifier)
    now = RACE_START + timedelta(hours=1)

    await watchdog.run(now)
    await watchdog.run(now)
    assert len(notifier.messages) == 1


async def test_an_ingestion_outage_is_not_an_exception():
    assert await _watchdog([], fail=True).run(RACE_START + timedelta(hours=1)) == []


# ── The webhook ──────────────────────────────────────────────────────────────


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, status=200):
        self.status, self.requests = status, []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return httpx.Response(self.status)


@pytest.fixture
def transport(monkeypatch):
    holder = _Transport()
    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=holder, **kwargs)

    monkeypatch.setattr(alerts_module.httpx, "AsyncClient", client)
    return holder


async def test_text_format_suits_ntfy(transport):
    assert await Notifier("https://ntfy.sh/topic").send("Missed lock: São Paulo", "body")
    request = transport.requests[0]
    assert request.content == b"body"
    assert request.headers["Title"] == "Missed lock: Sao Paulo"


async def test_json_format_suits_slack_and_discord(transport):
    assert await Notifier("https://hooks.example/x", "json").send("T", "body")
    body = transport.requests[0].read().decode()
    assert '"text"' in body and '"content"' in body


async def test_no_url_sends_nothing(transport):
    assert not await Notifier("").send("T", "body")
    assert transport.requests == []


async def test_a_webhook_error_is_reported_as_not_delivered(transport):
    transport.status = 500
    assert not await Notifier("https://ntfy.sh/topic").send("T", "body")


def test_the_watchdog_route_lists_problems_without_sending():
    from fastapi.testclient import TestClient

    from app.dependencies import get_watchdog
    from app.main import app

    notifier = _Notifier()
    now = datetime.now(timezone.utc)
    weekend = _weekend(start=now - timedelta(hours=1))
    app.dependency_overrides[get_watchdog] = lambda: Watchdog(
        _Ingestion([weekend]), _Predictions([]), _Store(), notifier
    )
    try:
        # No context manager: the app's startup would launch the scheduler.
        body = TestClient(app).get("/watchdog").json()
    finally:
        app.dependency_overrides.clear()

    assert body[0]["title"].startswith("Missed forecast lock")
    assert notifier.messages == []
