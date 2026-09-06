"""Pure extraction of typed rows from FastF1 DataFrames.

These functions take DataFrames rather than a live FastF1 ``Session`` so they can
be exercised against synthetic frames without network access — the session
plumbing lives in ``fastf1_source`` instead.

Coercion helpers and the pit-stop pairing rule are ported from the original
``scripts/fastf1-ingest/ingest.py``; the pit-stop logic in particular encodes a
non-obvious FastF1 detail worth preserving verbatim (see ``extract_pit_stops``).
"""

import uuid
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.models.schemas import (
    ExpectedSession,
    LapRow,
    PitStopRow,
    QualifyingRow,
    RaceControlRow,
    ResultRow,
    StintRow,
    TelemetryRow,
    WeatherRow,
)

_ID_NAMESPACE = uuid.NAMESPACE_DNS


# ── Coercion helpers ─────────────────────────────────────────────────────────


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, float) and np.isnan(value):
            return default
        return int(value)
    except Exception:
        return default


def safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def to_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, pd.Timedelta):
        return safe_float(value.total_seconds())
    return safe_float(value)


def stable_id(*parts: Any) -> str:
    """Deterministic id from the row's natural key.

    Re-ingesting a session must upsert rather than duplicate, which is what lets
    a gap be healed by simply running the session again.
    """
    key = "|".join(safe_text(part, "-") for part in parts)
    return str(uuid.uuid5(_ID_NAMESPACE, key))


# ── Extraction ───────────────────────────────────────────────────────────────


def build_driver_name_map(results_df: Optional[pd.DataFrame]) -> Dict[str, str]:
    """Map driver abbreviation → full name.

    Laps and telemetry identify drivers by code; results carry the full name. The
    dataset should speak one language, so everything downstream is keyed on the
    full name.
    """
    mapping: Dict[str, str] = {}
    if results_df is None or results_df.empty:
        return mapping
    for _, res in results_df.iterrows():
        code = safe_text(res.get("Abbreviation"))
        if not code:
            continue
        mapping[code] = safe_text(
            res.get("FullName"), safe_text(res.get("Abbreviation"), "Unknown")
        )
    return mapping


def extract_results(
    session: ExpectedSession, results_df: Optional[pd.DataFrame]
) -> List[ResultRow]:
    rows: List[ResultRow] = []
    if results_df is None or results_df.empty:
        return rows

    for _, res in results_df.iterrows():
        driver = safe_text(
            res.get("FullName"), safe_text(res.get("Abbreviation"), "Unknown")
        )
        team = safe_text(res.get("TeamName"), "Unknown")
        rows.append(
            ResultRow(
                id=stable_id(session.season, session.round, driver, team),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                circuit=session.circuit,
                race_date=session.race_date,
                driver=driver,
                team=team,
                position=safe_int(
                    res.get("Position"), safe_int(res.get("ClassifiedPosition"), 999)
                ),
                points=safe_float(res.get("Points"), 0.0),
                grid_position=safe_int(res.get("GridPosition"), 0),
                classified_position=safe_text(res.get("ClassifiedPosition")),
                status=safe_text(res.get("Status")),
            )
        )
    return rows


def extract_qualifying(
    session: ExpectedSession, results_df: Optional[pd.DataFrame]
) -> List[QualifyingRow]:
    """Qualifying classification and per-segment times.

    Shares the results-frame shape with the race, but the Q1/Q2/Q3 columns only
    appear on a qualifying session — hence a separate extractor rather than a
    flag on ``extract_results``.
    """
    rows: List[QualifyingRow] = []
    if results_df is None or results_df.empty:
        return rows

    for _, res in results_df.iterrows():
        driver = safe_text(
            res.get("FullName"), safe_text(res.get("Abbreviation"), "Unknown")
        )
        team = safe_text(res.get("TeamName"), "Unknown")
        rows.append(
            QualifyingRow(
                id=stable_id("quali", session.season, session.round, driver),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                driver=driver,
                team=team,
                position=safe_int(res.get("Position"), 999),
                # Measured, not assumed: FastF1 carries a GridPosition column on
                # qualifying sessions but never populates it — zero of nineteen
                # rows for a long-finished 2024 race, zero of twenty-two for a
                # current one. The penalty-adjusted grid therefore never arrives
                # from here; it is applied afterwards from the FIA's own
                # starting-grid document. See services/fia_documents.py.
                grid_position=0,
                driver_number=safe_int(res.get("DriverNumber"), 0),
                q1_seconds=to_seconds(res.get("Q1")),
                q2_seconds=to_seconds(res.get("Q2")),
                q3_seconds=to_seconds(res.get("Q3")),
            )
        )
    return rows


def extract_laps(
    session: ExpectedSession,
    laps_df: Optional[pd.DataFrame],
    driver_names: Dict[str, str],
) -> List[LapRow]:
    rows: List[LapRow] = []
    if laps_df is None or laps_df.empty:
        return rows

    for _, lap in laps_df.iterrows():
        code = safe_text(lap.get("Driver"))
        driver = driver_names.get(code, code)
        if not driver:
            continue
        lap_number = safe_int(lap.get("LapNumber"), 0)
        rows.append(
            LapRow(
                id=stable_id(session.season, session.round, driver, lap_number),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                driver=driver,
                lap=lap_number,
                lap_time_seconds=to_seconds(lap.get("LapTime")),
                compound=safe_text(lap.get("Compound"), "Unknown"),
                stint=safe_int(lap.get("Stint"), 0),
            )
        )
    return rows


def extract_pit_stops(
    session: ExpectedSession,
    laps_df: Optional[pd.DataFrame],
    driver_names: Dict[str, str],
) -> List[PitStopRow]:
    """Pair pit entry with exit across adjacent laps.

    FastF1 records ``PitInTime`` on the lap the car entered the pits but
    ``PitOutTime`` on the *following* lap. Reading both columns off one row —
    the obvious implementation — silently yields nothing, so the join has to walk
    to the next lap for the same driver.
    """
    rows: List[PitStopRow] = []
    if laps_df is None or laps_df.empty or "PitInTime" not in laps_df:
        return rows

    pit_in_laps = laps_df[laps_df["PitInTime"].notna()]
    stop_counts: Dict[str, int] = {}

    for _, pit_lap in pit_in_laps.iterrows():
        code = safe_text(pit_lap.get("Driver"))
        driver = driver_names.get(code, code)
        if not driver:
            continue

        lap_number = safe_int(pit_lap.get("LapNumber"), 0)
        pit_in = pit_lap.get("PitInTime")

        next_laps = laps_df[
            (laps_df["Driver"] == pit_lap.get("Driver"))
            & (laps_df["LapNumber"] == lap_number + 1)
        ]
        pit_out = None
        if not next_laps.empty:
            pit_out = next_laps.iloc[0].get("PitOutTime")

        # Fall back to the same row in case a FastF1 version populates it there.
        if pit_out is None or pd.isna(pit_out):
            pit_out = pit_lap.get("PitOutTime")
        if pit_out is None or pd.isna(pit_out):
            continue

        duration = to_seconds(pit_out - pit_in)
        if duration <= 0:
            continue

        stop_number = stop_counts.get(driver, 0) + 1
        stop_counts[driver] = stop_number
        rows.append(
            PitStopRow(
                id=stable_id(session.season, session.round, driver, stop_number),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                driver=driver,
                stop=stop_number,
                lap=lap_number,
                duration_seconds=duration,
            )
        )
    return rows


def extract_stints(
    session: ExpectedSession,
    laps_df: Optional[pd.DataFrame],
    driver_names: Dict[str, str],
) -> List[StintRow]:
    rows: List[StintRow] = []
    if laps_df is None or laps_df.empty:
        return rows
    for column in ("Driver", "Stint", "Compound"):
        if column not in laps_df:
            return rows

    grouped = (
        laps_df.assign(
            Driver=laps_df["Driver"].fillna(""),
            Stint=laps_df["Stint"].fillna(0),
            Compound=laps_df["Compound"].fillna("Unknown"),
        )
        .groupby(["Driver", "Stint", "Compound"], as_index=False)
        .size()
    )

    for _, stint in grouped.iterrows():
        code = safe_text(stint.get("Driver"))
        driver = driver_names.get(code, code)
        if not driver:
            continue
        stint_number = safe_int(stint.get("Stint"), 0)
        compound = safe_text(stint.get("Compound"), "Unknown")
        rows.append(
            StintRow(
                id=stable_id(
                    session.season, session.round, driver, stint_number, compound
                ),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                driver=driver,
                stint=stint_number,
                compound=compound,
                laps=safe_int(stint.get("size"), 0),
            )
        )
    return rows


def extract_weather(
    session: ExpectedSession, weather_df: Optional[pd.DataFrame]
) -> List[WeatherRow]:
    rows: List[WeatherRow] = []
    if weather_df is None or weather_df.empty:
        return rows

    for _, weather in weather_df.iterrows():
        sample_time = safe_text(weather.get("Time"))
        rows.append(
            WeatherRow(
                id=stable_id(session.season, session.round, sample_time),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                sample_time=sample_time,
                air_temp=safe_float(weather.get("AirTemp"), 0.0),
                track_temp=safe_float(weather.get("TrackTemp"), 0.0),
                humidity=safe_float(weather.get("Humidity"), 0.0),
                rainfall=bool(weather.get("Rainfall", False)),
            )
        )
    return rows


def extract_race_control(
    session: ExpectedSession, rc_df: Optional[pd.DataFrame]
) -> List[RaceControlRow]:
    rows: List[RaceControlRow] = []
    if rc_df is None or rc_df.empty:
        return rows

    for _, rc in rc_df.iterrows():
        message = safe_text(rc.get("Message"))
        time = safe_text(rc.get("Time"))
        lap = safe_int(rc.get("Lap"), 0)
        rows.append(
            RaceControlRow(
                id=stable_id(session.season, session.round, time, lap, message),
                season=session.season,
                round=session.round,
                race_name=session.race_name,
                category=safe_text(rc.get("Category"), "Info"),
                message=message,
                time=time,
                lap=lap,
            )
        )
    return rows


def build_telemetry_row(
    session: ExpectedSession,
    driver: str,
    lap_time: str,
    speed: Optional[pd.Series],
    throttle: Optional[pd.Series],
    brake: Optional[pd.Series],
) -> TelemetryRow:
    """Summarise one driver's fastest-lap car data into a single row."""
    return TelemetryRow(
        id=stable_id(session.season, session.round, driver),
        season=session.season,
        round=session.round,
        race_name=session.race_name,
        driver=driver,
        sample_time=lap_time,
        max_speed=safe_float(speed.max() if speed is not None else 0.0, 0.0),
        avg_speed=safe_float(speed.mean() if speed is not None else 0.0, 0.0),
        throttle_mean=safe_float(throttle.mean() if throttle is not None else 0.0, 0.0),
        brake_mean=safe_float(brake.mean() if brake is not None else 0.0, 0.0),
    )
