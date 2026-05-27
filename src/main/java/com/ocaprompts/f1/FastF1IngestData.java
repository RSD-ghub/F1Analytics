package com.ocaprompts.f1;

import java.util.ArrayList;
import java.util.List;

public class FastF1IngestData {
    private List<Prompt> results = new ArrayList<>();
    private List<FastF1DomainData.LapRow> laps = new ArrayList<>();
    private List<FastF1DomainData.StintRow> stints = new ArrayList<>();
    private List<FastF1DomainData.PitStopRow> pitStops = new ArrayList<>();
    private List<FastF1DomainData.WeatherRow> weather = new ArrayList<>();
    private List<FastF1DomainData.RaceControlRow> raceControl = new ArrayList<>();
    private List<FastF1DomainData.TelemetryRow> telemetry = new ArrayList<>();

    public List<Prompt> getResults() {
        return results;
    }

    public void setResults(List<Prompt> results) {
        this.results = results;
    }

    public List<FastF1DomainData.LapRow> getLaps() {
        return laps;
    }

    public void setLaps(List<FastF1DomainData.LapRow> laps) {
        this.laps = laps;
    }

    public List<FastF1DomainData.StintRow> getStints() {
        return stints;
    }

    public void setStints(List<FastF1DomainData.StintRow> stints) {
        this.stints = stints;
    }

    public List<FastF1DomainData.PitStopRow> getPitStops() {
        return pitStops;
    }

    public void setPitStops(List<FastF1DomainData.PitStopRow> pitStops) {
        this.pitStops = pitStops;
    }

    public List<FastF1DomainData.WeatherRow> getWeather() {
        return weather;
    }

    public void setWeather(List<FastF1DomainData.WeatherRow> weather) {
        this.weather = weather;
    }

    public List<FastF1DomainData.RaceControlRow> getRaceControl() {
        return raceControl;
    }

    public void setRaceControl(List<FastF1DomainData.RaceControlRow> raceControl) {
        this.raceControl = raceControl;
    }

    public List<FastF1DomainData.TelemetryRow> getTelemetry() {
        return telemetry;
    }

    public void setTelemetry(List<FastF1DomainData.TelemetryRow> telemetry) {
        this.telemetry = telemetry;
    }
}
