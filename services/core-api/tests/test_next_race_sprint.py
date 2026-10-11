"""The next-race view carries a sprint weekend's sprint forecast on its own.

Kept out of ``predictions`` on purpose: that list is the Grand Prix's, and the
page, the blog and Bernie all read it as such.
"""

from app.config import Settings
from app.routers import dashboard


class _Client:
    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        handler = self._routes.get(path)
        return handler(params) if callable(handler) else handler


def _wire(monkeypatch, weekend):
    prediction = _Client({
        "/predictions/2026/6": lambda params: (
            [{"window": "sprint"}] if (params or {}).get("event") == "sprint"
            else [{"window": "pre_quali"}]
        ),
    })
    ingestion = _Client({"/forward/next": weekend})
    monkeypatch.setattr(dashboard, "_clients", lambda settings: {
        "ingestion": ingestion, "prediction": prediction, "scoring": _Client({}),
    })
    return prediction


async def test_a_sprint_weekend_carries_its_sprint_forecast_separately(monkeypatch):
    _wire(monkeypatch, {"season": 2026, "round": 6, "is_sprint_weekend": True})

    view = await dashboard.next_race(Settings())

    assert [p["window"] for p in view.predictions] == ["pre_quali"]
    assert [p["window"] for p in view.sprint_predictions] == ["sprint"]


async def test_a_grand_prix_weekend_does_not_ask_for_one(monkeypatch):
    prediction = _wire(monkeypatch, {"season": 2026, "round": 6})

    view = await dashboard.next_race(Settings())

    assert view.sprint_predictions == []
    assert all((params or {}).get("event") != "sprint" for _, params in prediction.calls)
