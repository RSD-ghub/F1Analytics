package com.ocaprompts.f1;

import io.helidon.config.Config;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

@ApplicationScoped
public class FastF1AutoRefreshScheduler {

    private final FastF1IngestionService ingestionService;
    private final boolean enabled;
    private final long refreshHours;
    private ScheduledExecutorService executor;

    @Inject
    public FastF1AutoRefreshScheduler(Config config, FastF1IngestionService ingestionService) {
        this.ingestionService = ingestionService;
        this.enabled = config.get("fastf1.ingest.auto-refresh-enabled").asBoolean().orElse(true);
        this.refreshHours = Math.max(1, config.get("fastf1.ingest.auto-refresh-hours").asLong().orElse(24L));
    }

    @PostConstruct
    void start() {
        if (!enabled) {
            return;
        }

        executor = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "fastf1-refresh-scheduler");
            thread.setDaemon(true);
            return thread;
        });

        executor.execute(() -> {
            try {
                ingestionService.ensureBackfillFromFloor();
            } catch (Exception ignored) {
                // Startup backfill failure is captured in ingestion status and should not block app boot.
            }
        });

        executor.scheduleAtFixedRate(() -> {
            try {
                ingestionService.refreshCurrentSeason();
            } catch (Exception ignored) {
                // Failures are persisted in ingestion status; scheduler continues running.
            }
        }, refreshHours, refreshHours, TimeUnit.HOURS);
    }

    @PreDestroy
    void stop() {
        if (executor != null) {
            executor.shutdownNow();
        }
    }
}
