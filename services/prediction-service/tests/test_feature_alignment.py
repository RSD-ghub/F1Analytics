"""``BASE_FEATURES`` and ``feature_vector`` are two halves of one definition.

One holds the names, the other the values, positionally parallel. Adding a
feature to one and not the other does not fail where you made the mistake — it
surfaces as `IndexError: index 13 is out of bounds` inside standardisation,
several files away, part-way through a training run.
"""

from app.models.schemas import DriverFeatures
from app.services.model import BASE_FEATURES, GRID_FEATURES, feature_names, feature_vector


def test_feature_names_and_values_agree():
    vector = feature_vector(DriverFeatures(driver="X"), with_grid=False)
    assert len(vector) == len(BASE_FEATURES), (
        "feature_vector produces {} values for {} names — the two lists have "
        "drifted".format(len(vector), len(BASE_FEATURES))
    )


def test_the_grid_block_agrees_too():
    vector = feature_vector(DriverFeatures(driver="X"), with_grid=True)
    assert len(vector) == len(BASE_FEATURES) + len(GRID_FEATURES)
    assert len(vector) == len(feature_names(with_grid=True))


def test_every_named_feature_is_a_field_on_driverfeatures():
    """A name with no matching attribute would silently read as whatever the
    positional list happened to put there."""
    fields = set(DriverFeatures.model_fields)
    # is_cold_start is a bool rendered as 0/1; grid names are synthesised.
    synthesised = {"grid_legacy", "grid_modern"}
    for name in BASE_FEATURES:
        assert name in fields, "{} is named but not a DriverFeatures field".format(name)
    for name in GRID_FEATURES:
        assert name in fields or name in synthesised
