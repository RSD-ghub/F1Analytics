package com.ocaprompts.f1;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class StandingsSupport {
    private StandingsSupport() {
    }

    static List<StandingsEntry> aggregateByDriver(List<Prompt> rows) {
        return aggregate(rows, true);
    }

    static List<StandingsEntry> aggregateByTeam(List<Prompt> rows) {
        return aggregate(rows, false);
    }

    private static List<StandingsEntry> aggregate(List<Prompt> rows, boolean byDriver) {
        Map<String, StandingsEntry> totals = new LinkedHashMap<>();

        for (Prompt row : rows) {
            String key = byDriver ? row.getDriver() : row.getTeam();
            if (key == null || key.isBlank()) {
                continue;
            }

            StandingsEntry entry = totals.computeIfAbsent(key.trim(), StandingsEntry::new);
            entry.setPoints(entry.getPoints() + row.getPoints());
            if (row.getPosition() == 1) {
                entry.setWins(entry.getWins() + 1);
            }
        }

        List<StandingsEntry> standings = new ArrayList<>(totals.values());
        standings.sort(Comparator
                .comparingDouble(StandingsEntry::getPoints).reversed()
                .thenComparing(Comparator.comparingInt(StandingsEntry::getWins).reversed())
                .thenComparing(StandingsEntry::getName));
        return standings;
    }
}
