package com.ocaprompts.f1;

import io.helidon.config.Config;
import io.helidon.config.ConfigSources;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FastF1IngestionServiceTest {

    @Test
    void shouldRejectInvalidSeasonAndRange() throws IOException {
        Fixture fixture = createFixture(Map.of("fastf1.python.enabled", "false"));
        FastF1IngestionService service = fixture.service();

        IllegalArgumentException badSeason = assertThrows(
                IllegalArgumentException.class,
                () -> service.ingestSeason(2009)
        );
        assertTrue(badSeason.getMessage().contains("Season must be between"));

        IllegalArgumentException badRange = assertThrows(
                IllegalArgumentException.class,
                () -> service.ingestRange(2026, 2025)
        );
        assertEquals("fromSeason must be <= toSeason", badRange.getMessage());
    }

    @Test
    void shouldFailWhenExecutableIsMissing() throws IOException {
        Fixture fixture = createFixture(Map.of(
                "fastf1.python.enabled", "true",
                "fastf1.python.executable", "missing-python-binary-xyz-123"
        ));
        FastF1IngestionService service = fixture.service();

        IllegalStateException error = assertThrows(
                IllegalStateException.class,
                () -> service.ingestSeason(2026)
        );
        assertTrue(error.getMessage().contains("Unable to start local Python FastF1 ingest process"));
        assertEquals("FAILED", service.getStatus().getState());
    }

    @Test
    void shouldFailWhenProcessReturnsNonZeroExit() throws IOException {
        Path javaExecutable = javaExecutablePath();
        Fixture fixture = createFixture(Map.of(
                "fastf1.python.enabled", "true",
                "fastf1.python.executable", javaExecutable.toString()
        ));
        FastF1IngestionService service = fixture.service();

        IllegalStateException error = assertThrows(
                IllegalStateException.class,
                () -> service.ingestSeason(2026)
        );
        assertTrue(error.getMessage().contains("FastF1 local ingest failed (exit"));
        assertEquals("FAILED", service.getStatus().getState());
    }

    private Fixture createFixture(Map<String, String> overrides) throws IOException {
        Path datasetRoot = Files.createTempDirectory("fastf1-ingestion-test-");
        Files.writeString(datasetRoot.resolve("results.json"), "[]");

        Path script = datasetRoot.resolve("dummy-ingest.py");
        Files.writeString(script, "print('dummy')");

        Path requirements = datasetRoot.resolve("requirements.txt");
        Files.writeString(requirements, "fastf1==3.5.3");

        Map<String, String> values = new LinkedHashMap<>();
        values.put("fastf1.dataset-root", datasetRoot.toString());
        values.put("fastf1.ingest.season-floor", "2010");
        values.put("fastf1.python.enabled", "true");
        values.put("fastf1.python.executable", "");
        values.put("fastf1.python.script-path", script.toString());
        values.put("fastf1.python.requirements-path", requirements.toString());
        values.putAll(overrides);

        Config config = Config.builder()
                .sources(ConfigSources.create(values))
                .build();

        FastF1DatasetRepository datasetRepository = new FastF1DatasetRepository(config);
        FilePromptRepository filePromptRepository = new FilePromptRepository(config);
        PromptRepository promptRepository = new PromptRepository(config, filePromptRepository);
        FastF1DomainRepository domainRepository = new FastF1DomainRepository(config, datasetRepository);
        FastF1IngestionService service = new FastF1IngestionService(config, domainRepository, promptRepository, true);
        return new Fixture(service);
    }

    private Path javaExecutablePath() {
        boolean windows = System.getProperty("os.name", "").toLowerCase().contains("win");
        return Paths.get(System.getProperty("java.home"), "bin", windows ? "java.exe" : "java");
    }

    private record Fixture(FastF1IngestionService service) {
    }
}
