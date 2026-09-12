"""The top-pick hit rate, which was computed and then thrown away.

``top_pick_was_correct`` existed and was tested; nothing ever passed its result
into the aggregate, because ``build_window_record`` took a ``hit_rates``
argument that no caller supplied. The field was therefore ``None`` on every
window forever — a reported metric that could never report anything.
"""

from datetime import datetime, timezone

from app.models.schemas import MarketScore, Market, PredictionScore
from app.services.track_record import build_window_record


def _score(window="post_quali", top_pick=None):
    return PredictionScore(
        score_id="s", prediction_id="p", season=2026, round=13, window=window,
        model_version="v4", scored_at=datetime.now(timezone.utc),
        markets=[MarketScore(market=Market.WIN, brier=0.03)],
        top_pick_correct=top_pick,
    )


def test_a_correct_favourite_gives_a_full_hit_rate():
    record = build_window_record("post_quali", [_score(top_pick=True)])
    assert record.top_pick_hit_rate == 1.0


def test_a_wrong_favourite_gives_zero():
    record = build_window_record("post_quali", [_score(top_pick=False)])
    assert record.top_pick_hit_rate == 0.0


def test_it_averages_across_races():
    scores = [_score(top_pick=True), _score(top_pick=True), _score(top_pick=False)]
    record = build_window_record("post_quali", scores)
    assert record.top_pick_hit_rate == 2 / 3


def test_a_window_that_names_no_favourite_has_no_hit_rate():
    """Pre-quali publishes no win probability. Counting that as a miss would
    report a 0% hit rate for a question it deliberately declined to answer —
    the same "absent is not wrong" distinction as p_win = None."""
    record = build_window_record("pre_quali", [_score("pre_quali", top_pick=None)])
    assert record.top_pick_hit_rate is None


def test_declining_races_do_not_drag_the_average_down():
    scores = [_score(top_pick=True), _score(top_pick=None)]
    record = build_window_record("post_quali", scores)
    assert record.top_pick_hit_rate == 1.0
