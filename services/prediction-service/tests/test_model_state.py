"""Where the model artifact lives, and why that is not the image.

A promotion writes three things: the new weights, the archived predecessor, and
the decision history. In the deployed arrangement all three were going into the
container's own filesystem, because the artifact ships inside the image and
scripts/retrain.py has always written next to the weights it read.

Nothing would have failed. `--apply` would report a promotion, the service would
serve the new model, and the next `up --build` would restore the image and put
the old champion back — along with an empty archive, so the forecasts published
in between could no longer be reproduced from the weights that made them. That
is the one property the archive exists for.
"""

import json
import os

from app.services.model import (
    MODEL_STATE_DIR,
    PACKAGED_STATE_DIR,
    RaceModel,
    ensure_state_dir,
    state_paths,
)


def _packaged_has_weights():
    return os.path.exists(state_paths(PACKAGED_STATE_DIR)[0])


# ── The three paths are one unit ─────────────────────────────────────────────


def test_every_path_a_promotion_writes_sits_under_one_directory():
    """Splitting them across an image and a volume would archive the outgoing
    champion into a layer that disappears on the next rebuild."""
    weights, archive, history = state_paths("/data/model")

    assert weights == "/data/model/model_weights.json"
    assert archive == "/data/model/model_archive"
    assert history == "/data/model/promotion_history.json"


def test_the_default_is_the_packaged_location():
    """A checkout must behave exactly as it always has — retrain.py writing
    next to the weights it read. Only a deployment moves this."""
    assert os.path.abspath(MODEL_STATE_DIR) == os.path.abspath(PACKAGED_STATE_DIR)
    assert ensure_state_dir() == [], "seeding a checkout would be a no-op"


# ── Seeding a fresh volume ───────────────────────────────────────────────────


def test_an_empty_volume_is_seeded_so_the_service_can_start(tmp_path):
    """A named volume is empty the first time compose creates it, and the
    service refuses to serve a forecast without weights."""
    state = tmp_path / "model"

    seeded = ensure_state_dir(str(state), PACKAGED_STATE_DIR)

    if not _packaged_has_weights():
        return  # nothing to seed from; covered by the artifact tests
    assert "model_weights.json" in seeded
    assert RaceModel.load(path=str(state / "model_weights.json")).version


def test_the_archive_travels_with_the_weights(tmp_path):
    """A promoted model whose predecessor stayed behind in an image layer is
    not reproducible, which defeats the point of having archived it."""
    state = tmp_path / "model"
    seeded = ensure_state_dir(str(state), PACKAGED_STATE_DIR)

    if os.path.isdir(state_paths(PACKAGED_STATE_DIR)[1]):
        assert "model_archive/" in seeded
        assert (state / "model_archive").is_dir()


def test_seeding_never_overwrites_what_the_volume_already_holds(tmp_path):
    """The whole point. After first boot the volume is authoritative — if
    seeding kept running, every restart would silently demote the promoted
    model back to whatever shipped in the image."""
    state = tmp_path / "model"
    ensure_state_dir(str(state), PACKAGED_STATE_DIR)
    if not _packaged_has_weights():
        return

    promoted = json.loads((state / "model_weights.json").read_text())
    promoted["version"] = "race-plackett-luce-v4+r9"
    (state / "model_weights.json").write_text(json.dumps(promoted))

    assert ensure_state_dir(str(state), PACKAGED_STATE_DIR) == []
    assert RaceModel.load(path=str(state / "model_weights.json")).version \
        == "race-plackett-luce-v4+r9"


def test_a_partially_filled_volume_is_completed_not_replaced(tmp_path):
    """Weights present but no history — the state after a promotion on a volume
    created before the history file existed. Fill the gap, touch nothing else.
    """
    state = tmp_path / "model"
    state.mkdir()
    (state / "model_weights.json").write_text('{"version": "kept"}')

    seeded = ensure_state_dir(str(state), PACKAGED_STATE_DIR)

    assert "model_weights.json" not in seeded
    assert json.loads((state / "model_weights.json").read_text())["version"] == "kept"


def test_seeding_from_a_packaged_dir_with_nothing_in_it_is_harmless(tmp_path):
    """An image built without an artifact should not crash the service at
    startup; it should fail later, loudly, when the weights are actually
    wanted — which is what ModelWeightsMissing is for."""
    empty = tmp_path / "packaged"
    empty.mkdir()

    assert ensure_state_dir(str(tmp_path / "state"), str(empty)) == []


# ── The script and the service agree ─────────────────────────────────────────


def test_retrain_reads_its_paths_from_the_service(tmp_path):
    """retrain.py used to rebuild the archive and history paths itself from the
    directory of the weights. Two definitions of one location drift."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import retrain

    from app.services import model

    assert retrain.ARTIFACT == os.path.abspath(model.WEIGHTS_PATH)
    assert retrain.ARCHIVE_DIR == model.ARCHIVE_DIR
    assert retrain.HISTORY == model.HISTORY_PATH
