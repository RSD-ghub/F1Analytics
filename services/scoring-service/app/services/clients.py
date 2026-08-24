"""HTTP boundaries to ingestion-service and prediction-service.

Local models again, rather than shared imports: this is the contract
scoring-service depends on, and it should fail loudly at the boundary if either
upstream changes shape. Silently mis-parsing a prediction would corrupt the
accuracy record, which is the one thing in this system that must not be
quietly wrong.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class UpstreamUnavailable(RuntimeError):
    """An upstream service could not be reached or returned an error."""


class RaceResultRow(BaseModel):
    season: int
    round: int
    race_name: str = ""
    driver: str
    position: int = 999
    classified_position: str = ""
    status: str = ""

    @property
    def classified(self) -> bool:
        """See ingestion: a retirement still carries a numeric ``position``."""
        if self.classified_position:
            return self.classified_position.isdigit()
        return 1 <= self.position <= 30


class DriverProbabilityRow(BaseModel):
    driver: str
    team: str = "Unknown"
    #: ``None`` when the forecast made no win claim. Distinct from 0.0, which
    #: would be a confident assertion that this driver cannot win.
    p_win: Optional[float] = None
    p_podium: float = 0.0
    p_points: float = 0.0


class LockedPrediction(BaseModel):
    prediction_id: str
    season: int
    round: int
    race_name: str = ""
    window: str
    locked_at: Optional[datetime] = None
    model_version: str = ""
    driver_probabilities: List[DriverProbabilityRow] = Field(default_factory=list)
    #: Markets this forecast actually claimed. Empty means "not declared", which
    #: is treated as all three for backwards compatibility with any prediction
    #: locked before the field existed.
    published_markets: List[str] = Field(default_factory=list)
    data_complete: bool = True

    def publishes(self, market: str) -> bool:
        return not self.published_markets or market in self.published_markets

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "LockedPrediction":
        """Flatten the nested data_quality block the prediction API returns."""
        quality = payload.get("data_quality") or {}
        return cls(
            prediction_id=payload.get("prediction_id", ""),
            season=payload["season"],
            round=payload["round"],
            race_name=payload.get("race_name", ""),
            window=payload.get("window", ""),
            locked_at=payload.get("locked_at"),
            model_version=payload.get("model_version", ""),
            published_markets=list(payload.get("published_markets", []) or []),
            driver_probabilities=[
                DriverProbabilityRow(**row)
                for row in payload.get("driver_probabilities", [])
            ],
            data_complete=bool(quality.get("complete", True)),
        )


class _JsonClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = "{}{}".format(self._base_url, path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("GET {} failed: {}".format(url, exc)) from exc


class IngestionClient(_JsonClient):
    async def race_results(
        self, season: int, round_number: int
    ) -> List[RaceResultRow]:
        payload = await self._get(
            "/data/results",
            {"season": season, "round": round_number, "limit": 100},
        )
        return [RaceResultRow(**row) for row in payload]

    async def season_results(self, season: int) -> List[RaceResultRow]:
        payload = await self._get("/data/results", {"season": season, "limit": 20000})
        return [RaceResultRow(**row) for row in payload]


class PredictionClient(_JsonClient):
    async def predictions_for(
        self, season: int, round_number: int
    ) -> List[LockedPrediction]:
        payload = await self._get("/predictions/{}/{}".format(season, round_number))
        return [LockedPrediction.from_payload(row) for row in payload]

    async def list_predictions(
        self, season: Optional[int] = None, limit: int = 500
    ) -> List[LockedPrediction]:
        params: Dict[str, Any] = {"limit": limit}
        if season is not None:
            params["season"] = season
        payload = await self._get("/predictions", params)
        return [LockedPrediction.from_payload(row) for row in payload]
