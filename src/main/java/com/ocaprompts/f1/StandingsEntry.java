package com.ocaprompts.f1;

public class StandingsEntry {
    private String name;
    private double points;
    private int wins;

    public StandingsEntry() {
    }

    public StandingsEntry(String name) {
        this.name = name;
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

    public int getWins() {
        return wins;
    }

    public void setWins(int wins) {
        this.wins = wins;
    }
}