package com.ocaprompts.f1;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import io.helidon.config.Config;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.function.ToIntFunction;

@ApplicationScoped
public class FastF1DatasetRepository {

    private final ObjectMapper mapper = new ObjectMapper();
    private final Path datasetRoot;
    private final Path lapsFile;
    private final Path stintsFile;
    private final Path pitStopsFile;
    private final Path weatherFile;
    private final Path raceControlFile;
    private final Path telemetryFile;
    private final Path statusFile;

    @Inject
    public FastF1DatasetRepository(Config config) {
        String root = config.get("fastf1.dataset-root").asString().orElse("fastf1-data");
        this.datasetRoot = Paths.get(root);
        this.lapsFile = datasetRoot.resolve("laps.json");
        this.stintsFile = datasetRoot.resolve("stints.json");
        this.pitStopsFile = datasetRoot.resolve("pit_stops.json");
        this.weatherFile = datasetRoot.resolve("weather.json");
        this.raceControlFile = datasetRoot.resolve("race_control.json");
        this.telemetryFile = datasetRoot.resolve("telemetry.json");
        this.statusFile = datasetRoot.resolve("ingest-status.json");
    }

    public Path getDatasetRoot() {
        return datasetRoot;
    }

    public Path getStatusFile() {
        return statusFile;
    }

    public List<FastF1DomainData.LapRow> listLaps() {
        return readList(lapsFile, new TypeReference<List<FastF1DomainData.LapRow>>() {});
    }

    public List<FastF1DomainData.StintRow> listStints() {
        return readList(stintsFile, new TypeReference<List<FastF1DomainData.StintRow>>() {});
    }

    public List<FastF1DomainData.PitStopRow> listPitStops() {
        return readList(pitStopsFile, new TypeReference<List<FastF1DomainData.PitStopRow>>() {});
    }

    public List<FastF1DomainData.WeatherRow> listWeather() {
        return readList(weatherFile, new TypeReference<List<FastF1DomainData.WeatherRow>>() {});
    }

    public List<FastF1DomainData.RaceControlRow> listRaceControl() {
        return readList(raceControlFile, new TypeReference<List<FastF1DomainData.RaceControlRow>>() {});
    }

    public List<FastF1DomainData.TelemetryRow> listTelemetry() {
        return readList(telemetryFile, new TypeReference<List<FastF1DomainData.TelemetryRow>>() {});
    }

    public Optional<FastF1IngestionStatus> readStatus() {
        if (Files.notExists(statusFile)) {
            return Optional.empty();
        }
        try {
            return Optional.of(mapper.readValue(statusFile.toFile(), FastF1IngestionStatus.class));
        } catch (IOException e) {
            throw new IllegalStateException("Failed to read FastF1 ingest status", e);
        }
    }

    public void writeStatus(FastF1IngestionStatus status) {
        try {
            Files.createDirectories(datasetRoot);
            mapper.writerWithDefaultPrettyPrinter().writeValue(statusFile.toFile(), status);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to write FastF1 ingest status", e);
        }
    }

    public void replaceSeasonRange(int fromSeason, int toSeason, FastF1IngestData data) {
        replaceSeasonRange(
                lapsFile,
                data.getLaps(),
                fromSeason,
                toSeason,
                new TypeReference<List<FastF1DomainData.LapRow>>() {},
                FastF1DomainData.LapRow::getSeason
        );
        replaceSeasonRange(
                stintsFile,
                data.getStints(),
                fromSeason,
                toSeason,
                new TypeReference<List<FastF1DomainData.StintRow>>() {},
                FastF1DomainData.StintRow::getSeason
        );
        replaceSeasonRange(
                pitStopsFile,
                data.getPitStops(),
                fromSeason,
                toSeason,
                new TypeReference<List<FastF1DomainData.PitStopRow>>() {},
                FastF1DomainData.PitStopRow::getSeason
        );
        replaceSeasonRange(
                weatherFile,
                data.getWeather(),
                fromSeason,
                toSeason,
                new TypeReference<List<FastF1DomainData.WeatherRow>>() {},
                FastF1DomainData.WeatherRow::getSeason
        );
        replaceSeasonRange(
                raceControlFile,
                data.getRaceControl(),
                fromSeason,
                toSeason,
                new TypeReference<List<FastF1DomainData.RaceControlRow>>() {},
                FastF1DomainData.RaceControlRow::getSeason
        );
        replaceSeasonRange(
                telemetryFile,
                data.getTelemetry(),
                fromSeason,
                toSeason,
                new TypeReference<List<FastF1DomainData.TelemetryRow>>() {},
                FastF1DomainData.TelemetryRow::getSeason
        );
    }

    private <T> List<T> readList(Path file, TypeReference<List<T>> typeRef) {
        if (Files.notExists(file)) {
            return new ArrayList<>();
        }
        try {
            return mapper.readValue(file.toFile(), typeRef);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to read FastF1 dataset file: " + file, e);
        }
    }

    private <T> void replaceSeasonRange(Path file,
                                        List<T> incoming,
                                        int fromSeason,
                                        int toSeason,
                                        TypeReference<List<T>> typeRef,
                                        ToIntFunction<T> seasonExtractor) {
        List<T> existing = readList(file, typeRef);
        List<T> merged = new ArrayList<>();
        for (T row : existing) {
            int season = seasonExtractor.applyAsInt(row);
            if (season < fromSeason || season > toSeason) {
                merged.add(row);
            }
        }
        for (T row : incoming) {
            int season = seasonExtractor.applyAsInt(row);
            if (season >= fromSeason && season <= toSeason) {
                merged.add(row);
            }
        }

        try {
            Files.createDirectories(datasetRoot);
            mapper.writerWithDefaultPrettyPrinter().writeValue(file.toFile(), merged);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to write FastF1 dataset file: " + file, e);
        }
    }
}
