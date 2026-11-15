"""The late-race lean toward "the order holds"."""

import numpy as np

from app.services import live_replay
from app.training import late_blend
from app.training.late_blend import LateBlend, Sample

from tests.test_live_replay import build


def test_weight_grows_linearly_from_the_start_to_alpha_at_the_flag():
    assert late_blend.weight(0.5, 0.8, 0.6) == 0.0
    assert abs(late_blend.weight(0.8, 0.8, 0.6) - 0.4) < 1e-9
    assert abs(late_blend.weight(1.0, 0.8, 0.6) - 0.8) < 1e-9
    assert late_blend.weight(0.9, 0.0, 0.6) == 0.0


def test_order_chances_ignore_retired_cars():
    win, podium = late_blend.order_chances(["B", "A", "C", "D"], ["A", "B", "C", "D"],
                                           [False, False, True, False])
    assert list(win) == [0, 1, 0, 0] and list(podium) == [1, 1, 0, 0]


def _sample(race, fraction, spread=True):
    # Four cars, A leads and wins. The forecast is spread; order-holds is sure.
    win = np.array([0.4, 0.3, 0.2, 0.1]) if spread else np.array([1.0, 0, 0, 0])
    podium = np.array([0.8, 0.7, 0.6, 0.5])
    return Sample(race, fraction, win, podium, np.array([1.0, 0, 0, 0]),
                  np.array([1.0, 1, 1, 0]), np.array([1.5, 2, 3, 4]), [1, 2, 3, 4])


def test_choose_and_gate_find_a_blend_that_helps_when_the_order_holds():
    samples = [_sample("r%d" % i, f) for i in range(8) for f in (0.1, 0.5, 0.75, 0.9)]
    alpha, start, search = late_blend.choose(samples)
    assert alpha > 0 and search[(alpha, start)] < search[(0.0, 0.4)]
    mean, (low, high), enabled = late_blend.gate(samples, alpha, start)
    assert enabled and high < 0


def test_round_trip_and_off_when_not_enabled():
    blend = LateBlend(alpha=0.6, start=0.7, enabled=True, meta={"gate": {"mean": -0.1}})
    again = LateBlend.from_dict(blend.to_dict())
    assert (again.alpha, again.start, again.enabled, again.meta) == (0.6, 0.7, True, {"gate": {"mean": -0.1}})
    off = LateBlend(alpha=0.6, start=0.7, enabled=False)
    win = np.array([0.5, 0.5])
    assert off.apply(0.95, win, win, [1, 0], [1, 1])[0] is win


def test_the_replay_applies_an_enabled_blend_late_and_only_late(monkeypatch):
    plain = build()
    live_replay.cached_late_blend.cache_clear()
    monkeypatch.setattr(live_replay, "cached_late_blend",
                        lambda: LateBlend(alpha=0.8, start=0.6, enabled=True))
    blended = build()
    by_lap = {f["lap"]: f for f in blended["frames"]}
    assert by_lap[10]["blend"] == 0.0 and by_lap[27]["blend"] > 0.5
    def distance(frames, lap):
        """How far the podium chances sit from 'the order holds' at ``lap``."""
        cars = {f["lap"]: f for f in frames}[lap]["cars"]
        return sum(abs(c["podium"] - float(c["position"] <= 3)) for c in cars)

    assert distance(blended["frames"], 10) == distance(plain["frames"], 10)
    assert distance(plain["frames"], 27) > 0
    assert distance(blended["frames"], 27) < distance(plain["frames"], 27)
