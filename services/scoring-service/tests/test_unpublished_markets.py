"""Scoring a forecast that deliberately declines to predict a market.

The pre-quali window no longer publishes a win probability: measured over two
held-out seasons its win-market skill was roughly zero with huge variance, so
the honest move is to make no claim rather than a worthless one.

That creates a specific trap, and these tests exist to keep it shut. A missing
probability must be treated as **absent**, never as 0.0. Zero is a confident
assertion that a driver cannot win; absence is the refusal to guess. If scoring
conflated them, every real winner would register as a confident miss, and a
forecast would be punished precisely for being honest about its limits — the
exact opposite of what a track record is for.
"""

import pytest

from app.models.schemas import Market
from app.services.clients import (
    DriverProbabilityRow,
    LockedPrediction,
    RaceResultRow,
)
from app.services.reconciliation import (
    build_outcome,
    score_prediction,
    top_pick_was_correct,
)


def _results():
    return [
        RaceResultRow(season=2026, round=5, driver=name, position=position,
                      classified_position=str(position), status="Finished")
        for name, position in [
            ("Alpha", 1), ("Bravo", 2), ("Charlie", 3), ("Delta", 4),
            ("Echo", 5), ("Foxtrot", 11),
        ]
    ]


OUTCOME = build_outcome(2026, 5, _results())


def _pre_quali():
    """What the pre-quali window now produces: podium and points, no win."""
    return LockedPrediction(
        prediction_id="pre", season=2026, round=5, window="pre_quali",
        model_version="v3", published_markets=["podium", "points"],
        driver_probabilities=[
            DriverProbabilityRow(driver="Alpha", p_win=None, p_podium=0.7, p_points=0.95),
            DriverProbabilityRow(driver="Bravo", p_win=None, p_podium=0.6, p_points=0.9),
            DriverProbabilityRow(driver="Charlie", p_win=None, p_podium=0.5, p_points=0.85),
            DriverProbabilityRow(driver="Delta", p_win=None, p_podium=0.4, p_points=0.8),
        ],
    )


def _post_quali():
    return LockedPrediction(
        prediction_id="post", season=2026, round=5, window="post_quali",
        model_version="v3", published_markets=["win", "podium", "points"],
        driver_probabilities=[
            DriverProbabilityRow(driver="Alpha", p_win=0.5, p_podium=0.8, p_points=0.97),
            DriverProbabilityRow(driver="Bravo", p_win=0.3, p_podium=0.7, p_points=0.93),
            DriverProbabilityRow(driver="Charlie", p_win=0.2, p_podium=0.6, p_points=0.9),
        ],
    )


# ── The core rule ────────────────────────────────────────────────────────────


def test_unpublished_market_is_not_scored_at_all():
    score = score_prediction(_pre_quali(), OUTCOME)

    assert score.market(Market.WIN) is None
    assert {m.market for m in score.markets} == {Market.PODIUM, Market.POINTS}


def test_absent_win_is_not_scored_as_zero():
    """The trap.

    Reading None as 0.0 would score a confident 'Alpha cannot win' against a
    race Alpha won — a large Brier penalty invented entirely by the scoring
    layer, for a claim the forecast never made.
    """
    score = score_prediction(_pre_quali(), OUTCOME)

    assert all(m.brier > 0 or m.drivers_scored > 0 for m in score.markets)
    assert Market.WIN not in {m.market for m in score.markets}


def test_the_published_markets_are_still_scored_normally():
    score = score_prediction(_pre_quali(), OUTCOME)
    podium = score.market(Market.PODIUM)

    assert podium.drivers_scored == 4
    assert podium.skill_vs_baseline > 0


def test_post_quali_still_scores_all_three():
    score = score_prediction(_post_quali(), OUTCOME)

    assert {m.market for m in score.markets} == set(Market)
    assert score.market(Market.WIN).drivers_scored == 3


# ── Aggregate honesty ────────────────────────────────────────────────────────


def test_a_declined_market_cannot_flatter_the_record():
    """Declining to predict must not look like predicting well.

    A skipped market contributes no score at all, so it can neither help nor
    hurt the aggregate — the record simply shows fewer win-market predictions.
    """
    pre = score_prediction(_pre_quali(), OUTCOME)
    post = score_prediction(_post_quali(), OUTCOME)

    assert len(pre.markets) == 2
    assert len(post.markets) == 3


def test_top_pick_is_undefined_without_win_probabilities():
    """No win claim means no top pick — not a wrong one."""
    assert top_pick_was_correct(_pre_quali(), OUTCOME) is False
    assert top_pick_was_correct(_post_quali(), OUTCOME) is True


# ── Backwards compatibility ──────────────────────────────────────────────────


def test_a_prediction_without_the_field_is_scored_on_everything():
    """Anything locked before the policy existed keeps its original meaning.

    A forecast that did claim a win probability must go on being scored on it;
    silently dropping that would rewrite history in the model's favour.
    """
    legacy = LockedPrediction(
        prediction_id="legacy", season=2026, round=5, window="pre_quali",
        model_version="v2", published_markets=[],
        driver_probabilities=[
            DriverProbabilityRow(driver="Alpha", p_win=0.4, p_podium=0.8, p_points=0.95),
            DriverProbabilityRow(driver="Bravo", p_win=0.6, p_podium=0.7, p_points=0.9),
        ],
    )
    score = score_prediction(legacy, OUTCOME)

    assert score.market(Market.WIN) is not None
    assert score.market(Market.WIN).drivers_scored == 2


# ── Calibration sampling ─────────────────────────────────────────────────────


def test_calibration_samples_skip_unpublished_markets():
    """The second code path that has to honour "absent, not zero".

    ``score_prediction`` already skipped unpublished markets; the calibration
    sampler did not, and a ``None`` win probability reached a bucketer that
    computes ``int(p * buckets)``. That was a live 500 on /track-record.
    """
    from app.services.reconciler import _probability_rows

    rows = _probability_rows(_pre_quali())
    markets = {market for _, market, _, _ in rows}

    assert Market.WIN not in markets
    assert markets == {Market.PODIUM, Market.POINTS}
    assert all(probability is not None for _, _, _, probability in rows)


def test_calibration_samples_include_every_published_market():
    from app.services.reconciler import _probability_rows

    markets = {market for _, market, _, _ in _probability_rows(_post_quali())}
    assert markets == set(Market)
