package com.ocaprompts.f1;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.helidon.config.Config;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.ProxySelector;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@ApplicationScoped
public class F1SyncService {

    private static final int MIN_SEASON = 1950;
    private static final int MAX_SEASON = 2100;

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient httpClient;
    private final String baseUrl;

    @Inject
    public F1SyncService(Config config) {
        this.baseUrl = config.get("f1api.base-url").asString().orElse("https://api.jolpi.ca/ergast");
        System.setProperty("java.net.useSystemProxies", "true");

        HttpClient.Builder builder = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(HttpClient.Redirect.NORMAL);

        ProxySelector proxySelector = ProxySelector.getDefault();
        if (proxySelector != null) {
            builder.proxy(proxySelector);
        }

        this.httpClient = builder.build();
    }

    public List<Prompt> fetchCurrentSeasonResults() {
        return fetchSeasonResults(java.time.Year.now().getValue());
    }

    public List<Prompt> fetchSeasonResults(int season) {
        if (season < MIN_SEASON || season > MAX_SEASON) {
            throw new IllegalArgumentException("Season must be between 1950 and 2100");
        }

        String cleanBase = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        String targetUrl = cleanBase + "/f1/" + URLEncoder.encode(String.valueOf(season), StandardCharsets.UTF_8) + "/results.json?limit=1100";

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(targetUrl))
                .timeout(Duration.ofSeconds(30))
                .GET()
                .build();

        HttpResponse<String> response;
        try {
            response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Failed to call live F1 API: " + e.getMessage(), e);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to call live F1 API: " + e.getMessage(), e);
        }

        if (response.statusCode() < 200 || response.statusCode() > 299) {
            throw new IllegalStateException("F1 API returned status " + response.statusCode());
        }

        return parseResults(response.body(), season);
    }

    private List<Prompt> parseResults(String json, int fallbackSeason) {
        List<Prompt> rows = new ArrayList<>();

        try {
            JsonNode races = mapper.readTree(json)
                    .path("MRData")
                    .path("RaceTable")
                    .path("Races");

            long importedAt = System.currentTimeMillis();
            for (JsonNode race : races) {
                int season = parseInt(race.path("season").asText(), fallbackSeason);
                int round = parseInt(race.path("round").asText(), 0);
                String raceName = race.path("raceName").asText("");
                String raceDate = race.path("date").asText("");
                String circuit = race.path("Circuit").path("circuitName").asText("");

                for (JsonNode result : race.path("Results")) {
                    String givenName = result.path("Driver").path("givenName").asText("");
                    String familyName = result.path("Driver").path("familyName").asText("");
                    String code = result.path("Driver").path("code").asText("");
                    String driver = (givenName + " " + familyName).trim();
                    if (driver.isBlank()) {
                        driver = code;
                    }

                    String team = result.path("Constructor").path("name").asText("");
                    int position = parseInt(result.path("position").asText(), 999);
                    double points = parseDouble(result.path("points").asText(), 0d);

                    Prompt p = new Prompt();
                    p.setId(generateStableId(season, round, driver, team));
                    p.setSeason(season);
                    p.setRound(round);
                    p.setRaceName(raceName);
                    p.setCircuit(circuit);
                    p.setRaceDate(raceDate);
                    p.setDriver(driver);
                    p.setTeam(team);
                    p.setPosition(position);
                    p.setPoints(points);
                    p.setTimestamp(importedAt);
                    rows.add(p);
                }
            }
        } catch (IOException e) {
            throw new IllegalStateException("Failed to parse F1 API response", e);
        }

        return rows;
    }

    private int parseInt(String value, int defaultValue) {
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    private double parseDouble(String value, double defaultValue) {
        try {
            return Double.parseDouble(value);
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    private String generateStableId(int season, int round, String driver, String team) {
        String key = season + "|" + round + "|" + driver + "|" + team;
        return UUID.nameUUIDFromBytes(key.getBytes(StandardCharsets.UTF_8)).toString();
    }
}
