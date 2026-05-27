package com.ocaprompts.f1;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.helidon.config.Config;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Year;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@ApplicationScoped
public class FastF1IngestionService {
    private static final int MAX_SEASON = 2100;

    private final ObjectMapper mapper = new ObjectMapper();
    private final FastF1DomainRepository domainRepository;
    private final PromptRepository promptRepository;
    private final int seasonFloor;
    private final boolean pythonEnabled;
    private final String pythonExecutable;
    private final Path pythonScriptPath;
    private final Path pythonRequirementsPath;
    private final boolean fileSnapshotEnabled;
    private volatile FastF1IngestionStatus lastStatus;

    @Inject
    public FastF1IngestionService(Config config,
                                  FastF1DomainRepository domainRepository,
                                  PromptRepository promptRepository) {
        this.domainRepository = domainRepository;
        this.promptRepository = promptRepository;
        this.seasonFloor = config.get("fastf1.ingest.season-floor").asInt().orElse(2010);
        this.pythonEnabled = config.get("fastf1.python.enabled").asBoolean().orElse(true);
        this.pythonExecutable = config.get("fastf1.python.executable").asString().orElse("").trim();
        this.pythonScriptPath = resolvePath(
                config.get("fastf1.python.script-path").asString().orElse("scripts/fastf1-ingest/ingest.py")
        );
        this.pythonRequirementsPath = resolvePath(
                config.get("fastf1.python.requirements-path").asString().orElse("scripts/fastf1-ingest/requirements.txt")
        );
        this.fileSnapshotEnabled = config.get("fastf1.ingest.file-snapshot-enabled").asBoolean().orElse(false);
        this.lastStatus = domainRepository.readStatus().orElseGet(this::idleStatus);
    }

    FastF1IngestionService(Config config,
                           FastF1DomainRepository domainRepository,
                           PromptRepository promptRepository,
                           boolean fileSnapshotEnabled) {
        this.domainRepository = domainRepository;
        this.promptRepository = promptRepository;
        this.seasonFloor = config.get("fastf1.ingest.season-floor").asInt().orElse(2010);
        this.pythonEnabled = config.get("fastf1.python.enabled").asBoolean().orElse(true);
        this.pythonExecutable = config.get("fastf1.python.executable").asString().orElse("").trim();
        this.pythonScriptPath = resolvePath(
                config.get("fastf1.python.script-path").asString().orElse("scripts/fastf1-ingest/ingest.py")
        );
        this.pythonRequirementsPath = resolvePath(
                config.get("fastf1.python.requirements-path").asString().orElse("scripts/fastf1-ingest/requirements.txt")
        );
        this.fileSnapshotEnabled = fileSnapshotEnabled;
        this.lastStatus = domainRepository.readStatus().orElseGet(this::idleStatus);
    }

    public synchronized FastF1IngestionStatus backfillFromFloor() {
        int current = Year.now().getValue();
        return ingestRange(seasonFloor, current, "BACKFILL");
    }

    public synchronized FastF1IngestionStatus ingestRange(int fromSeason, int toSeason) {
        return ingestRange(fromSeason, toSeason, "MANUAL_RANGE");
    }

    public synchronized FastF1IngestionStatus ingestSeason(int season) {
        validateSeason(season);
        return ingestRange(season, season, "MANUAL_SEASON");
    }

    public synchronized FastF1IngestionStatus refreshCurrentSeason() {
        int current = Year.now().getValue();
        return ingestRange(current, current, "AUTO_REFRESH");
    }

    public synchronized FastF1IngestionStatus ensureBackfillFromFloor() {
        List<Integer> seasons = promptRepository.list().stream()
                .map(Prompt::getSeason)
                .distinct()
                .toList();
        if (!seasons.isEmpty()) {
            return getStatus();
        }
        return backfillFromFloor();
    }

    public synchronized FastF1IngestionStatus getStatus() {
        return copyOf(lastStatus);
    }

    public int getSeasonFloor() {
        return seasonFloor;
    }

    private FastF1IngestionStatus ingestRange(int fromSeason, int toSeason, String trigger) {
        validateSeason(fromSeason);
        validateSeason(toSeason);
        if (fromSeason > toSeason) {
            throw new IllegalArgumentException("fromSeason must be <= toSeason");
        }

        long started = System.currentTimeMillis();
        FastF1IngestionStatus running = new FastF1IngestionStatus();
        running.setState("RUNNING");
        running.setTrigger(trigger);
        running.setFromSeason(fromSeason);
        running.setToSeason(toSeason);
        running.setStartedAt(started);
        running.setMessage("FastF1 ingestion started");
        running.setRowCounts(new LinkedHashMap<>());
        lastStatus = running;
        domainRepository.writeStatus(running);

        Path outputDir = null;
        try {
            outputDir = prepareOutputDir();
            runLocalPythonIngest(fromSeason, toSeason, outputDir);

            FastF1IngestData ingested = loadIngestedData(outputDir);
            persistPromptResults(fromSeason, toSeason, ingested.getResults());
            domainRepository.persistIngestedRange(fromSeason, toSeason, ingested, fileSnapshotEnabled);

            long finished = System.currentTimeMillis();
            FastF1IngestionStatus success = new FastF1IngestionStatus();
            success.setState("SUCCESS");
            success.setTrigger(trigger);
            success.setFromSeason(fromSeason);
            success.setToSeason(toSeason);
            success.setStartedAt(started);
            success.setFinishedAt(finished);
            success.setDurationMs(Math.max(0, finished - started));
            success.setMessage("FastF1 ingestion completed");
            success.setRowCounts(collectRowCounts());
            lastStatus = success;
            domainRepository.writeStatus(success);
            return copyOf(success);
        } catch (RuntimeException e) {
            long finished = System.currentTimeMillis();
            FastF1IngestionStatus failure = new FastF1IngestionStatus();
            failure.setState("FAILED");
            failure.setTrigger(trigger);
            failure.setFromSeason(fromSeason);
            failure.setToSeason(toSeason);
            failure.setStartedAt(started);
            failure.setFinishedAt(finished);
            failure.setDurationMs(Math.max(0, finished - started));
            failure.setMessage(e.getMessage());
            failure.setRowCounts(collectRowCounts());
            lastStatus = failure;
            domainRepository.writeStatus(failure);
            throw e;
        } finally {
            cleanupTempOutput(outputDir);
        }
    }

    private Path prepareOutputDir() {
        if (fileSnapshotEnabled) {
            return domainRepository.getDatasetRoot().toAbsolutePath().normalize();
        }
        try {
            return Files.createTempDirectory("fastf1-ingest-").toAbsolutePath().normalize();
        } catch (IOException e) {
            throw new IllegalStateException("Failed to create temporary output directory for FastF1 ingest", e);
        }
    }

    private void cleanupTempOutput(Path outputDir) {
        if (outputDir == null || fileSnapshotEnabled) {
            return;
        }
        try {
            if (Files.notExists(outputDir)) {
                return;
            }
            Files.walk(outputDir)
                    .sorted(Comparator.reverseOrder())
                    .forEach(path -> {
                        try {
                            Files.deleteIfExists(path);
                        } catch (IOException ignored) {
                            // Best-effort cleanup for temp ingestion folders.
                        }
                    });
        } catch (IOException ignored) {
            // Best-effort cleanup for temp ingestion folders.
        }
    }

    private void persistPromptResults(int fromSeason, int toSeason, List<Prompt> importedRows) {
        Map<Integer, List<Prompt>> bySeason = importedRows.stream()
                .filter(row -> row.getSeason() >= fromSeason && row.getSeason() <= toSeason)
                .collect(Collectors.groupingBy(Prompt::getSeason));

        for (int season = fromSeason; season <= toSeason; season++) {
            List<Prompt> rows = bySeason.getOrDefault(season, List.of());
            promptRepository.replaceSeasonResults(season, rows);
        }
    }

    private FastF1IngestData loadIngestedData(Path outputDir) {
        FastF1IngestData data = new FastF1IngestData();
        data.setResults(readList(outputDir.resolve("results.json"), new TypeReference<List<Prompt>>() {}));
        data.setLaps(readList(outputDir.resolve("laps.json"), new TypeReference<List<FastF1DomainData.LapRow>>() {}));
        data.setStints(readList(outputDir.resolve("stints.json"), new TypeReference<List<FastF1DomainData.StintRow>>() {}));
        data.setPitStops(readList(outputDir.resolve("pit_stops.json"), new TypeReference<List<FastF1DomainData.PitStopRow>>() {}));
        data.setWeather(readList(outputDir.resolve("weather.json"), new TypeReference<List<FastF1DomainData.WeatherRow>>() {}));
        data.setRaceControl(readList(outputDir.resolve("race_control.json"), new TypeReference<List<FastF1DomainData.RaceControlRow>>() {}));
        data.setTelemetry(readList(outputDir.resolve("telemetry.json"), new TypeReference<List<FastF1DomainData.TelemetryRow>>() {}));
        return data;
    }

    private <T> List<T> readList(Path file, TypeReference<List<T>> typeRef) {
        if (Files.notExists(file)) {
            return new ArrayList<>();
        }
        try {
            return mapper.readValue(file.toFile(), typeRef);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to parse FastF1 ingest output file: " + file.toAbsolutePath(), e);
        }
    }

    private void runLocalPythonIngest(int fromSeason, int toSeason, Path outputDir) {
        if (!pythonEnabled) {
            throw new IllegalStateException("FastF1 local Python ingestion is disabled by configuration");
        }
        if (Files.notExists(pythonScriptPath)) {
            throw new IllegalStateException("FastF1 ingest script not found: " + pythonScriptPath.toAbsolutePath());
        }

        List<String> pythonCommand = resolvePythonCommand();

        List<String> command = new ArrayList<>(pythonCommand);
        command.add(pythonScriptPath.toAbsolutePath().toString());

        ProcessBuilder pb = new ProcessBuilder(command);
        pb.redirectErrorStream(true);
        pb.environment().put("OUTPUT_DIR", outputDir.toAbsolutePath().toString());
        pb.environment().put("FROM_SEASON", String.valueOf(fromSeason));
        pb.environment().put("TO_SEASON", String.valueOf(toSeason));

        try {
            Process process = pb.start();
            StringBuilder output = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    output.append(line).append('\n');
                }
            }
            int exitCode = process.waitFor();
            if (exitCode != 0) {
                throw new IllegalStateException(buildProcessFailureMessage(exitCode, output.toString(), pythonCommand));
            }
        } catch (IOException e) {
            String commandName = pythonExecutable.isBlank() ? "auto-detected Python command" : "'" + pythonExecutable + "'";
            throw new IllegalStateException(
                    "Unable to start local Python FastF1 ingest process using " + commandName
                            + ". Ensure Python 3 is installed and available on PATH.",
                    e
            );
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("FastF1 local Python ingest process interrupted", e);
        }
    }

    private List<String> resolvePythonCommand() {
        if (!pythonExecutable.isBlank()) {
            List<String> configured = tokenizeCommand(pythonExecutable);
            if (configured.isEmpty()) {
                throw new IllegalStateException("fastf1.python.executable must not be empty when configured");
            }
            return configured;
        }

        List<List<String>> candidates = List.of(
                List.of("py", "-3"),
                List.of("python"),
                List.of("python3")
        );
        for (List<String> candidate : candidates) {
            if (isCommandAvailable(candidate)) {
                return candidate;
            }
        }

        throw new IllegalStateException(
                "Unable to find a Python 3 executable. Install Python 3 or set fastf1.python.executable."
        );
    }

    private boolean isCommandAvailable(List<String> command) {
        List<String> probeCommand = new ArrayList<>(command);
        probeCommand.add("--version");
        ProcessBuilder pb = new ProcessBuilder(probeCommand);
        pb.redirectErrorStream(true);
        try {
            Process process = pb.start();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                while (reader.readLine() != null) {
                    // Drain stream to avoid blocking.
                }
            }
            return process.waitFor() == 0;
        } catch (IOException e) {
            return false;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private List<String> tokenizeCommand(String raw) {
        List<String> tokens = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        boolean inQuotes = false;
        char quoteChar = 0;

        for (int i = 0; i < raw.length(); i++) {
            char ch = raw.charAt(i);
            if ((ch == '"' || ch == '\'') && !inQuotes) {
                inQuotes = true;
                quoteChar = ch;
                continue;
            }
            if (inQuotes && ch == quoteChar) {
                inQuotes = false;
                quoteChar = 0;
                continue;
            }
            if (Character.isWhitespace(ch) && !inQuotes) {
                if (current.length() > 0) {
                    tokens.add(current.toString());
                    current.setLength(0);
                }
                continue;
            }
            current.append(ch);
        }
        if (current.length() > 0) {
            tokens.add(current.toString());
        }
        return tokens;
    }

    private String buildProcessFailureMessage(int exitCode, String output, List<String> pythonCommand) {
        String text = output == null ? "" : output.trim();
        String lower = text.toLowerCase();
        if (lower.contains("modulenotfounderror") || lower.contains("no module named")) {
            return "FastF1 Python dependencies are missing. Run `"
                    + buildInstallCommand(pythonCommand)
                    + "` and retry.";
        }
        if (text.isEmpty()) {
            return "FastF1 local ingest failed (exit " + exitCode + ")";
        }
        String truncated = text.length() > 3000 ? text.substring(0, 3000) + "..." : text;
        return "FastF1 local ingest failed (exit " + exitCode + "): " + truncated;
    }

    private String buildInstallCommand(List<String> pythonCommand) {
        String joined = String.join(" ", pythonCommand);
        Path requirements = pythonRequirementsPath.toAbsolutePath().normalize();
        return joined + " -m pip install -r \"" + requirements + "\"";
    }

    private Path resolvePath(String rawPath) {
        return Paths.get(rawPath).toAbsolutePath().normalize();
    }

    private Map<String, Integer> collectRowCounts() {
        Map<String, Integer> counts = new LinkedHashMap<>();
        counts.put("results", promptRepository.list().size());
        counts.put("laps", domainRepository.listLaps().size());
        counts.put("stints", domainRepository.listStints().size());
        counts.put("pitStops", domainRepository.listPitStops().size());
        counts.put("weather", domainRepository.listWeather().size());
        counts.put("raceControl", domainRepository.listRaceControl().size());
        counts.put("telemetry", domainRepository.listTelemetry().size());
        return counts;
    }

    private FastF1IngestionStatus idleStatus() {
        FastF1IngestionStatus idle = new FastF1IngestionStatus();
        idle.setState("IDLE");
        idle.setMessage("No FastF1 ingestion run yet");
        idle.setFromSeason(seasonFloor);
        idle.setToSeason(Year.now().getValue());
        idle.setRowCounts(collectRowCounts());
        return idle;
    }

    private FastF1IngestionStatus copyOf(FastF1IngestionStatus source) {
        FastF1IngestionStatus copy = new FastF1IngestionStatus();
        copy.setState(source.getState());
        copy.setTrigger(source.getTrigger());
        copy.setFromSeason(source.getFromSeason());
        copy.setToSeason(source.getToSeason());
        copy.setStartedAt(source.getStartedAt());
        copy.setFinishedAt(source.getFinishedAt());
        copy.setDurationMs(source.getDurationMs());
        copy.setMessage(source.getMessage());
        Map<String, Integer> rowCounts = source.getRowCounts() == null
                ? new LinkedHashMap<>()
                : new LinkedHashMap<>(source.getRowCounts());
        copy.setRowCounts(rowCounts);
        return copy;
    }

    private void validateSeason(int season) {
        if (season < seasonFloor || season > MAX_SEASON) {
            throw new IllegalArgumentException("Season must be between " + seasonFloor + " and " + MAX_SEASON);
        }
    }
}
