package com.ocaprompts.f1;

import java.util.ArrayList;
import java.util.List;

public class DriverAnalyticsResponse {
    private String driver;
    private double totalPoints;
    private int wins;
    private int podiums;
    private int races;
    private double avgFinish;
    private List<RacePoints> pointsByRace = new ArrayList<>();
    private List<FinishPoint> finishByRace = new ArrayList<>();
    private List<TeamPoints> teamPoints = new ArrayList<>();
    private List<SeasonPoints> pointsBySeason = new ArrayList<>();
    private List<BucketCount> podiumWinBreakdown = new ArrayList<>();
    private List<LapTrendPoint> lapTrend = new ArrayList<>();
    private List<StintSummary> stintSummary = new ArrayList<>();
    private List<PitStopStat> pitStopStats = new ArrayList<>();
    private List<WeatherPoint> weatherSummary = new ArrayList<>();
    private List<TelemetrySummary> telemetrySummary = new ArrayList<>();
    private List<RaceControlEntry> raceControlTimeline = new ArrayList<>();
    private List<Prompt> raceResults = new ArrayList<>();

    public String getDriver() {
        return driver;
    }

    public void setDriver(String driver) {
        this.driver = driver;
    }

    public double getTotalPoints() {
        return totalPoints;
    }

    public void setTotalPoints(double totalPoints) {
        this.totalPoints = totalPoints;
    }

    public int getWins() {
        return wins;
    }

    public void setWins(int wins) {
        this.wins = wins;
    }

    public int getPodiums() {
        return podiums;
    }

    public void setPodiums(int podiums) {
        this.podiums = podiums;
    }

    public int getRaces() {
        return races;
    }

    public void setRaces(int races) {
        this.races = races;
    }

    public double getAvgFinish() {
        return avgFinish;
    }

    public void setAvgFinish(double avgFinish) {
        this.avgFinish = avgFinish;
    }

    public List<RacePoints> getPointsByRace() {
        return pointsByRace;
    }

    public void setPointsByRace(List<RacePoints> pointsByRace) {
        this.pointsByRace = pointsByRace;
    }

    public List<FinishPoint> getFinishByRace() {
        return finishByRace;
    }

    public void setFinishByRace(List<FinishPoint> finishByRace) {
        this.finishByRace = finishByRace;
    }

    public List<TeamPoints> getTeamPoints() {
        return teamPoints;
    }

    public void setTeamPoints(List<TeamPoints> teamPoints) {
        this.teamPoints = teamPoints;
    }

    public List<SeasonPoints> getPointsBySeason() {
        return pointsBySeason;
    }

    public void setPointsBySeason(List<SeasonPoints> pointsBySeason) {
        this.pointsBySeason = pointsBySeason;
    }

    public List<BucketCount> getPodiumWinBreakdown() {
        return podiumWinBreakdown;
    }

    public void setPodiumWinBreakdown(List<BucketCount> podiumWinBreakdown) {
        this.podiumWinBreakdown = podiumWinBreakdown;
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

    public List<Prompt> getRaceResults() {
        return raceResults;
    }

    public void setRaceResults(List<Prompt> raceResults) {
        this.raceResults = raceResults;
    }

    public static class RacePoints {
        private String label;
        private int season;
        private int round;
        private double points;

        public RacePoints() {
        }

        public RacePoints(String label, int season, int round, double points) {
            this.label = label;
            this.season = season;
            this.round = round;
            this.points = points;
        }

        public String getLabel() {
            return label;
        }

        public void setLabel(String label) {
            this.label = label;
        }

        public int getSeason() {
            return season;
        }

        public void setSeason(int season) {
            this.season = season;
        }

        public int getRound() {
            return round;
        }

        public void setRound(int round) {
            this.round = round;
        }

        public double getPoints() {
            return points;
        }

        public void setPoints(double points) {
            this.points = points;
        }
    }

    public static class FinishPoint {
        private String label;
        private int season;
        private int round;
        private int position;

        public FinishPoint() {
        }

        public FinishPoint(String label, int season, int round, int position) {
            this.label = label;
            this.season = season;
            this.round = round;
            this.position = position;
        }

        public String getLabel() {
            return label;
        }

        public void setLabel(String label) {
            this.label = label;
        }

        public int getSeason() {
            return season;
        }

        public void setSeason(int season) {
            this.season = season;
        }

        public int getRound() {
            return round;
        }

        public void setRound(int round) {
            this.round = round;
        }

        public int getPosition() {
            return position;
        }

        public void setPosition(int position) {
            this.position = position;
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

    public static class SeasonPoints {
        private int season;
        private double points;

        public SeasonPoints() {
        }

        public SeasonPoints(int season, double points) {
            this.season = season;
            this.points = points;
        }

        public int getSeason() {
            return season;
        }

        public void setSeason(int season) {
            this.season = season;
        }

        public double getPoints() {
            return points;
        }

        public void setPoints(double points) {
            this.points = points;
        }
    }

    public static class BucketCount {
        private String bucket;
        private int count;

        public BucketCount() {
        }

        public BucketCount(String bucket, int count) {
            this.bucket = bucket;
            this.count = count;
        }

        public String getBucket() {
            return bucket;
        }

        public void setBucket(String bucket) {
            this.bucket = bucket;
        }

        public int getCount() {
            return count;
        }

        public void setCount(int count) {
            this.count = count;
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
        private String label;
        private int stops;
        private double avgDurationSeconds;

        public PitStopStat() {
        }

        public PitStopStat(String label, int stops, double avgDurationSeconds) {
            this.label = label;
            this.stops = stops;
            this.avgDurationSeconds = avgDurationSeconds;
        }

        public String getLabel() {
            return label;
        }

        public void setLabel(String label) {
            this.label = label;
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
        private double avgAirTemp;
        private double avgTrackTemp;
        private int rainSamples;

        public WeatherPoint() {
        }

        public WeatherPoint(String label, double avgAirTemp, double avgTrackTemp, int rainSamples) {
            this.label = label;
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
        private String label;
        private double maxSpeed;
        private double avgSpeed;

        public TelemetrySummary() {
        }

        public TelemetrySummary(String label, double maxSpeed, double avgSpeed) {
            this.label = label;
            this.maxSpeed = maxSpeed;
            this.avgSpeed = avgSpeed;
        }

        public String getLabel() {
            return label;
        }

        public void setLabel(String label) {
            this.label = label;
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
