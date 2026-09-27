"""Where the training corpus starts, and why both scripts must agree.

2014 is the turbo-hybrid reset. Measured on 2024-2025 held out, it beat 2010
on both markets while using 73 fewer races — the model has been
feature-limited rather than data-limited for some time, so the older seasons
were paying rent in noise.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_the_corpus_starts_at_the_hybrid_reset():
    from train_model import TRAINING_FROM_SEASON

    from app.training.eras import era_for

    assert TRAINING_FROM_SEASON == 2014
    assert era_for(TRAINING_FROM_SEASON) == "hybrid_v6", (
        "the cutoff should land on a regulation boundary, not inside an era"
    )


def test_the_challenger_is_fitted_on_the_same_span_as_the_champion():
    """A challenger trained on a different span is not a comparison of models,
    it is a comparison of corpora wearing a model's name. Both scripts take
    the cutoff from one constant so they cannot drift apart."""
    import retrain
    import train_model

    assert retrain.TRAINING_FROM_SEASON is train_model.TRAINING_FROM_SEASON


def test_the_cutoff_does_not_starve_the_serving_grid_weight():
    """The era bucket that serves forecasts must still contain training races.

    A bucket with no races fits to exactly zero, and a zero grid weight means
    the served model ignores starting position — the strongest feature it has —
    while still producing well-formed probabilities. It has happened once
    already, from a finer era split. Raising the cutoff is the other way to
    cause it.
    """
    from train_model import TRAINING_FROM_SEASON

    from app.services.model import SERVING_ERA_BUCKET
    from app.training.eras import era_bucket

    assert era_bucket(TRAINING_FROM_SEASON, 2026) == SERVING_ERA_BUCKET, (
        "the first trainable season falls outside the serving bucket, so the "
        "weight that matters would be fitted on nothing"
    )
