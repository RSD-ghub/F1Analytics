"""The data health page degrades panel by panel, never as a whole."""

from app.config import Settings
from app.models.schemas import SystemStatus
from app.routers import health_page
from app.services.downstream import DownstreamUnavailable


class _Ingestion:
    def __init__(self, routes, down=()):
        self.routes, self.down = routes, set(down)

    async def get(self, path, params=None):
        if path in self.down:
            raise DownstreamUnavailable("ingestion", "down")
        value = self.routes.get(path)
        return value(params) if callable(value) else value


def _wire(monkeypatch, ingestion):
    async def fake_status(settings):
        return SystemStatus(services=[], all_reachable=True)
    monkeypatch.setattr(health_page, "_clients", lambda s: {"ingestion": ingestion})
    monkeypatch.setattr(health_page, "service_status", fake_status)


async def test_it_reports_gaps_datasets_and_latest_result(monkeypatch):
    _wire(monkeypatch, _Ingestion({
        "/ingest/status": lambda p: {"summary": {"depth": p["depth"], "expected": 10, "complete": 9}},
        "/ingest/gaps": [{"season": 2026, "round": 19, "state": "failed", "reason": "timeout", "attempts": 2}],
        "/data/datasets": {"results": 9000},
        "/data/results": [{"season": 2026, "round": 20, "race_name": "Mexico City GP"}],
        "/forward/next": {"season": 2026, "round": 21, "race_name": "São Paulo GP"},
    }))
    page = await health_page.data_health(Settings())
    assert page["completeness"]["training"]["depth"] == "results"
    assert page["completeness"]["lap_data"]["depth"] == "full"
    assert page["gaps"][0]["session"] == "2026-19" and page["gaps_total"] == 1
    assert page["latest_result"]["round"] == 20
    assert page["next_race"]["round"] == 21
    assert page["unavailable"] == []


async def test_a_down_dependency_costs_only_its_panel(monkeypatch):
    _wire(monkeypatch, _Ingestion({"/data/datasets": {"results": 1}}, down={"/ingest/gaps", "/ingest/status"}))
    page = await health_page.data_health(Settings())
    assert page["datasets"] == {"results": 1}
    assert page["gaps"] == [] and page["completeness"]["training"] is None
    assert {"gaps", "training", "laps"} <= set(page["unavailable"])
