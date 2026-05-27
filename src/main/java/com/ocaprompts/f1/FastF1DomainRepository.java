package com.ocaprompts.f1;

import io.helidon.config.Config;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.function.Function;
import java.util.logging.Level;
import java.util.logging.Logger;

@ApplicationScoped
public class FastF1DomainRepository {
    private static final Logger LOGGER = Logger.getLogger(FastF1DomainRepository.class.getName());

    private final FastF1DatasetRepository fileRepository;
    private final MongoFastF1DatasetRepository mongoRepository;
    private final List<String> readOrder;

    @Inject
    public FastF1DomainRepository(Config config,
                                  FastF1DatasetRepository fileRepository,
                                  MongoFastF1DatasetRepository mongoRepository) {
        this.fileRepository = fileRepository;
        this.mongoRepository = mongoRepository;
        this.readOrder = parseBackends(config.get("fastf1.storage.read-order").asString().orElse("mongo,file"));
    }

    FastF1DomainRepository(Config config, FastF1DatasetRepository fileRepository) {
        this.fileRepository = fileRepository;
        this.mongoRepository = null;
        this.readOrder = removeMongo(parseBackends(config.get("fastf1.storage.read-order").asString().orElse("file")));
    }

    public Path getDatasetRoot() {
        return fileRepository.getDatasetRoot();
    }

    public Optional<FastF1IngestionStatus> readStatus() {
        return fileRepository.readStatus();
    }

    public void writeStatus(FastF1IngestionStatus status) {
        fileRepository.writeStatus(status);
    }

    public List<FastF1DomainData.LapRow> listLaps() {
        List<FastF1DomainData.LapRow> mongoRows = readOrder.contains("mongo")
                ? safeList("mongo", () -> mongoRepository == null ? List.of() : mongoRepository.listLaps())
                : List.of();
        List<FastF1DomainData.LapRow> fileRows = readOrder.contains("file")
                ? safeList("file", fileRepository::listLaps)
                : List.of();
        return merge(
                mongoRows,
                fileRows,
                row -> join(
                        row.getSeason(),
                        row.getRound(),
                        row.getDriver(),
                        row.getLap(),
                        row.getStint(),
                        row.getCompound(),
                        row.getLapTimeSeconds())
        );
    }

    public List<FastF1DomainData.StintRow> listStints() {
        List<FastF1DomainData.StintRow> mongoRows = readOrder.contains("mongo")
                ? safeList("mongo", () -> mongoRepository == null ? List.of() : mongoRepository.listStints())
                : List.of();
        List<FastF1DomainData.StintRow> fileRows = readOrder.contains("file")
                ? safeList("file", fileRepository::listStints)
                : List.of();
        return merge(
                mongoRows,
                fileRows,
                row -> join(
                        row.getSeason(),
                        row.getRound(),
                        row.getDriver(),
                        row.getStint(),
                        row.getCompound(),
                        row.getLaps())
        );
    }

    public List<FastF1DomainData.PitStopRow> listPitStops() {
        List<FastF1DomainData.PitStopRow> mongoRows = readOrder.contains("mongo")
                ? safeList("mongo", () -> mongoRepository == null ? List.of() : mongoRepository.listPitStops())
                : List.of();
        List<FastF1DomainData.PitStopRow> fileRows = readOrder.contains("file")
                ? safeList("file", fileRepository::listPitStops)
                : List.of();
        return merge(
                mongoRows,
                fileRows,
                row -> join(
                        row.getSeason(),
                        row.getRound(),
                        row.getDriver(),
                        row.getStop(),
                        row.getLap(),
                        row.getDurationSeconds())
        );
    }

    public List<FastF1DomainData.WeatherRow> listWeather() {
        List<FastF1DomainData.WeatherRow> mongoRows = readOrder.contains("mongo")
                ? safeList("mongo", () -> mongoRepository == null ? List.of() : mongoRepository.listWeather())
                : List.of();
        List<FastF1DomainData.WeatherRow> fileRows = readOrder.contains("file")
                ? safeList("file", fileRepository::listWeather)
                : List.of();
        return merge(
                mongoRows,
                fileRows,
                row -> join(
                        row.getSeason(),
                        row.getRound(),
                        row.getRaceName(),
                        row.getSampleTime(),
                        row.getAirTemp(),
                        row.getTrackTemp(),
                        row.getHumidity(),
                        row.isRainfall())
        );
    }

    public List<FastF1DomainData.RaceControlRow> listRaceControl() {
        List<FastF1DomainData.RaceControlRow> mongoRows = readOrder.contains("mongo")
                ? safeList("mongo", () -> mongoRepository == null ? List.of() : mongoRepository.listRaceControl())
                : List.of();
        List<FastF1DomainData.RaceControlRow> fileRows = readOrder.contains("file")
                ? safeList("file", fileRepository::listRaceControl)
                : List.of();
        return merge(
                mongoRows,
                fileRows,
                row -> join(
                        row.getSeason(),
                        row.getRound(),
                        row.getCategory(),
                        row.getMessage(),
                        row.getTime(),
                        row.getLap())
        );
    }

    public List<FastF1DomainData.TelemetryRow> listTelemetry() {
        List<FastF1DomainData.TelemetryRow> mongoRows = readOrder.contains("mongo")
                ? safeList("mongo", () -> mongoRepository == null ? List.of() : mongoRepository.listTelemetry())
                : List.of();
        List<FastF1DomainData.TelemetryRow> fileRows = readOrder.contains("file")
                ? safeList("file", fileRepository::listTelemetry)
                : List.of();
        return merge(
                mongoRows,
                fileRows,
                row -> join(
                        row.getSeason(),
                        row.getRound(),
                        row.getDriver(),
                        row.getSampleTime(),
                        row.getMaxSpeed(),
                        row.getAvgSpeed(),
                        row.getThrottleMean(),
                        row.getBrakeMean())
        );
    }

    public void persistIngestedRange(int fromSeason, int toSeason, FastF1IngestData data, boolean writeFileSnapshot) {
        boolean mongoSuccess = false;
        if (mongoRepository != null) {
            try {
                mongoRepository.replaceSeasonRange(fromSeason, toSeason, data);
                mongoSuccess = true;
            } catch (RuntimeException e) {
                LOGGER.log(Level.WARNING, "Failed to write FastF1 domain data to Mongo", e);
            }
        }

        boolean fileSuccess = false;
        if (writeFileSnapshot || !mongoSuccess) {
            try {
                fileRepository.replaceSeasonRange(fromSeason, toSeason, data);
                fileSuccess = true;
            } catch (RuntimeException e) {
                LOGGER.log(Level.WARNING, "Failed to write FastF1 domain snapshot files", e);
            }
        }

        if (!mongoSuccess && !fileSuccess) {
            throw new IllegalStateException("Failed to persist FastF1 domain data to configured storage");
        }
    }

    private <T> List<T> merge(List<T> mongoRows, List<T> fileRows, Function<T, String> keyFn) {
        List<List<T>> byOrder = new ArrayList<>();
        for (String backend : readOrder) {
            if ("mongo".equals(backend)) {
                byOrder.add(mongoRows);
            } else if ("file".equals(backend)) {
                byOrder.add(fileRows);
            }
        }
        if (byOrder.isEmpty()) {
            byOrder.add(fileRows);
        }

        Map<String, T> merged = new LinkedHashMap<>();
        for (List<T> rows : byOrder) {
            for (T row : rows) {
                merged.putIfAbsent(keyFn.apply(row), row);
            }
        }
        return new ArrayList<>(merged.values());
    }

    private <T> List<T> safeList(String backend, Provider<T> provider) {
        try {
            return provider.get();
        } catch (RuntimeException e) {
            LOGGER.log(Level.WARNING, "Failed to read FastF1 " + backend + " domain dataset. Falling back.", e);
            return List.of();
        }
    }

    private List<String> parseBackends(String raw) {
        String[] parts = raw.split(",");
        Set<String> values = new LinkedHashSet<>();
        for (String part : parts) {
            String normalized = normalize(part);
            if (!normalized.isEmpty()) {
                values.add(normalized);
            }
        }
        if (values.isEmpty()) {
            values.add("file");
        }
        return List.copyOf(values);
    }

    private List<String> removeMongo(List<String> backends) {
        List<String> filtered = backends.stream()
                .filter(value -> !"mongo".equals(value))
                .toList();
        if (filtered.isEmpty()) {
            return List.of("file");
        }
        return filtered;
    }

    private String normalize(String value) {
        return Objects.toString(value, "")
                .trim()
                .toLowerCase(Locale.ROOT);
    }

    private String join(Object... values) {
        StringBuilder key = new StringBuilder();
        for (int i = 0; i < values.length; i++) {
            if (i > 0) {
                key.append('|');
            }
            key.append(Objects.toString(values[i], ""));
        }
        return key.toString();
    }

    @FunctionalInterface
    private interface Provider<T> {
        List<T> get();
    }
}
