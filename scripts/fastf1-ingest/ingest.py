import json
import os
import sys
import uuid
from datetime import datetime

import fastf1
import numpy as np
import pandas as pd


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def safe_float(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        if isinstance(value, float) and np.isnan(value):
            return default
        return int(value)
    except Exception:
        return default


def safe_text(value, default=""):
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def to_seconds(value):
    if value is None:
        return 0.0
    if isinstance(value, pd.Timedelta):
        return safe_float(value.total_seconds())
    return safe_float(value)


def write_json(path, rows):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)


def read_json_list(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        print(f"[WARN] failed to read existing file {path}: {exc}")
        return []


def season_round_key(row):
    season = safe_int(row.get("season") if isinstance(row, dict) else None, -1)
    round_number = safe_int(row.get("round") if isinstance(row, dict) else None, -1)
    return season, round_number


def merge_rows_by_round(path, fresh_rows, from_season, to_season):
    existing_rows = read_json_list(path)
    fresh_keys = {season_round_key(row) for row in fresh_rows}
    merged = []

    for row in existing_rows:
        season, round_number = season_round_key(row)
        in_target_range = from_season <= season <= to_season
        replaced_round = (season, round_number) in fresh_keys
        if in_target_range and replaced_round:
            continue
        merged.append(row)

    merged.extend(fresh_rows)
    return merged


def stable_id(season, round_number, driver, team):
    key = f"{season}|{round_number}|{driver}|{team}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def main():
    output_dir = os.getenv("OUTPUT_DIR", "./data")
    from_season = env_int("FROM_SEASON", 2010)
    to_season = env_int("TO_SEASON", datetime.utcnow().year)
    if from_season > to_season:
        from_season, to_season = to_season, from_season

    os.makedirs(output_dir, exist_ok=True)
    cache_dir = os.path.join(output_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)

    results_rows = []
    lap_rows = []
    stint_rows = []
    pit_stop_rows = []
    weather_rows = []
    race_control_rows = []
    telemetry_rows = []
    sessions_loaded = 0

    for season in range(from_season, to_season + 1):
        try:
            schedule = fastf1.get_event_schedule(season, include_testing=False)
        except Exception as exc:
            print(f"[WARN] season {season}: failed schedule fetch: {exc}")
            continue

        for _, event in schedule.iterrows():
            round_number = safe_int(event.get("RoundNumber"), 0)
            event_name = safe_text(event.get("EventName"), f"Round {round_number}")
            if round_number <= 0:
                continue

            try:
                session = fastf1.get_session(season, round_number, "R")
                session.load(laps=True, telemetry=True, weather=True, messages=True)
                sessions_loaded += 1
            except Exception as exc:
                print(f"[WARN] {season} R{round_number} {event_name}: session load failed: {exc}")
                continue

            race_date = ""
            try:
                race_date = session.date.strftime("%Y-%m-%d") if session.date is not None else ""
            except Exception:
                race_date = ""

            circuit = safe_text(event.get("Location"), "")
            driver_name_map = {}

            # Results rows
            try:
                results_df = session.results if session.results is not None else pd.DataFrame()
                for _, res in results_df.iterrows():
                    driver_name = safe_text(res.get("FullName"), safe_text(res.get("Abbreviation"), "Unknown"))
                    driver_code = safe_text(res.get("Abbreviation"), "")
                    if driver_code:
                        driver_name_map[driver_code] = driver_name

                    team = safe_text(res.get("TeamName"), "Unknown")
                    position = safe_int(res.get("Position"), safe_int(res.get("ClassifiedPosition"), 999))
                    points = safe_float(res.get("Points"), 0.0)
                    row = {
                        "id": stable_id(season, round_number, driver_name, team),
                        "season": season,
                        "round": round_number,
                        "raceName": event_name,
                        "circuit": circuit,
                        "raceDate": race_date,
                        "driver": driver_name,
                        "team": team,
                        "position": position,
                        "points": points,
                        "timestamp": int(datetime.utcnow().timestamp() * 1000),
                    }
                    results_rows.append(row)
            except Exception as exc:
                print(f"[WARN] {season} R{round_number}: results parse failed: {exc}")

            # Laps and pit stops
            try:
                laps_df = session.laps if session.laps is not None else pd.DataFrame()
                if not laps_df.empty:
                    for _, lap in laps_df.iterrows():
                        driver_code = safe_text(lap.get("Driver"), "")
                        driver = driver_name_map.get(driver_code, driver_code)
                        if not driver:
                            continue

                        lap_number = safe_int(lap.get("LapNumber"), 0)
                        lap_time_seconds = to_seconds(lap.get("LapTime"))
                        compound = safe_text(lap.get("Compound"), "Unknown")
                        stint = safe_int(lap.get("Stint"), 0)
                        lap_rows.append(
                            {
                                "season": season,
                                "round": round_number,
                                "raceName": event_name,
                                "driver": driver,
                                "lap": lap_number,
                                "lapTimeSeconds": lap_time_seconds,
                                "compound": compound,
                                "stint": stint,
                            }
                        )

                    pit_counts = {}
                    for _, lap in laps_df.iterrows():
                        driver_code = safe_text(lap.get("Driver"), "")
                        driver = driver_name_map.get(driver_code, driver_code)
                        if not driver:
                            continue

                        pit_in = lap.get("PitInTime")
                        pit_out = lap.get("PitOutTime")
                        if pd.isna(pit_in) or pd.isna(pit_out):
                            continue

                        duration = to_seconds(pit_out - pit_in)
                        if duration <= 0:
                            continue

                        current = pit_counts.get(driver, 0) + 1
                        pit_counts[driver] = current
                        pit_stop_rows.append(
                            {
                                "season": season,
                                "round": round_number,
                                "raceName": event_name,
                                "driver": driver,
                                "stop": current,
                                "lap": safe_int(lap.get("LapNumber"), 0),
                                "durationSeconds": duration,
                            }
                        )

                    stint_group = (
                        laps_df.assign(
                            Driver=laps_df["Driver"].fillna(""),
                            Stint=laps_df["Stint"].fillna(0),
                            Compound=laps_df["Compound"].fillna("Unknown"),
                        )
                        .groupby(["Driver", "Stint", "Compound"], as_index=False)
                        .size()
                    )
                    for _, stint_row in stint_group.iterrows():
                        driver_code = safe_text(stint_row.get("Driver"), "")
                        driver = driver_name_map.get(driver_code, driver_code)
                        if not driver:
                            continue

                        stint_rows.append(
                            {
                                "season": season,
                                "round": round_number,
                                "raceName": event_name,
                                "driver": driver,
                                "stint": safe_int(stint_row.get("Stint"), 0),
                                "compound": safe_text(stint_row.get("Compound"), "Unknown"),
                                "laps": safe_int(stint_row.get("size"), 0),
                            }
                        )
            except Exception as exc:
                print(f"[WARN] {season} R{round_number}: laps parse failed: {exc}")

            # Weather rows
            try:
                weather_df = session.weather_data if session.weather_data is not None else pd.DataFrame()
                if not weather_df.empty:
                    for _, weather in weather_df.iterrows():
                        weather_rows.append(
                            {
                                "season": season,
                                "round": round_number,
                                "raceName": event_name,
                                "sampleTime": safe_text(weather.get("Time"), ""),
                                "airTemp": safe_float(weather.get("AirTemp"), 0.0),
                                "trackTemp": safe_float(weather.get("TrackTemp"), 0.0),
                                "humidity": safe_float(weather.get("Humidity"), 0.0),
                                "rainfall": bool(weather.get("Rainfall", False)),
                            }
                        )
            except Exception as exc:
                print(f"[WARN] {season} R{round_number}: weather parse failed: {exc}")

            # Race control messages
            try:
                rc_df = session.race_control_messages if session.race_control_messages is not None else pd.DataFrame()
                if not rc_df.empty:
                    for _, rc in rc_df.iterrows():
                        race_control_rows.append(
                            {
                                "season": season,
                                "round": round_number,
                                "raceName": event_name,
                                "category": safe_text(rc.get("Category"), "Info"),
                                "message": safe_text(rc.get("Message"), ""),
                                "time": safe_text(rc.get("Time"), ""),
                                "lap": safe_int(rc.get("Lap"), 0),
                            }
                        )
            except Exception as exc:
                print(f"[WARN] {season} R{round_number}: race control parse failed: {exc}")

            # Telemetry summary rows per driver
            try:
                for drv in session.drivers:
                    try:
                        drv_laps = session.laps.pick_driver(drv)
                        fastest = drv_laps.pick_fastest()
                        if fastest is None or fastest.empty:
                            continue

                        car_data = fastest.get_car_data()
                        if car_data is None or car_data.empty:
                            continue

                        speed_series = car_data.get("Speed") if "Speed" in car_data else None
                        throttle_series = car_data.get("Throttle") if "Throttle" in car_data else None
                        brake_series = car_data.get("Brake") if "Brake" in car_data else None
                        driver_code = safe_text(fastest.get("Driver"), safe_text(drv, "Unknown"))
                        driver_name = driver_name_map.get(driver_code, driver_code)
                        telemetry_rows.append(
                            {
                                "season": season,
                                "round": round_number,
                                "raceName": event_name,
                                "driver": driver_name,
                                "sampleTime": safe_text(fastest.get("LapTime"), ""),
                                "maxSpeed": safe_float(speed_series.max() if speed_series is not None else 0.0, 0.0),
                                "avgSpeed": safe_float(speed_series.mean() if speed_series is not None else 0.0, 0.0),
                                "throttleMean": safe_float(
                                    throttle_series.mean() if throttle_series is not None else 0.0, 0.0
                                ),
                                "brakeMean": safe_float(brake_series.mean() if brake_series is not None else 0.0, 0.0),
                            }
                        )
                    except Exception:
                        continue
            except Exception as exc:
                print(f"[WARN] {season} R{round_number}: telemetry parse failed: {exc}")

    if sessions_loaded <= 0:
        print("[ERROR] FastF1 ingest finished with zero loaded race sessions; keeping existing dataset unchanged.")
        sys.exit(2)

    results_path = os.path.join(output_dir, "results.json")
    laps_path = os.path.join(output_dir, "laps.json")
    stints_path = os.path.join(output_dir, "stints.json")
    pit_stops_path = os.path.join(output_dir, "pit_stops.json")
    weather_path = os.path.join(output_dir, "weather.json")
    race_control_path = os.path.join(output_dir, "race_control.json")
    telemetry_path = os.path.join(output_dir, "telemetry.json")

    merged_results = merge_rows_by_round(results_path, results_rows, from_season, to_season)
    merged_laps = merge_rows_by_round(laps_path, lap_rows, from_season, to_season)
    merged_stints = merge_rows_by_round(stints_path, stint_rows, from_season, to_season)
    merged_pit_stops = merge_rows_by_round(pit_stops_path, pit_stop_rows, from_season, to_season)
    merged_weather = merge_rows_by_round(weather_path, weather_rows, from_season, to_season)
    merged_race_control = merge_rows_by_round(race_control_path, race_control_rows, from_season, to_season)
    merged_telemetry = merge_rows_by_round(telemetry_path, telemetry_rows, from_season, to_season)

    merged_results.sort(
        key=lambda x: (
            safe_int(x.get("season"), 0),
            safe_int(x.get("round"), 0),
            safe_int(x.get("position"), 999),
            safe_text(x.get("driver"), ""),
        )
    )
    merged_laps.sort(
        key=lambda x: (
            safe_int(x.get("season"), 0),
            safe_int(x.get("round"), 0),
            safe_text(x.get("driver"), ""),
            safe_int(x.get("lap"), 0),
        )
    )
    merged_stints.sort(
        key=lambda x: (
            safe_int(x.get("season"), 0),
            safe_int(x.get("round"), 0),
            safe_text(x.get("driver"), ""),
            safe_int(x.get("stint"), 0),
            safe_text(x.get("compound"), ""),
        )
    )
    merged_pit_stops.sort(
        key=lambda x: (
            safe_int(x.get("season"), 0),
            safe_int(x.get("round"), 0),
            safe_text(x.get("driver"), ""),
            safe_int(x.get("stop"), 0),
        )
    )
    merged_weather.sort(
        key=lambda x: (
            safe_int(x.get("season"), 0),
            safe_int(x.get("round"), 0),
            safe_text(x.get("sampleTime"), ""),
        )
    )
    merged_race_control.sort(
        key=lambda x: (
            safe_int(x.get("season"), 0),
            safe_int(x.get("round"), 0),
            safe_text(x.get("time"), ""),
            safe_int(x.get("lap"), 0),
        )
    )
    merged_telemetry.sort(
        key=lambda x: (
            safe_int(x.get("season"), 0),
            safe_int(x.get("round"), 0),
            safe_text(x.get("driver"), ""),
        )
    )

    write_json(results_path, merged_results)
    write_json(laps_path, merged_laps)
    write_json(stints_path, merged_stints)
    write_json(pit_stops_path, merged_pit_stops)
    write_json(weather_path, merged_weather)
    write_json(race_control_path, merged_race_control)
    write_json(telemetry_path, merged_telemetry)

    print("[INFO] FastF1 ingest completed")
    print(
        f"[INFO] results={len(merged_results)} laps={len(merged_laps)} stints={len(merged_stints)} "
        f"pit_stops={len(merged_pit_stops)} weather={len(merged_weather)} "
        f"race_control={len(merged_race_control)} telemetry={len(merged_telemetry)}"
    )


if __name__ == "__main__":
    main()
