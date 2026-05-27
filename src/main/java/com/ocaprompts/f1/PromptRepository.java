package com.ocaprompts.f1;

import io.helidon.config.Config;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.logging.Level;
import java.util.logging.Logger;

@ApplicationScoped
public class PromptRepository {
    private static final Logger LOGGER = Logger.getLogger(PromptRepository.class.getName());

    private final FilePromptRepository fileRepository;
    private final MongoPromptRepository mongoRepository;
    private final List<String> readOrder;
    private final List<String> writeTargets;
    private final boolean parityLogEnabled;

    @Inject
    public PromptRepository(Config config,
                            FilePromptRepository fileRepository,
                            MongoPromptRepository mongoRepository) {
        this.fileRepository = fileRepository;
        this.mongoRepository = mongoRepository;
        this.readOrder = parseBackends(config.get("fastf1.storage.read-order").asString().orElse("mongo,file"));
        this.writeTargets = parseBackends(config.get("fastf1.storage.write-targets").asString().orElse("mongo,file"));
        this.parityLogEnabled = config.get("fastf1.storage.parity-log-enabled").asBoolean().orElse(true);
    }

    PromptRepository(Config config, FilePromptRepository fileRepository) {
        this.fileRepository = fileRepository;
        this.mongoRepository = null;
        this.readOrder = removeMongo(parseBackends(config.get("fastf1.storage.read-order").asString().orElse("file")));
        this.writeTargets = removeMongo(parseBackends(config.get("fastf1.storage.write-targets").asString().orElse("file")));
        this.parityLogEnabled = false;
    }

    public List<Prompt> list() {
        Map<String, List<Prompt>> rowsByBackend = new LinkedHashMap<>();
        Map<String, Prompt> merged = new LinkedHashMap<>();

        for (String backend : readOrder) {
            List<Prompt> rows = safeList(backend);
            rowsByBackend.put(backend, rows);
            for (Prompt row : rows) {
                String key = row.getId();
                if (key == null || key.isBlank()) {
                    key = fallbackPromptKey(row);
                }
                merged.putIfAbsent(key, row);
            }
        }

        logParity(rowsByBackend);

        List<Prompt> list = new ArrayList<>(merged.values());
        sortResults(list);
        return list;
    }

    public Optional<Prompt> get(String id) {
        for (String backend : readOrder) {
            Optional<Prompt> row = safeGet(backend, id);
            if (row.isPresent()) {
                return row;
            }
        }
        return Optional.empty();
    }

    public Prompt add(Prompt prompt) {
        List<String> failures = new ArrayList<>();
        boolean success = false;
        for (String backend : writeTargets) {
            try {
                backend(backend).add(prompt);
                success = true;
            } catch (RuntimeException e) {
                failures.add(backend + ": " + e.getMessage());
                LOGGER.log(Level.WARNING, "Failed to write prompt to backend " + backend, e);
            }
        }
        if (!success) {
            throw new IllegalStateException("Failed to persist prompt to any backend: " + String.join("; ", failures));
        }
        return prompt;
    }

    public int replaceSeasonResults(int season, List<Prompt> imported) {
        List<String> failures = new ArrayList<>();
        boolean success = false;
        Integer primaryCount = null;
        for (String backend : writeTargets) {
            try {
                int written = backend(backend).replaceSeasonResults(season, imported);
                if (primaryCount == null) {
                    primaryCount = written;
                }
                success = true;
            } catch (RuntimeException e) {
                failures.add(backend + ": " + e.getMessage());
                LOGGER.log(Level.WARNING, "Failed to replace season results on backend " + backend, e);
            }
        }
        if (!success) {
            throw new IllegalStateException("Failed to replace season results on any backend: " + String.join("; ", failures));
        }
        return primaryCount == null ? imported.size() : primaryCount;
    }

    public boolean delete(String id) {
        List<String> failures = new ArrayList<>();
        boolean removed = false;
        boolean success = false;
        for (String backend : writeTargets) {
            try {
                removed = backend(backend).delete(id) || removed;
                success = true;
            } catch (RuntimeException e) {
                failures.add(backend + ": " + e.getMessage());
                LOGGER.log(Level.WARNING, "Failed to delete prompt on backend " + backend, e);
            }
        }
        if (!success) {
            throw new IllegalStateException("Failed to delete prompt on any backend: " + String.join("; ", failures));
        }
        return removed;
    }

    public List<StandingsEntry> driverStandings() {
        return StandingsSupport.aggregateByDriver(list());
    }

    public List<StandingsEntry> teamStandings() {
        return StandingsSupport.aggregateByTeam(list());
    }

    public int countSeasonResults(int season) {
        return (int) list().stream().filter(row -> row.getSeason() == season).count();
    }

    private void logParity(Map<String, List<Prompt>> rowsByBackend) {
        if (!parityLogEnabled) {
            return;
        }
        if (!rowsByBackend.containsKey("mongo") || !rowsByBackend.containsKey("file")) {
            return;
        }

        List<Prompt> mongo = rowsByBackend.get("mongo");
        List<Prompt> file = rowsByBackend.get("file");

        if (mongo.size() != file.size()) {
            LOGGER.log(Level.INFO, "Prompt parity mismatch mongo={0} file={1}",
                    new Object[]{mongo.size(), file.size()});
        }
    }

    private List<Prompt> safeList(String backend) {
        try {
            return backend(backend).list();
        } catch (RuntimeException e) {
            LOGGER.log(Level.WARNING, "Failed to read prompts from backend " + backend + ". Falling back.", e);
            return List.of();
        }
    }

    private Optional<Prompt> safeGet(String backend, String id) {
        try {
            return backend(backend).get(id);
        } catch (RuntimeException e) {
            LOGGER.log(Level.WARNING, "Failed to read prompt by id from backend " + backend + ". Falling back.", e);
            return Optional.empty();
        }
    }

    private PromptBackend backend(String name) {
        String normalized = normalize(name);
        return switch (normalized) {
            case "mongo" -> {
                if (mongoRepository == null) {
                    throw new IllegalStateException("Mongo backend is not available");
                }
                yield new PromptBackend() {
                    @Override
                    public List<Prompt> list() {
                        return mongoRepository.list();
                    }

                    @Override
                    public Optional<Prompt> get(String id) {
                        return mongoRepository.get(id);
                    }

                    @Override
                    public Prompt add(Prompt prompt) {
                        return mongoRepository.add(prompt);
                    }

                    @Override
                    public int replaceSeasonResults(int season, List<Prompt> imported) {
                        return mongoRepository.replaceSeasonResults(season, imported);
                    }

                    @Override
                    public boolean delete(String id) {
                        return mongoRepository.delete(id);
                    }
                };
            }
            case "file" -> new PromptBackend() {
                @Override
                public List<Prompt> list() {
                    return fileRepository.list();
                }

                @Override
                public Optional<Prompt> get(String id) {
                    return fileRepository.get(id);
                }

                @Override
                public Prompt add(Prompt prompt) {
                    return fileRepository.add(prompt);
                }

                @Override
                public int replaceSeasonResults(int season, List<Prompt> imported) {
                    return fileRepository.replaceSeasonResults(season, imported);
                }

                @Override
                public boolean delete(String id) {
                    return fileRepository.delete(id);
                }
            };
            default -> throw new IllegalArgumentException("Unknown prompt backend: " + name);
        };
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

    private String fallbackPromptKey(Prompt prompt) {
        return String.join("|",
                String.valueOf(prompt.getSeason()),
                String.valueOf(prompt.getRound()),
                Objects.toString(prompt.getRaceName(), ""),
                Objects.toString(prompt.getDriver(), ""),
                Objects.toString(prompt.getTeam(), ""),
                String.valueOf(prompt.getPosition()));
    }

    private void sortResults(List<Prompt> rows) {
        rows.sort(Comparator
                .comparingInt(Prompt::getSeason).reversed()
                .thenComparing(Comparator.comparingInt(Prompt::getRound).reversed())
                .thenComparingInt(Prompt::getPosition));
    }

    private interface PromptBackend {
        List<Prompt> list();

        Optional<Prompt> get(String id);

        Prompt add(Prompt prompt);

        int replaceSeasonResults(int season, List<Prompt> imported);

        boolean delete(String id);
    }
}
