package com.ocaprompts.f1;

import java.util.LinkedHashMap;
import java.util.Map;

public class FastF1IngestionStatus {
    private String state = "IDLE";
    private String trigger = "";
    private int fromSeason;
    private int toSeason;
    private long startedAt;
    private long finishedAt;
    private long durationMs;
    private String message = "";
    private Map<String, Integer> rowCounts = new LinkedHashMap<>();

    public String getState() {
        return state;
    }

    public void setState(String state) {
        this.state = state;
    }

    public String getTrigger() {
        return trigger;
    }

    public void setTrigger(String trigger) {
        this.trigger = trigger;
    }

    public int getFromSeason() {
        return fromSeason;
    }

    public void setFromSeason(int fromSeason) {
        this.fromSeason = fromSeason;
    }

    public int getToSeason() {
        return toSeason;
    }

    public void setToSeason(int toSeason) {
        this.toSeason = toSeason;
    }

    public long getStartedAt() {
        return startedAt;
    }

    public void setStartedAt(long startedAt) {
        this.startedAt = startedAt;
    }

    public long getFinishedAt() {
        return finishedAt;
    }

    public void setFinishedAt(long finishedAt) {
        this.finishedAt = finishedAt;
    }

    public long getDurationMs() {
        return durationMs;
    }

    public void setDurationMs(long durationMs) {
        this.durationMs = durationMs;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public Map<String, Integer> getRowCounts() {
        return rowCounts;
    }

    public void setRowCounts(Map<String, Integer> rowCounts) {
        this.rowCounts = rowCounts;
    }
}
