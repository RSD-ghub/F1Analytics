package com.ocaprompts.f1;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.helidon.config.Config;
import io.helidon.config.ConfigSources;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

class F1DomainAnalyticsServiceTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private final F1AnalyticsService analyticsService = new F1AnalyticsService();

    @Test
    void shouldEnrichSeasonAndDriverAnalyticsFromDomainFiles() throws IOException {
        Path root = Files.createTempDirectory("fastf1-domain-test-");

        write(root.resolve("laps.json"), List.of(lapRow(2026, 1, "Max Verstappen", 78.123)));
        write(root.resolve("stints.json"), List.of(stintRow(2026, 1, "Max Verstappen", "SOFT", 18)));
        write(root.resolve("pit_stops.json"), List.of(pitStopRow(2026, 1, "Max Verstappen", 2.14)));
        write(root.resolve("weather.json"), List.of(weatherRow(2026, 1, 27.5, 39.2, true)));
        write(root.resolve("telemetry.json"), List.of(telemetryRow(2026, 1, "Max Verstappen", 336.7, 211.3)));
        write(root.resolve("race_control.json"), List.of(raceControlRow(2026, 1, "Flag", "Yellow flag sector 2", "00:11:05")));

        Config config = Config.builder()
                .sources(ConfigSources.create(Map.of("fastf1.dataset-root", root.toString())))
                .build();

        FastF1DatasetRepository fileRepository = new FastF1DatasetRepository(config);
        F1DomainAnalyticsService domainService = new F1DomainAnalyticsService();
        domainService.datasetRepository = new FastF1DomainRepository(config, fileRepository);

        List<Prompt> rows = List.of(promptRow(2026, 1, "Max Verstappen", "Red Bull", 1, 25));

        F1AnalyticsResponse seasonAnalytics = analyticsService.buildSeasonAnalytics(2026, rows);
        domainService.enrichSeasonAnalytics(seasonAnalytics, 2026, rows);

        assertEquals(1, seasonAnalytics.getLapTrend().size());
        assertEquals(1, seasonAnalytics.getStintSummary().size());
        assertEquals(1, seasonAnalytics.getPitStopStats().size());
        assertEquals(1, seasonAnalytics.getWeatherSummary().size());
        assertEquals(1, seasonAnalytics.getTelemetrySummary().size());
        assertEquals(1, seasonAnalytics.getRaceControlTimeline().size());

        DriverAnalyticsResponse driverAnalytics = analyticsService.buildDriverAnalytics("Max Verstappen", 2026, rows);
        domainService.enrichDriverAnalytics(driverAnalytics, "Max Verstappen", 2026, rows);

        assertFalse(driverAnalytics.getLapTrend().isEmpty());
        assertFalse(driverAnalytics.getStintSummary().isEmpty());
        assertFalse(driverAnalytics.getPitStopStats().isEmpty());
        assertFalse(driverAnalytics.getWeatherSummary().isEmpty());
        assertFalse(driverAnalytics.getTelemetrySummary().isEmpty());
        assertFalse(driverAnalytics.getRaceControlTimeline().isEmpty());
    }

    private void write(Path file, Object value) throws IOException {
        mapper.writerWithDefaultPrettyPrinter().writeValue(file.toFile(), value);
    }

    private FastF1DomainData.LapRow lapRow(int season, int round, String driver, double lapSeconds) {
        FastF1DomainData.LapRow row = new FastF1DomainData.LapRow();
        row.setSeason(season);
        row.setRound(round);
        row.setDriver(driver);
        row.setLap(1);
        row.setLapTimeSeconds(lapSeconds);
        row.setCompound("SOFT");
        row.setStint(1);
        return row;
    }

    private FastF1DomainData.StintRow stintRow(int season, int round, String driver, String compound, int laps) {
        FastF1DomainData.StintRow row = new FastF1DomainData.StintRow();
        row.setSeason(season);
        row.setRound(round);
        row.setDriver(driver);
        row.setStint(1);
        row.setCompound(compound);
        row.setLaps(laps);
        return row;
    }

    private FastF1DomainData.PitStopRow pitStopRow(int season, int round, String driver, double durationSeconds) {
        FastF1DomainData.PitStopRow row = new FastF1DomainData.PitStopRow();
        row.setSeason(season);
        row.setRound(round);
        row.setDriver(driver);
        row.setStop(1);
        row.setLap(22);
        row.setDurationSeconds(durationSeconds);
        return row;
    }

    private FastF1DomainData.WeatherRow weatherRow(int season, int round, double air, double track, boolean rain) {
        FastF1DomainData.WeatherRow row = new FastF1DomainData.WeatherRow();
        row.setSeason(season);
        row.setRound(round);
        row.setAirTemp(air);
        row.setTrackTemp(track);
        row.setHumidity(45.0);
        row.setRainfall(rain);
        return row;
    }

    private FastF1DomainData.TelemetryRow telemetryRow(int season, int round, String driver, double max, double avg) {
        FastF1DomainData.TelemetryRow row = new FastF1DomainData.TelemetryRow();
        row.setSeason(season);
        row.setRound(round);
        row.setDriver(driver);
        row.setMaxSpeed(max);
        row.setAvgSpeed(avg);
        row.setThrottleMean(67.0);
        row.setBrakeMean(12.0);
        return row;
    }

    private FastF1DomainData.RaceControlRow raceControlRow(int season, int round, String category, String message, String time) {
        FastF1DomainData.RaceControlRow row = new FastF1DomainData.RaceControlRow();
        row.setSeason(season);
        row.setRound(round);
        row.setCategory(category);
        row.setMessage(message);
        row.setTime(time);
        row.setLap(9);
        return row;
    }

    private Prompt promptRow(int season, int round, String driver, String team, int position, double points) {
        Prompt row = new Prompt();
        row.setId("row-1");
        row.setSeason(season);
        row.setRound(round);
        row.setRaceName("Race " + round);
        row.setCircuit("Circuit");
        row.setRaceDate("2026-03-01");
        row.setDriver(driver);
        row.setTeam(team);
        row.setPosition(position);
        row.setPoints(points);
        row.setTimestamp(System.currentTimeMillis());
        return row;
    }
}
