package com.ocaprompts.f1;

import com.mongodb.client.MongoCollection;
import com.mongodb.client.model.Filters;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.util.ArrayList;
import java.util.List;
import java.util.function.ToIntFunction;

@ApplicationScoped
public class MongoFastF1DatasetRepository {

    private final MongoCollection<FastF1DomainData.LapRow> laps;
    private final MongoCollection<FastF1DomainData.StintRow> stints;
    private final MongoCollection<FastF1DomainData.PitStopRow> pitStops;
    private final MongoCollection<FastF1DomainData.WeatherRow> weather;
    private final MongoCollection<FastF1DomainData.RaceControlRow> raceControl;
    private final MongoCollection<FastF1DomainData.TelemetryRow> telemetry;

    @Inject
    public MongoFastF1DatasetRepository(MongoProvider mongoProvider) {
        this.laps = mongoProvider.laps();
        this.stints = mongoProvider.stints();
        this.pitStops = mongoProvider.pitStops();
        this.weather = mongoProvider.weather();
        this.raceControl = mongoProvider.raceControl();
        this.telemetry = mongoProvider.telemetry();
    }

    public List<FastF1DomainData.LapRow> listLaps() {
        return laps.find().into(new ArrayList<>());
    }

    public List<FastF1DomainData.StintRow> listStints() {
        return stints.find().into(new ArrayList<>());
    }

    public List<FastF1DomainData.PitStopRow> listPitStops() {
        return pitStops.find().into(new ArrayList<>());
    }

    public List<FastF1DomainData.WeatherRow> listWeather() {
        return weather.find().into(new ArrayList<>());
    }

    public List<FastF1DomainData.RaceControlRow> listRaceControl() {
        return raceControl.find().into(new ArrayList<>());
    }

    public List<FastF1DomainData.TelemetryRow> listTelemetry() {
        return telemetry.find().into(new ArrayList<>());
    }

    public void replaceSeasonRange(int fromSeason, int toSeason, FastF1IngestData data) {
        replaceSeasonRange(laps, data.getLaps(), fromSeason, toSeason, FastF1DomainData.LapRow::getSeason);
        replaceSeasonRange(stints, data.getStints(), fromSeason, toSeason, FastF1DomainData.StintRow::getSeason);
        replaceSeasonRange(pitStops, data.getPitStops(), fromSeason, toSeason, FastF1DomainData.PitStopRow::getSeason);
        replaceSeasonRange(weather, data.getWeather(), fromSeason, toSeason, FastF1DomainData.WeatherRow::getSeason);
        replaceSeasonRange(raceControl, data.getRaceControl(), fromSeason, toSeason, FastF1DomainData.RaceControlRow::getSeason);
        replaceSeasonRange(telemetry, data.getTelemetry(), fromSeason, toSeason, FastF1DomainData.TelemetryRow::getSeason);
    }

    private <T> void replaceSeasonRange(MongoCollection<T> collection,
                                        List<T> incoming,
                                        int fromSeason,
                                        int toSeason,
                                        ToIntFunction<T> seasonExtractor) {
        collection.deleteMany(Filters.and(
                Filters.gte("season", fromSeason),
                Filters.lte("season", toSeason)
        ));
        List<T> filteredIncoming = incoming.stream()
                .filter(row -> {
                    int season = seasonExtractor.applyAsInt(row);
                    return season >= fromSeason && season <= toSeason;
                })
                .toList();
        if (!filteredIncoming.isEmpty()) {
            collection.insertMany(filteredIncoming);
        }
    }
}
