package com.ocaprompts.f1;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.helidon.config.Config;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.locks.ReadWriteLock;
import java.util.concurrent.locks.ReentrantReadWriteLock;

@ApplicationScoped
public class FilePromptRepository {

    private static final Path LEGACY_FILE = Paths.get("C:\\Deepu\\OCA-Prompts\\f1-results.json");
    private final Path file;
    private final ObjectMapper mapper = new ObjectMapper();
    private final ReadWriteLock lock = new ReentrantReadWriteLock();

    @Inject
    public FilePromptRepository(Config config) {
        String root = config.get("fastf1.dataset-root").asString().orElse("fastf1-data");
        this.file = Paths.get(root).resolve("results.json");
    }

    public Path getFilePath() {
        return file;
    }

    public List<Prompt> list() {
        lock.readLock().lock();
        try {
            Path activeFile = resolveReadFile();
            if (Files.notExists(activeFile)) {
                return new ArrayList<>();
            }

            List<Prompt> all = mapper.readValue(activeFile.toFile(), new TypeReference<List<Prompt>>() {});
            sortResults(all);
            return all;
        } catch (IOException e) {
            throw new IllegalStateException("Failed to read F1 results", e);
        } finally {
            lock.readLock().unlock();
        }
    }

    public Optional<Prompt> get(String id) {
        return list().stream()
                .filter(p -> p.getId().equals(id))
                .findFirst();
    }

    public Prompt add(Prompt p) {
        lock.writeLock().lock();
        try {
            List<Prompt> all = list();
            all.add(p);
            save(all);
            return p;
        } finally {
            lock.writeLock().unlock();
        }
    }

    public int replaceSeasonResults(int season, List<Prompt> imported) {
        lock.writeLock().lock();
        try {
            List<Prompt> all = list();
            all.removeIf(p -> p.getSeason() == season);
            all.addAll(imported);
            sortResults(all);
            save(all);
            return imported.size();
        } finally {
            lock.writeLock().unlock();
        }
    }

    public boolean delete(String id) {
        lock.writeLock().lock();
        try {
            List<Prompt> all = list();
            boolean removed = all.removeIf(p -> p.getId().equals(id));
            if (removed) {
                save(all);
            }
            return removed;
        } finally {
            lock.writeLock().unlock();
        }
    }

    public List<StandingsEntry> driverStandings() {
        return StandingsSupport.aggregateByDriver(list());
    }

    public List<StandingsEntry> teamStandings() {
        return StandingsSupport.aggregateByTeam(list());
    }

    private void save(List<Prompt> all) {
        try {
            Files.createDirectories(file.getParent());
            mapper.writerWithDefaultPrettyPrinter().writeValue(file.toFile(), all);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to write F1 results", e);
        }
    }

    private Path resolveReadFile() {
        if (Files.exists(file)) {
            return file;
        }
        if (Files.exists(LEGACY_FILE)) {
            return LEGACY_FILE;
        }
        return file;
    }

    private void sortResults(List<Prompt> all) {
        all.sort(Comparator
                .comparingInt(Prompt::getSeason).reversed()
                .thenComparing(Comparator.comparingInt(Prompt::getRound).reversed())
                .thenComparingInt(Prompt::getPosition));
    }
}
