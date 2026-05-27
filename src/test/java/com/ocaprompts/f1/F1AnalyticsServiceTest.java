package com.ocaprompts.f1;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class F1AnalyticsServiceTest {

    private final F1AnalyticsService service = new F1AnalyticsService();

    @Test
    void shouldBuildAggregatesForSeason() {
        List<Prompt> rows = List.of(
                row(2026, 1, "Max Verstappen", "Red Bull", 1, 25),
                row(2026, 1, "Lando Norris", "McLaren", 2, 18),
                row(2026, 1, "Charles Leclerc", "Ferrari", 3, 15),
                row(2026, 2, "Lando Norris", "McLaren", 1, 25),
                row(2026, 2, "Max Verstappen", "Red Bull", 2, 18),
                row(2026, 2, "George Russell", "Mercedes", 3, 15),
                row(2025, 1, "Legacy Driver", "Legacy Team", 1, 25)
        );

        F1AnalyticsResponse analytics = service.buildSeasonAnalytics(2026, rows);

        assertEquals(2026, analytics.getSeason());
        assertEquals(4, analytics.getTeamPoints().size());
        assertEquals(2, analytics.getRaceWinners().size());
        assertEquals(2, analytics.getPointsByRound().size());
        assertEquals(4, analytics.getPodiumConsistency().size());

        assertEquals(58.0, analytics.getPointsByRound().get(0).getTotalPoints());
        assertEquals(58.0, analytics.getPointsByRound().get(1).getTotalPoints());

        assertEquals("Lando Norris", analytics.getRaceWinners().get(0).getDriver());
        assertEquals(1, analytics.getRaceWinners().get(0).getWins());

        assertEquals("Lando Norris", analytics.getDriverTop10().get(0).getName());
        assertEquals(43.0, analytics.getDriverTop10().get(0).getPoints());
        assertTrue(analytics.getDriverTop10().stream().noneMatch(row -> "Legacy Driver".equals(row.getName())));
    }

    @Test
    void shouldReturnEmptySeriesForSeasonWithoutRows() {
        F1AnalyticsResponse analytics = service.buildSeasonAnalytics(2030, List.of(row(2026, 1, "A", "B", 1, 25)));

        assertEquals(2030, analytics.getSeason());
        assertTrue(analytics.getDriverTop10().isEmpty());
        assertTrue(analytics.getConstructorTop10().isEmpty());
        assertTrue(analytics.getTeamPoints().isEmpty());
        assertTrue(analytics.getRaceWinners().isEmpty());
        assertTrue(analytics.getPointsByRound().isEmpty());
        assertTrue(analytics.getPodiumConsistency().isEmpty());
    }

    @Test
    void shouldRejectInvalidSeason() {
        assertThrows(IllegalArgumentException.class, () -> service.buildSeasonAnalytics(1949, List.of()));
        assertThrows(IllegalArgumentException.class, () -> service.buildSeasonAnalytics(2009, List.of()));
        assertThrows(IllegalArgumentException.class, () -> service.buildSeasonAnalytics(2101, List.of()));
    }

    @Test
    void shouldListUniqueDriversAlphabeticallyIgnoringCase() {
        List<String> drivers = service.listDrivers(List.of(
                row(2024, 1, "  Max Verstappen  ", "Red Bull", 1, 25),
                row(2024, 2, "lando norris", "McLaren", 2, 18),
                row(2024, 3, "Lando Norris", "McLaren", 1, 25),
                row(2024, 3, "Charles Leclerc", "Ferrari", 3, 15),
                row(2024, 4, " ", "Ferrari", 4, 12)
        ));

        assertEquals(List.of("Charles Leclerc", "lando norris", "Max Verstappen"), drivers);
    }

    @Test
    void shouldListUniqueSeasonsInDescendingOrder() {
        List<Integer> seasons = service.listSeasons(List.of(
                row(2024, 1, "A", "T1", 1, 25),
                row(2026, 1, "B", "T2", 2, 18),
                row(2025, 1, "C", "T3", 3, 15),
                row(2026, 2, "D", "T4", 4, 12),
                row(2009, 1, "F", "T6", 6, 8),
                row(1940, 1, "E", "T5", 5, 10)
        ));

        assertEquals(List.of(2026, 2025, 2024), seasons);
    }

    @Test
    void shouldBuildDriverAnalyticsAcrossSeasons() {
        List<Prompt> rows = List.of(
                row(2024, 2, "Max Verstappen", "Red Bull", 2, 18),
                row(2024, 1, "Max Verstappen", "Red Bull", 1, 25),
                row(2025, 1, "MAX VERSTAPPEN", "Red Bull", 3, 15),
                row(2025, 2, "Max Verstappen", "Oracle Red Bull", 4, 12),
                row(2023, 1, "Lando Norris", "McLaren", 1, 25)
        );

        DriverAnalyticsResponse analytics = service.buildDriverAnalytics(" max verstappen ", rows);

        assertEquals("Max Verstappen", analytics.getDriver());
        assertEquals(70.0, analytics.getTotalPoints());
        assertEquals(1, analytics.getWins());
        assertEquals(3, analytics.getPodiums());
        assertEquals(4, analytics.getRaces());
        assertEquals(2.5, analytics.getAvgFinish());

        assertEquals(4, analytics.getPointsByRace().size());
        assertEquals(2024, analytics.getPointsByRace().get(0).getSeason());
        assertEquals(1, analytics.getPointsByRace().get(0).getRound());
        assertEquals(2025, analytics.getPointsByRace().get(3).getSeason());
        assertEquals(2, analytics.getPointsByRace().get(3).getRound());

        assertEquals(4, analytics.getFinishByRace().size());
        assertEquals(1, analytics.getFinishByRace().get(0).getPosition());
        assertEquals(4, analytics.getFinishByRace().get(3).getPosition());

        assertEquals(2, analytics.getTeamPoints().size());
        assertEquals("Red Bull", analytics.getTeamPoints().get(0).getTeam());
        assertEquals(58.0, analytics.getTeamPoints().get(0).getPoints());
        assertEquals("Oracle Red Bull", analytics.getTeamPoints().get(1).getTeam());
        assertEquals(12.0, analytics.getTeamPoints().get(1).getPoints());

        assertEquals(2, analytics.getPointsBySeason().size());
        assertEquals(2024, analytics.getPointsBySeason().get(0).getSeason());
        assertEquals(43.0, analytics.getPointsBySeason().get(0).getPoints());
        assertEquals(2025, analytics.getPointsBySeason().get(1).getSeason());
        assertEquals(27.0, analytics.getPointsBySeason().get(1).getPoints());

        assertEquals(3, analytics.getPodiumWinBreakdown().size());
        assertEquals("Wins", analytics.getPodiumWinBreakdown().get(0).getBucket());
        assertEquals(1, analytics.getPodiumWinBreakdown().get(0).getCount());
        assertEquals("Podiums (2-3)", analytics.getPodiumWinBreakdown().get(1).getBucket());
        assertEquals(2, analytics.getPodiumWinBreakdown().get(1).getCount());
        assertEquals("Other Finishes", analytics.getPodiumWinBreakdown().get(2).getBucket());
        assertEquals(1, analytics.getPodiumWinBreakdown().get(2).getCount());

        assertEquals(4, analytics.getRaceResults().size());
        assertEquals(2025, analytics.getRaceResults().get(0).getSeason());
        assertEquals(2, analytics.getRaceResults().get(0).getRound());
        assertEquals(2024, analytics.getRaceResults().get(3).getSeason());
        assertEquals(1, analytics.getRaceResults().get(3).getRound());
    }

    @Test
    void shouldBuildDriverAnalyticsForSpecificSeasonOnly() {
        List<Prompt> rows = List.of(
                row(2024, 2, "Max Verstappen", "Red Bull", 2, 18),
                row(2024, 1, "Max Verstappen", "Red Bull", 1, 25),
                row(2025, 1, "Max Verstappen", "Red Bull", 3, 15),
                row(2025, 2, "Max Verstappen", "Oracle Red Bull", 4, 12),
                row(2025, 1, "Lando Norris", "McLaren", 1, 25)
        );

        DriverAnalyticsResponse analytics = service.buildDriverAnalytics("Max Verstappen", 2025, rows);

        assertEquals("Max Verstappen", analytics.getDriver());
        assertEquals(27.0, analytics.getTotalPoints());
        assertEquals(0, analytics.getWins());
        assertEquals(1, analytics.getPodiums());
        assertEquals(2, analytics.getRaces());
        assertEquals(3.5, analytics.getAvgFinish());

        assertEquals(2, analytics.getPointsByRace().size());
        assertTrue(analytics.getPointsByRace().stream().allMatch(point -> point.getSeason() == 2025));
        assertEquals(1, analytics.getPointsBySeason().size());
        assertEquals(2025, analytics.getPointsBySeason().get(0).getSeason());
        assertEquals(27.0, analytics.getPointsBySeason().get(0).getPoints());
        assertEquals(2, analytics.getRaceResults().size());
        assertTrue(analytics.getRaceResults().stream().allMatch(result -> result.getSeason() == 2025));
    }

    @Test
    void shouldReturnEmptyDriverAnalyticsForUnknownDriver() {
        DriverAnalyticsResponse analytics = service.buildDriverAnalytics(
                "Unknown Driver",
                List.of(row(2024, 1, "Max Verstappen", "Red Bull", 1, 25))
        );

        assertEquals("Unknown Driver", analytics.getDriver());
        assertEquals(0.0, analytics.getTotalPoints());
        assertEquals(0, analytics.getWins());
        assertEquals(0, analytics.getPodiums());
        assertEquals(0, analytics.getRaces());
        assertEquals(0.0, analytics.getAvgFinish());
        assertTrue(analytics.getPointsByRace().isEmpty());
        assertTrue(analytics.getFinishByRace().isEmpty());
        assertTrue(analytics.getTeamPoints().isEmpty());
        assertTrue(analytics.getPointsBySeason().isEmpty());
        assertTrue(analytics.getRaceResults().isEmpty());
        assertEquals(3, analytics.getPodiumWinBreakdown().size());
        assertTrue(analytics.getPodiumWinBreakdown().stream().allMatch(row -> row.getCount() == 0));
    }

    @Test
    void shouldRejectMissingDriverName() {
        assertThrows(IllegalArgumentException.class, () -> service.buildDriverAnalytics(null, List.of()));
        assertThrows(IllegalArgumentException.class, () -> service.buildDriverAnalytics("   ", List.of()));
    }

    @Test
    void shouldRejectInvalidSeasonForDriverAnalytics() {
        assertThrows(IllegalArgumentException.class,
                () -> service.buildDriverAnalytics("Max Verstappen", 1949, List.of()));
        assertThrows(IllegalArgumentException.class,
                () -> service.buildDriverAnalytics("Max Verstappen", 2009, List.of()));
        assertThrows(IllegalArgumentException.class,
                () -> service.buildDriverAnalytics("Max Verstappen", 2101, List.of()));
    }

    private Prompt row(int season, int round, String driver, String team, int position, double points) {
        Prompt row = new Prompt();
        row.setSeason(season);
        row.setRound(round);
        row.setRaceName("Race " + round);
        row.setDriver(driver);
        row.setTeam(team);
        row.setPosition(position);
        row.setPoints(points);
        return row;
    }
}
