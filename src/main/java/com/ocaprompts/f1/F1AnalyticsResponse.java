package com.ocaprompts.f1;

import java.util.ArrayList;
import java.util.List;

public class F1AnalyticsResponse {
    private int season;
    private List<RankedPoints> driverTop10 = new ArrayList<>();
    private List<RankedPoints> constructorTop10 = new ArrayList<>();
    private List<TeamPoints> teamPoints = new ArrayList<>();
    private List<RaceWinner> raceWinners = new ArrayList<>();
    private List<RoundPoints> pointsByRound = new ArrayList<>();
    private List<PodiumConsistency> podiumConsistency = new ArrayList<>();
    private List<LapTrendPoint> lapTrend = new ArrayList<>();
    private List<StintSummary> stintSummary = new ArrayList<>();
    private List<PitStopStat> pitStopStats = new ArrayList<>();
    private List<WeatherPoint> weatherSummary = new ArrayList<>();
    private List<TelemetrySummary> telemetrySummary = new ArrayList<>();
    private List<RaceControlEntry> raceControlTimeline = new ArrayList<>();

    public int getSeason() {
        return season;
    }

    public void setSeason(int season) {
        this.season = season;
    }

    public List<RankedPoints> getDriverTop10() {
        return driverTop10;
    }

    public void setDriverTop10(List<RankedPoints> driverTop10) {
        this.driverTop10 = driverTop10;
    }

    public List<RankedPoints> getConstructorTop10() {
        return constructorTop10;
    }

    public void setConstructorTop10(List<RankedPoints> constructorTop10) {
        this.constructorTop10 = constructorTop10;
    }

    public List<TeamPoints> getTeamPoints() {
        return teamPoints;
    }

    public void setTeamPoints(List<TeamPoints> teamPoints) {
        this.teamPoints = teamPoints;
    }

    public List<RaceWinner> getRaceWinners() {
        return raceWinners;
    }

    public void setRaceWinners(List<RaceWinner> raceWinners) {
        this.raceWinners = raceWinners;
    }

    public List<RoundPoints> getPointsByRound() {
        return pointsByRound;
    }

    public void setPointsByRound(List<RoundPoints> pointsByRound) {
        this.pointsByRound = pointsByRound;
    }

    public List<PodiumConsistency> getPodiumConsistency() {
        return podiumConsistency;
    }

    public void setPodiumConsistency(List<PodiumConsistency> podiumConsistency) {
        this.podiumConsistency = podiumConsistency;
    }

    public List<LapTrendPoint> getLapTrend() {
        return lapTrend;
    }

    public void setLapTrend(List<LapTrendPoint> lapTrend) {
        this.lapTrend = lapTrend;
    }

    public List<StintSummary> getStintSummary() {
        return stintSummary;
    }

    public void setStintSummary(List<StintSummary> stintSummary) {
        this.stintSummary = stintSummary;
    }

    public List<PitStopStat> getPitStopStats() {
        return pitStopStats;
    }

    public void setPitStopStats(List<PitStopStat> pitStopStats) {
        this.pitStopStats = pitStopStats;
    }

    public List<WeatherPoint> getWeatherSummary() {
        return weatherSummary;
    }

    public void setWeatherSummary(List<WeatherPoint> weatherSummary) {
        this.weatherSummary = weatherSummary;
    }

    public List<TelemetrySummary> getTelemetrySummary() {
        return telemetrySummary;
    }

    public void setTelemetrySummary(List<TelemetrySummary> telemetrySummary) {
        this.telemetrySummary = telemetrySummary;
    }

    public List<RaceControlEntry> getRaceControlTimeline() {
        return raceControlTimeline;
    }

    public void setRaceControlTimeline(List<RaceControlEntry> raceControlTimeline) {
        this.raceControlTimeline = raceControlTimeline;
    }

    public static class RankedPoints {
        private String name;
        private double points;
        private int rank;

        public RankedPoints() {
        }

        public RankedPoints(String name, double points, int rank) {
            this.name = name;
            this.points = points;
            this.rank = rank;
        }

        public String getName() {
            return name;
        }

        public void setName(String name) {
            this.name = name;
        }

        public double getPoints() {
            return points;
        }

        public void setPoints(double points) {
            this.points = points;
        }

        public int getRank() {
            return rank;
        }

        public void setRank(int rank) {
            this.rank = rank;
        }
    }

    public static class TeamPoints {
        private String team;
        private double points;

        public TeamPoints() {
        }

        public TeamPoints(String team, double points) {
            this.team = team;
            this.points = points;
        }

        public String getTeam() {
            return team;
        }

        public void setTeam(String team) {
            this.team = team;
        }

        public double getPoints() {
            return points;
        }

        public void setPoints(double points) {
            this.points = points;
        }
    }

    public static class RaceWinner {
        private String driver;
        private int wins;

        public RaceWinner() {
        }

        public RaceWinner(String driver, int wins) {
            this.driver = driver;
            this.wins = wins;
        }

        public String getDriver() {
            return driver;
        }

        public void setDriver(String driver) {
            this.driver = driver;
        }

        public int getWins() {
            return wins;
        }

        public void setWins(int wins) {
            this.wins = wins;
        }
    }

    public static class RoundPoints {
        private int round;
        private double totalPoints;

        public RoundPoints() {
        }

        public RoundPoints(int round, double totalPoints) {
            this.round = round;
            this.totalPoints = totalPoints;
        }

        public int getRound() {
            return round;
        }

        public void setRound(int round) {
            this.round = round;
        }

        public double getTotalPoints() {
            return totalPoints;
        }

        public void setTotalPoints(double totalPoints) {
            this.totalPoints = totalPoints;
        }
    }

    public static class PodiumConsistency {
        private String driver;
        private int podiumFinishes;

        public PodiumConsistency() {
        }

        public PodiumConsistency(String driver, int podiumFinishes) {
            this.driver = driver;
            this.podiumFinishes = podiumFinishes;
        }

        public String getDriver() {
            return driver;
        }

        public void setDriver(String driver) {
            this.driver = driver;
        }

        public int getPodiumFinishes() {
            return podiumFinishes;
        }

        public void setPodiumFinishes(int podiumFinishes) {
            this.podiumFinishes = podiumFinishes;
        }
    }

    public static class LapTrendPoint {
        private String label;
        private int round;
        private double avgLapTimeSeconds;

        public LapTrendPoint() {
        }

        public LapTrendPoint(String label, int round, double avgLapTimeSeconds) {
            this.label = label;
            this.round = round;
            this.avgLapTimeSeconds = avgLapTimeSeconds;
        }

        public String getLabel() {
            return label;
        }

        public void setLabel(String label) {
            this.label = label;
        }

        public int getRound() {
            return round;
        }

        public void setRound(int round) {
            this.round = round;
        }

        public double getAvgLapTimeSeconds() {
            return avgLapTimeSeconds;
        }

        public void setAvgLapTimeSeconds(double avgLapTimeSeconds) {
            this.avgLapTimeSeconds = avgLapTimeSeconds;
        }
    }

    public static class StintSummary {
        private String compound;
        private int laps;

        public StintSummary() {
        }

        public StintSummary(String compound, int laps) {
            this.compound = compound;
            this.laps = laps;
        }

        public String getCompound() {
            return compound;
        }

        public void setCompound(String compound) {
            this.compound = compound;
        }

        public int getLaps() {
            return laps;
        }

        public void setLaps(int laps) {
            this.laps = laps;
        }
    }

    public static class PitStopStat {
        private String entity;
        private int stops;
        private double avgDurationSeconds;

        public PitStopStat() {
        }

        public PitStopStat(String entity, int stops, double avgDurationSeconds) {
            this.entity = entity;
            this.stops = stops;
            this.avgDurationSeconds = avgDurationSeconds;
        }

        public String getEntity() {
            return entity;
        }

        public void setEntity(String entity) {
            this.entity = entity;
        }

        public int getStops() {
            return stops;
        }

        public void setStops(int stops) {
            this.stops = stops;
        }

        public double getAvgDurationSeconds() {
            return avgDurationSeconds;
        }

        public void setAvgDurationSeconds(double avgDurationSeconds) {
            this.avgDurationSeconds = avgDurationSeconds;
        }
    }

    public static class WeatherPoint {
        private String label;
        private int round;
        private double avgAirTemp;
        private double avgTrackTemp;
        private int rainSamples;

        public WeatherPoint() {
        }

        public WeatherPoint(String label, int round, double avgAirTemp, double avgTrackTemp, int rainSamples) {
            this.label = label;
            this.round = round;
            this.avgAirTemp = avgAirTemp;
            this.avgTrackTemp = avgTrackTemp;
            this.rainSamples = rainSamples;
        }

        public String getLabel() {
            return label;
        }

        public void setLabel(String label) {
            this.label = label;
        }

        public int getRound() {
            return round;
        }

        public void setRound(int round) {
            this.round = round;
        }

        public double getAvgAirTemp() {
            return avgAirTemp;
        }

        public void setAvgAirTemp(double avgAirTemp) {
            this.avgAirTemp = avgAirTemp;
        }

        public double getAvgTrackTemp() {
            return avgTrackTemp;
        }

        public void setAvgTrackTemp(double avgTrackTemp) {
            this.avgTrackTemp = avgTrackTemp;
        }

        public int getRainSamples() {
            return rainSamples;
        }

        public void setRainSamples(int rainSamples) {
            this.rainSamples = rainSamples;
        }
    }

    public static class TelemetrySummary {
        private String entity;
        private double maxSpeed;
        private double avgSpeed;

        public TelemetrySummary() {
        }

        public TelemetrySummary(String entity, double maxSpeed, double avgSpeed) {
            this.entity = entity;
            this.maxSpeed = maxSpeed;
            this.avgSpeed = avgSpeed;
        }

        public String getEntity() {
            return entity;
        }

        public void setEntity(String entity) {
            this.entity = entity;
        }

        public double getMaxSpeed() {
            return maxSpeed;
        }

        public void setMaxSpeed(double maxSpeed) {
            this.maxSpeed = maxSpeed;
        }

        public double getAvgSpeed() {
            return avgSpeed;
        }

        public void setAvgSpeed(double avgSpeed) {
            this.avgSpeed = avgSpeed;
        }
    }

    public static class RaceControlEntry {
        private String label;
        private String category;
        private String message;
        private String time;

        public RaceControlEntry() {
        }

        public RaceControlEntry(String label, String category, String message, String time) {
            this.label = label;
            this.category = category;
            this.message = message;
            this.time = time;
        }

        public String getLabel() {
            return label;
        }

        public void setLabel(String label) {
            this.label = label;
        }

        public String getCategory() {
            return category;
        }

        public void setCategory(String category) {
            this.category = category;
        }

        public String getMessage() {
            return message;
        }

        public void setMessage(String message) {
            this.message = message;
        }

        public String getTime() {
            return time;
        }

        public void setTime(String time) {
            this.time = time;
        }
    }
}
