package com.ocaprompts.f1;

public final class FastF1DomainData {

    private FastF1DomainData() {
    }

    public static class LapRow {
        private int season;
        private int round;
        private String raceName;
        private String driver;
        private int lap;
        private double lapTimeSeconds;
        private String compound;
        private int stint;

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

        public String getRaceName() {
            return raceName;
        }

        public void setRaceName(String raceName) {
            this.raceName = raceName;
        }

        public String getDriver() {
            return driver;
        }

        public void setDriver(String driver) {
            this.driver = driver;
        }

        public int getLap() {
            return lap;
        }

        public void setLap(int lap) {
            this.lap = lap;
        }

        public double getLapTimeSeconds() {
            return lapTimeSeconds;
        }

        public void setLapTimeSeconds(double lapTimeSeconds) {
            this.lapTimeSeconds = lapTimeSeconds;
        }

        public String getCompound() {
            return compound;
        }

        public void setCompound(String compound) {
            this.compound = compound;
        }

        public int getStint() {
            return stint;
        }

        public void setStint(int stint) {
            this.stint = stint;
        }
    }

    public static class StintRow {
        private int season;
        private int round;
        private String raceName;
        private String driver;
        private int stint;
        private String compound;
        private int laps;

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

        public String getRaceName() {
            return raceName;
        }

        public void setRaceName(String raceName) {
            this.raceName = raceName;
        }

        public String getDriver() {
            return driver;
        }

        public void setDriver(String driver) {
            this.driver = driver;
        }

        public int getStint() {
            return stint;
        }

        public void setStint(int stint) {
            this.stint = stint;
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

    public static class PitStopRow {
        private int season;
        private int round;
        private String raceName;
        private String driver;
        private int stop;
        private int lap;
        private double durationSeconds;

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

        public String getRaceName() {
            return raceName;
        }

        public void setRaceName(String raceName) {
            this.raceName = raceName;
        }

        public String getDriver() {
            return driver;
        }

        public void setDriver(String driver) {
            this.driver = driver;
        }

        public int getStop() {
            return stop;
        }

        public void setStop(int stop) {
            this.stop = stop;
        }

        public int getLap() {
            return lap;
        }

        public void setLap(int lap) {
            this.lap = lap;
        }

        public double getDurationSeconds() {
            return durationSeconds;
        }

        public void setDurationSeconds(double durationSeconds) {
            this.durationSeconds = durationSeconds;
        }
    }

    public static class WeatherRow {
        private int season;
        private int round;
        private String raceName;
        private String sampleTime;
        private double airTemp;
        private double trackTemp;
        private double humidity;
        private boolean rainfall;

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

        public String getRaceName() {
            return raceName;
        }

        public void setRaceName(String raceName) {
            this.raceName = raceName;
        }

        public String getSampleTime() {
            return sampleTime;
        }

        public void setSampleTime(String sampleTime) {
            this.sampleTime = sampleTime;
        }

        public double getAirTemp() {
            return airTemp;
        }

        public void setAirTemp(double airTemp) {
            this.airTemp = airTemp;
        }

        public double getTrackTemp() {
            return trackTemp;
        }

        public void setTrackTemp(double trackTemp) {
            this.trackTemp = trackTemp;
        }

        public double getHumidity() {
            return humidity;
        }

        public void setHumidity(double humidity) {
            this.humidity = humidity;
        }

        public boolean isRainfall() {
            return rainfall;
        }

        public void setRainfall(boolean rainfall) {
            this.rainfall = rainfall;
        }
    }

    public static class RaceControlRow {
        private int season;
        private int round;
        private String raceName;
        private String category;
        private String message;
        private String time;
        private int lap;

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

        public String getRaceName() {
            return raceName;
        }

        public void setRaceName(String raceName) {
            this.raceName = raceName;
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

        public int getLap() {
            return lap;
        }

        public void setLap(int lap) {
            this.lap = lap;
        }
    }

    public static class TelemetryRow {
        private int season;
        private int round;
        private String raceName;
        private String driver;
        private String sampleTime;
        private double maxSpeed;
        private double avgSpeed;
        private double throttleMean;
        private double brakeMean;

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

        public String getRaceName() {
            return raceName;
        }

        public void setRaceName(String raceName) {
            this.raceName = raceName;
        }

        public String getDriver() {
            return driver;
        }

        public void setDriver(String driver) {
            this.driver = driver;
        }

        public String getSampleTime() {
            return sampleTime;
        }

        public void setSampleTime(String sampleTime) {
            this.sampleTime = sampleTime;
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

        public double getThrottleMean() {
            return throttleMean;
        }

        public void setThrottleMean(double throttleMean) {
            this.throttleMean = throttleMean;
        }

        public double getBrakeMean() {
            return brakeMean;
        }

        public void setBrakeMean(double brakeMean) {
            this.brakeMean = brakeMean;
        }
    }
}
