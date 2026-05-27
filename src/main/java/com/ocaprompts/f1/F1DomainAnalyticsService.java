package com.ocaprompts.f1;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

@ApplicationScoped
public class F1DomainAnalyticsService {

    @Inject
    FastF1DomainRepository datasetRepository;

    public void enrichSeasonAnalytics(F1AnalyticsResponse response, int season, List<Prompt> allRows) {
        List<Prompt> seasonRows = allRows.stream()
                .filter(row -> row.getSeason() == season)
                .toList();

        response.setLapTrend(buildSeasonLapTrend(season));
        response.setStintSummary(buildSeasonStintSummary(season));
        response.setPitStopStats(buildSeasonPitStopStats(season, seasonRows));
        response.setWeatherSummary(buildSeasonWeatherSummary(season));
        response.setTelemetrySummary(buildSeasonTelemetrySummary(season));
        response.setRaceControlTimeline(buildSeasonRaceControlTimeline(season));
    }

    public void enrichDriverAnalytics(DriverAnalyticsResponse response,
                                      String driverName,
                                      Integer season,
                                      List<Prompt> allRows) {
        String key = driverName == null ? "" : driverName.trim().toLowerCase(Locale.ROOT);
        List<Prompt> scopedRows = allRows.stream()
                .filter(row -> row.getDriver() != null && !row.getDriver().isBlank())
                .filter(row -> row.getDriver().trim().toLowerCase(Locale.ROOT).equals(key))
                .filter(row -> season == null || row.getSeason() == season)
                .toList();

        response.setLapTrend(buildDriverLapTrend(scopedRows, key));
        response.setStintSummary(buildDriverStintSummary(scopedRows, key));
        response.setPitStopStats(buildDriverPitStopStats(scopedRows, key));
        response.setWeatherSummary(buildDriverWeatherSummary(scopedRows));
        response.setTelemetrySummary(buildDriverTelemetrySummary(scopedRows, key));
        response.setRaceControlTimeline(buildDriverRaceControlTimeline(scopedRows));
    }

    private List<F1AnalyticsResponse.LapTrendPoint> buildSeasonLapTrend(int season) {
        return datasetRepository.listLaps().stream()
                .filter(row -> row.getSeason() == season)
                .filter(row -> row.getLapTimeSeconds() > 0)
                .collect(Collectors.groupingBy(
                        FastF1DomainData.LapRow::getRound,
                        LinkedHashMap::new,
                        Collectors.averagingDouble(FastF1DomainData.LapRow::getLapTimeSeconds)
                ))
                .entrySet()
                .stream()
                .sorted(Map.Entry.comparingByKey())
                .map(entry -> new F1AnalyticsResponse.LapTrendPoint("R" + entry.getKey(), entry.getKey(), entry.getValue()))
                .toList();
    }

    private List<F1AnalyticsResponse.StintSummary> buildSeasonStintSummary(int season) {
        return datasetRepository.listStints().stream()
                .filter(row -> row.getSeason() == season)
                .filter(row -> row.getCompound() != null && !row.getCompound().isBlank())
                .collect(Collectors.groupingBy(
                        row -> row.getCompound().trim(),
                        Collectors.summingInt(FastF1DomainData.StintRow::getLaps)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.<Map.Entry<String, Integer>>comparingInt(Map.Entry::getValue).reversed()
                        .thenComparing(Map.Entry::getKey))
                .map(entry -> new F1AnalyticsResponse.StintSummary(entry.getKey(), entry.getValue()))
                .toList();
    }

    private List<F1AnalyticsResponse.PitStopStat> buildSeasonPitStopStats(int season, List<Prompt> seasonRows) {
        Map<String, String> driverToTeam = seasonRows.stream()
                .filter(row -> row.getDriver() != null && !row.getDriver().isBlank())
                .collect(Collectors.toMap(
                        row -> row.getDriver().trim().toLowerCase(Locale.ROOT),
                        row -> row.getTeam() == null ? "" : row.getTeam().trim(),
                        (left, right) -> left
                ));

        Map<String, List<FastF1DomainData.PitStopRow>> byTeam = datasetRepository.listPitStops().stream()
                .filter(row -> row.getSeason() == season)
                .collect(Collectors.groupingBy(row -> {
                    String driverKey = row.getDriver() == null ? "" : row.getDriver().trim().toLowerCase(Locale.ROOT);
                    String team = driverToTeam.getOrDefault(driverKey, "Unknown");
                    return team.isBlank() ? "Unknown" : team;
                }));

        return byTeam.entrySet().stream()
                .map(entry -> {
                    List<FastF1DomainData.PitStopRow> rows = entry.getValue();
                    double avg = rows.stream()
                            .mapToDouble(FastF1DomainData.PitStopRow::getDurationSeconds)
                            .filter(value -> value > 0)
                            .average()
                            .orElse(0);
                    return new F1AnalyticsResponse.PitStopStat(entry.getKey(), rows.size(), avg);
                })
                .sorted(Comparator.comparing(F1AnalyticsResponse.PitStopStat::getEntity))
                .toList();
    }

    private List<F1AnalyticsResponse.WeatherPoint> buildSeasonWeatherSummary(int season) {
        return datasetRepository.listWeather().stream()
                .filter(row -> row.getSeason() == season)
                .collect(Collectors.groupingBy(
                        FastF1DomainData.WeatherRow::getRound,
                        Collectors.toList()
                ))
                .entrySet()
                .stream()
                .sorted(Map.Entry.comparingByKey())
                .map(entry -> {
                    int round = entry.getKey();
                    List<FastF1DomainData.WeatherRow> rows = entry.getValue();
                    double air = rows.stream().mapToDouble(FastF1DomainData.WeatherRow::getAirTemp).average().orElse(0);
                    double track = rows.stream().mapToDouble(FastF1DomainData.WeatherRow::getTrackTemp).average().orElse(0);
                    long rain = rows.stream().filter(FastF1DomainData.WeatherRow::isRainfall).count();
                    return new F1AnalyticsResponse.WeatherPoint("R" + round, round, air, track, (int) rain);
                })
                .toList();
    }

    private List<F1AnalyticsResponse.TelemetrySummary> buildSeasonTelemetrySummary(int season) {
        return datasetRepository.listTelemetry().stream()
                .filter(row -> row.getSeason() == season)
                .collect(Collectors.groupingBy(
                        row -> safeText(row.getDriver()),
                        Collectors.toList()
                ))
                .entrySet()
                .stream()
                .map(entry -> {
                    List<FastF1DomainData.TelemetryRow> rows = entry.getValue();
                    double max = rows.stream().mapToDouble(FastF1DomainData.TelemetryRow::getMaxSpeed).max().orElse(0);
                    double avg = rows.stream().mapToDouble(FastF1DomainData.TelemetryRow::getAvgSpeed).average().orElse(0);
                    return new F1AnalyticsResponse.TelemetrySummary(entry.getKey(), max, avg);
                })
                .sorted(Comparator.comparing(F1AnalyticsResponse.TelemetrySummary::getEntity))
                .toList();
    }

    private List<F1AnalyticsResponse.RaceControlEntry> buildSeasonRaceControlTimeline(int season) {
        return datasetRepository.listRaceControl().stream()
                .filter(row -> row.getSeason() == season)
                .sorted(Comparator
                        .comparingInt(FastF1DomainData.RaceControlRow::getRound)
                        .thenComparing(FastF1DomainData.RaceControlRow::getTime, Comparator.nullsLast(String::compareTo)))
                .limit(120)
                .map(row -> new F1AnalyticsResponse.RaceControlEntry(
                        "R" + row.getRound(),
                        safeText(row.getCategory()),
                        safeText(row.getMessage()),
                        safeText(row.getTime())
                ))
                .toList();
    }

    private List<DriverAnalyticsResponse.LapTrendPoint> buildDriverLapTrend(List<Prompt> scopedRows, String driverKey) {
        Map<String, Integer> roundToSeason = scopedRows.stream()
                .collect(Collectors.toMap(
                        row -> row.getSeason() + "-" + row.getRound(),
                        Prompt::getSeason,
                        (a, b) -> a
                ));

        return datasetRepository.listLaps().stream()
                .filter(row -> row.getDriver() != null && row.getDriver().trim().toLowerCase(Locale.ROOT).equals(driverKey))
                .filter(row -> roundToSeason.containsKey(row.getSeason() + "-" + row.getRound()))
                .filter(row -> row.getLapTimeSeconds() > 0)
                .collect(Collectors.groupingBy(
                        row -> row.getSeason() + "-" + row.getRound(),
                        Collectors.averagingDouble(FastF1DomainData.LapRow::getLapTimeSeconds)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.comparingLong(entry -> seasonRoundOrder(entry.getKey())))
                .map(entry -> {
                    String key = entry.getKey();
                    int season = seasonFromKey(key);
                    int round = roundFromKey(key);
                    return new DriverAnalyticsResponse.LapTrendPoint(season + " R" + round, round, entry.getValue());
                })
                .toList();
    }

    private List<DriverAnalyticsResponse.StintSummary> buildDriverStintSummary(List<Prompt> scopedRows, String driverKey) {
        Map<String, Boolean> rounds = scopedRows.stream()
                .collect(Collectors.toMap(
                        row -> row.getSeason() + "-" + row.getRound(),
                        row -> true,
                        (a, b) -> a
                ));

        return datasetRepository.listStints().stream()
                .filter(row -> row.getDriver() != null && row.getDriver().trim().toLowerCase(Locale.ROOT).equals(driverKey))
                .filter(row -> rounds.containsKey(row.getSeason() + "-" + row.getRound()))
                .collect(Collectors.groupingBy(
                        row -> safeText(row.getCompound()),
                        Collectors.summingInt(FastF1DomainData.StintRow::getLaps)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.comparing(Map.Entry::getKey))
                .map(entry -> new DriverAnalyticsResponse.StintSummary(entry.getKey(), entry.getValue()))
                .toList();
    }

    private List<DriverAnalyticsResponse.PitStopStat> buildDriverPitStopStats(List<Prompt> scopedRows, String driverKey) {
        Map<String, String> raceLabel = scopedRows.stream()
                .collect(Collectors.toMap(
                        row -> row.getSeason() + "-" + row.getRound(),
                        row -> row.getSeason() + " R" + row.getRound(),
                        (a, b) -> a
                ));

        return datasetRepository.listPitStops().stream()
                .filter(row -> row.getDriver() != null && row.getDriver().trim().toLowerCase(Locale.ROOT).equals(driverKey))
                .filter(row -> raceLabel.containsKey(row.getSeason() + "-" + row.getRound()))
                .collect(Collectors.groupingBy(
                        row -> raceLabel.get(row.getSeason() + "-" + row.getRound()),
                        Collectors.toList()
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.comparingLong(entry -> seasonRoundOrder(entry.getKey())))
                .map(entry -> {
                    double avg = entry.getValue().stream()
                            .mapToDouble(FastF1DomainData.PitStopRow::getDurationSeconds)
                            .filter(value -> value > 0)
                            .average()
                            .orElse(0);
                    return new DriverAnalyticsResponse.PitStopStat(entry.getKey(), entry.getValue().size(), avg);
                })
                .toList();
    }

    private List<DriverAnalyticsResponse.WeatherPoint> buildDriverWeatherSummary(List<Prompt> scopedRows) {
        Map<String, Prompt> rounds = scopedRows.stream()
                .collect(Collectors.toMap(
                        row -> row.getSeason() + "-" + row.getRound(),
                        Function.identity(),
                        (left, right) -> left
                ));

        return datasetRepository.listWeather().stream()
                .filter(row -> rounds.containsKey(row.getSeason() + "-" + row.getRound()))
                .collect(Collectors.groupingBy(
                        row -> row.getSeason() + "-" + row.getRound(),
                        Collectors.toList()
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.comparingLong(entry -> seasonRoundOrder(entry.getKey())))
                .map(entry -> {
                    String key = entry.getKey();
                    Prompt race = rounds.get(key);
                    List<FastF1DomainData.WeatherRow> weatherRows = entry.getValue();
                    double air = weatherRows.stream().mapToDouble(FastF1DomainData.WeatherRow::getAirTemp).average().orElse(0);
                    double track = weatherRows.stream().mapToDouble(FastF1DomainData.WeatherRow::getTrackTemp).average().orElse(0);
                    long rain = weatherRows.stream().filter(FastF1DomainData.WeatherRow::isRainfall).count();
                    return new DriverAnalyticsResponse.WeatherPoint(
                            race.getSeason() + " R" + race.getRound(),
                            air,
                            track,
                            (int) rain
                    );
                })
                .toList();
    }

    private List<DriverAnalyticsResponse.TelemetrySummary> buildDriverTelemetrySummary(List<Prompt> scopedRows, String driverKey) {
        Map<String, String> races = scopedRows.stream()
                .collect(Collectors.toMap(
                        row -> row.getSeason() + "-" + row.getRound(),
                        row -> row.getSeason() + " R" + row.getRound(),
                        (a, b) -> a
                ));

        return datasetRepository.listTelemetry().stream()
                .filter(row -> row.getDriver() != null && row.getDriver().trim().toLowerCase(Locale.ROOT).equals(driverKey))
                .filter(row -> races.containsKey(row.getSeason() + "-" + row.getRound()))
                .collect(Collectors.groupingBy(
                        row -> races.get(row.getSeason() + "-" + row.getRound()),
                        Collectors.toList()
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.comparingLong(entry -> seasonRoundOrder(entry.getKey())))
                .map(entry -> {
                    List<FastF1DomainData.TelemetryRow> rows = entry.getValue();
                    double max = rows.stream().mapToDouble(FastF1DomainData.TelemetryRow::getMaxSpeed).max().orElse(0);
                    double avg = rows.stream().mapToDouble(FastF1DomainData.TelemetryRow::getAvgSpeed).average().orElse(0);
                    return new DriverAnalyticsResponse.TelemetrySummary(entry.getKey(), max, avg);
                })
                .toList();
    }

    private List<DriverAnalyticsResponse.RaceControlEntry> buildDriverRaceControlTimeline(List<Prompt> scopedRows) {
        Map<String, String> raceLookup = scopedRows.stream()
                .collect(Collectors.toMap(
                        row -> row.getSeason() + "-" + row.getRound(),
                        row -> row.getSeason() + " R" + row.getRound(),
                        (a, b) -> a
                ));

        return datasetRepository.listRaceControl().stream()
                .filter(row -> raceLookup.containsKey(row.getSeason() + "-" + row.getRound()))
                .sorted(Comparator
                        .comparingLong((FastF1DomainData.RaceControlRow row) -> seasonRoundOrder(row.getSeason(), row.getRound()))
                        .thenComparing(FastF1DomainData.RaceControlRow::getTime, Comparator.nullsLast(String::compareTo)))
                .limit(120)
                .map(row -> new DriverAnalyticsResponse.RaceControlEntry(
                        raceLookup.get(row.getSeason() + "-" + row.getRound()),
                        safeText(row.getCategory()),
                        safeText(row.getMessage()),
                        safeText(row.getTime())
                ))
                .toList();
    }

    private String safeText(String text) {
        if (text == null || text.isBlank()) {
            return "Unknown";
        }
        return text.trim();
    }

    private long seasonRoundOrder(String seasonRoundKey) {
        int season = seasonFromKey(seasonRoundKey);
        int round = roundFromKey(seasonRoundKey);
        return seasonRoundOrder(season, round);
    }

    private long seasonRoundOrder(int season, int round) {
        return ((long) season * 1000L) + round;
    }

    private int seasonFromKey(String seasonRoundKey) {
        String[] parts = seasonRoundKey.split("-", 2);
        try {
            return Integer.parseInt(parts[0]);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private int roundFromKey(String seasonRoundKey) {
        String[] parts = seasonRoundKey.split("-", 2);
        if (parts.length < 2) {
            return 0;
        }
        try {
            return Integer.parseInt(parts[1]);
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
