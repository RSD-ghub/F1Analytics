package com.ocaprompts.f1;

import jakarta.enterprise.context.ApplicationScoped;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Function;
import java.util.stream.Collectors;

@ApplicationScoped
public class F1AnalyticsService {
    private static final int FASTF1_SEASON_FLOOR = 2010;
    private static final int MIN_SEASON = FASTF1_SEASON_FLOOR;
    private static final int MAX_SEASON = 2100;

    public F1AnalyticsResponse buildSeasonAnalytics(int season, List<Prompt> allRows) {
        validateSeason(season);

        List<Prompt> seasonRows = allRows.stream()
                .filter(row -> row.getSeason() == season)
                .collect(Collectors.toList());

        F1AnalyticsResponse response = new F1AnalyticsResponse();
        response.setSeason(season);
        response.setDriverTop10(toRankedPoints(seasonRows, Prompt::getDriver, 10));
        response.setConstructorTop10(toRankedPoints(seasonRows, Prompt::getTeam, 10));
        response.setTeamPoints(toTeamPoints(seasonRows));
        response.setRaceWinners(toRaceWinners(seasonRows));
        response.setPointsByRound(toPointsByRound(seasonRows));
        response.setPodiumConsistency(toPodiumConsistency(seasonRows));
        return response;
    }

    public List<String> listDrivers(List<Prompt> allRows) {
        Set<String> names = new TreeSet<>(String.CASE_INSENSITIVE_ORDER);
        for (Prompt row : allRows) {
            if (row.getDriver() != null && !row.getDriver().isBlank()) {
                names.add(row.getDriver().trim());
            }
        }
        return new ArrayList<>(names);
    }

    public List<Integer> listSeasons(List<Prompt> allRows) {
        return allRows.stream()
                .map(Prompt::getSeason)
                .filter(season -> season >= FASTF1_SEASON_FLOOR && season <= MAX_SEASON)
                .distinct()
                .sorted(Comparator.reverseOrder())
                .collect(Collectors.toList());
    }

    public DriverAnalyticsResponse buildDriverAnalytics(String driverName, List<Prompt> allRows) {
        return buildDriverAnalytics(driverName, null, allRows);
    }

    public DriverAnalyticsResponse buildDriverAnalytics(String driverName, Integer season, List<Prompt> allRows) {
        String requested = validateDriverName(driverName);
        String requestedKey = requested.toLowerCase(Locale.ROOT);
        if (season != null) {
            validateSeason(season);
        }

        List<Prompt> driverRows = allRows.stream()
                .filter(row -> row.getDriver() != null && !row.getDriver().isBlank())
                .filter(row -> row.getDriver().trim().toLowerCase(Locale.ROOT).equals(requestedKey))
                .filter(row -> season == null || row.getSeason() == season)
                .collect(Collectors.toList());

        DriverAnalyticsResponse response = new DriverAnalyticsResponse();
        response.setDriver(driverRows.isEmpty() ? requested : driverRows.get(0).getDriver().trim());
        response.setRaces(driverRows.size());

        if (driverRows.isEmpty()) {
            response.setPodiumWinBreakdown(List.of(
                    new DriverAnalyticsResponse.BucketCount("Wins", 0),
                    new DriverAnalyticsResponse.BucketCount("Podiums (2-3)", 0),
                    new DriverAnalyticsResponse.BucketCount("Other Finishes", 0)
            ));
            return response;
        }

        response.setTotalPoints(driverRows.stream().mapToDouble(Prompt::getPoints).sum());
        response.setWins((int) driverRows.stream().filter(row -> row.getPosition() == 1).count());
        response.setPodiums((int) driverRows.stream().filter(row -> row.getPosition() > 0 && row.getPosition() <= 3).count());
        response.setAvgFinish(driverRows.stream()
                .filter(row -> row.getPosition() > 0)
                .mapToInt(Prompt::getPosition)
                .average()
                .orElse(0.0));

        List<Prompt> chronologicalRows = driverRows.stream()
                .sorted(Comparator.comparingInt(Prompt::getSeason).thenComparingInt(Prompt::getRound))
                .collect(Collectors.toList());

        response.setPointsByRace(chronologicalRows.stream()
                .map(row -> new DriverAnalyticsResponse.RacePoints(
                        raceLabel(row),
                        row.getSeason(),
                        row.getRound(),
                        row.getPoints()
                ))
                .collect(Collectors.toList()));

        response.setFinishByRace(chronologicalRows.stream()
                .map(row -> new DriverAnalyticsResponse.FinishPoint(
                        raceLabel(row),
                        row.getSeason(),
                        row.getRound(),
                        row.getPosition()
                ))
                .collect(Collectors.toList()));

        response.setTeamPoints(driverRows.stream()
                .filter(row -> row.getTeam() != null && !row.getTeam().isBlank())
                .collect(Collectors.groupingBy(
                        row -> row.getTeam().trim(),
                        Collectors.summingDouble(Prompt::getPoints)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.<Map.Entry<String, Double>>comparingDouble(Map.Entry::getValue).reversed()
                        .thenComparing(Map.Entry::getKey))
                .map(entry -> new DriverAnalyticsResponse.TeamPoints(entry.getKey(), entry.getValue()))
                .collect(Collectors.toList()));

        response.setPointsBySeason(driverRows.stream()
                .collect(Collectors.groupingBy(
                        Prompt::getSeason,
                        Collectors.summingDouble(Prompt::getPoints)
                ))
                .entrySet()
                .stream()
                .sorted(Map.Entry.comparingByKey())
                .map(entry -> new DriverAnalyticsResponse.SeasonPoints(entry.getKey(), entry.getValue()))
                .collect(Collectors.toList()));

        int wins = response.getWins();
        int podium23 = (int) driverRows.stream().filter(row -> row.getPosition() == 2 || row.getPosition() == 3).count();
        int otherFinishes = Math.max(0, response.getRaces() - wins - podium23);
        response.setPodiumWinBreakdown(List.of(
                new DriverAnalyticsResponse.BucketCount("Wins", wins),
                new DriverAnalyticsResponse.BucketCount("Podiums (2-3)", podium23),
                new DriverAnalyticsResponse.BucketCount("Other Finishes", otherFinishes)
        ));

        response.setRaceResults(driverRows.stream()
                .sorted(Comparator
                        .comparingInt(Prompt::getSeason).reversed()
                        .thenComparing(Comparator.comparingInt(Prompt::getRound).reversed())
                        .thenComparingInt(Prompt::getPosition))
                .collect(Collectors.toList()));

        return response;
    }

    private void validateSeason(int season) {
        if (season < MIN_SEASON || season > MAX_SEASON) {
            throw new IllegalArgumentException("Season must be between " + MIN_SEASON + " and " + MAX_SEASON);
        }
    }

    private String validateDriverName(String driverName) {
        if (driverName == null || driverName.isBlank()) {
            throw new IllegalArgumentException("Driver name is required");
        }
        return driverName.trim();
    }

    private String raceLabel(Prompt row) {
        String race = row.getRaceName() == null || row.getRaceName().isBlank()
                ? "Race"
                : row.getRaceName().trim();
        return row.getSeason() + " R" + row.getRound() + " - " + race;
    }

    private List<F1AnalyticsResponse.RankedPoints> toRankedPoints(List<Prompt> seasonRows, Function<Prompt, String> keyFn, int limit) {
        List<Map.Entry<String, Double>> sorted = seasonRows.stream()
                .filter(row -> keyFn.apply(row) != null && !keyFn.apply(row).isBlank())
                .collect(Collectors.groupingBy(
                        row -> keyFn.apply(row).trim(),
                        Collectors.summingDouble(Prompt::getPoints)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.<Map.Entry<String, Double>>comparingDouble(Map.Entry::getValue).reversed()
                        .thenComparing(Map.Entry::getKey))
                .limit(limit)
                .collect(Collectors.toList());

        List<F1AnalyticsResponse.RankedPoints> ranked = new ArrayList<>();
        for (int i = 0; i < sorted.size(); i++) {
            Map.Entry<String, Double> row = sorted.get(i);
            ranked.add(new F1AnalyticsResponse.RankedPoints(row.getKey(), row.getValue(), i + 1));
        }
        return ranked;
    }

    private List<F1AnalyticsResponse.TeamPoints> toTeamPoints(List<Prompt> seasonRows) {
        return seasonRows.stream()
                .filter(row -> row.getTeam() != null && !row.getTeam().isBlank())
                .collect(Collectors.groupingBy(
                        row -> row.getTeam().trim(),
                        Collectors.summingDouble(Prompt::getPoints)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.<Map.Entry<String, Double>>comparingDouble(Map.Entry::getValue).reversed()
                        .thenComparing(Map.Entry::getKey))
                .map(row -> new F1AnalyticsResponse.TeamPoints(row.getKey(), row.getValue()))
                .collect(Collectors.toList());
    }

    private List<F1AnalyticsResponse.RaceWinner> toRaceWinners(List<Prompt> seasonRows) {
        return seasonRows.stream()
                .filter(row -> row.getPosition() == 1)
                .filter(row -> row.getDriver() != null && !row.getDriver().isBlank())
                .collect(Collectors.groupingBy(
                        row -> row.getDriver().trim(),
                        Collectors.summingInt(row -> 1)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.<Map.Entry<String, Integer>>comparingInt(Map.Entry::getValue).reversed()
                        .thenComparing(Map.Entry::getKey))
                .map(row -> new F1AnalyticsResponse.RaceWinner(row.getKey(), row.getValue()))
                .collect(Collectors.toList());
    }

    private List<F1AnalyticsResponse.RoundPoints> toPointsByRound(List<Prompt> seasonRows) {
        Map<Integer, Double> pointsByRound = seasonRows.stream()
                .collect(Collectors.groupingBy(
                        Prompt::getRound,
                        LinkedHashMap::new,
                        Collectors.summingDouble(Prompt::getPoints)
                ));

        return pointsByRound.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .map(row -> new F1AnalyticsResponse.RoundPoints(row.getKey(), row.getValue()))
                .collect(Collectors.toList());
    }

    private List<F1AnalyticsResponse.PodiumConsistency> toPodiumConsistency(List<Prompt> seasonRows) {
        return seasonRows.stream()
                .filter(row -> row.getPosition() > 0 && row.getPosition() <= 3)
                .filter(row -> row.getDriver() != null && !row.getDriver().isBlank())
                .collect(Collectors.groupingBy(
                        row -> row.getDriver().trim(),
                        Collectors.summingInt(row -> 1)
                ))
                .entrySet()
                .stream()
                .sorted(Comparator.<Map.Entry<String, Integer>>comparingInt(Map.Entry::getValue).reversed()
                        .thenComparing(Map.Entry::getKey))
                .limit(10)
                .map(row -> new F1AnalyticsResponse.PodiumConsistency(row.getKey(), row.getValue()))
                .collect(Collectors.toList());
    }
}
