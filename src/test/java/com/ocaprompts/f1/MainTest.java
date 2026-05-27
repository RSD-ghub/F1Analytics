
package com.ocaprompts.f1;

import jakarta.inject.Inject;
import jakarta.ws.rs.client.WebTarget;
import jakarta.ws.rs.core.Response;
import org.eclipse.microprofile.metrics.Counter;
import org.eclipse.microprofile.metrics.MetricRegistry;
import jakarta.ws.rs.client.Entity;
import jakarta.ws.rs.core.MediaType;

import io.helidon.microprofile.testing.junit5.HelidonTest;
import io.helidon.metrics.api.MetricsFactory;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.AfterAll;

import java.util.List;
import java.util.Map;

import static org.hamcrest.MatcherAssert.assertThat;
import static org.hamcrest.Matchers.is;
import static org.hamcrest.Matchers.hasKey;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@HelidonTest
class MainTest {

    @Inject
    private MetricRegistry registry;

    @Inject
    private WebTarget target;


    @Test
    void testHealth() {
        Response response = target
                .path("health")
                .request()
                .get();
        assertThat(response.getStatus(), is(200));
    }

    @Test
    void testMicroprofileMetrics() {
        Message message = target.path("simple-greet/Joe")
                .request()
                .get(Message.class);

        assertThat(message.getMessage(), is("Hello Joe"));
        Counter counter = registry.counter("personalizedGets");
        double before = counter.getCount();

        message = target.path("simple-greet/Eric")
                .request()
                .get(Message.class);

        assertThat(message.getMessage(), is("Hello Eric"));
        double after = counter.getCount();
        assertEquals(1d, after - before, "Difference in personalized greeting counter between successive calls");
    }

    @AfterAll
    static void clear() {
        MetricsFactory.closeAll();
    }


    @Test
    void testGreet() {
        Message message = target
                .path("simple-greet")
                .request()
                .get(Message.class);
        assertThat(message.getMessage(), is("Hello World!"));
    }

    @Test
    void testGreetings() {
        Message jsonMessage = target
                .path("greet/Joe")
                .request()
                .get(Message.class);
        assertThat(jsonMessage.getMessage(), is("Hello Joe!"));

        try (Response r = target
                .path("greet/greeting")
                .request()
                .put(Entity.entity("{\"greeting\" : \"Hola\"}", MediaType.APPLICATION_JSON))) {
            assertThat(r.getStatus(), is(204));
        }

        jsonMessage = target
                .path("greet/Jose")
                .request()
                .get(Message.class);
        assertThat(jsonMessage.getMessage(), is("Hola Jose!"));
    }

    @Test
    void testSeasonAnalyticsEndpoint() {
        Map<?, ?> payload = target
                .path("f1/analytics/2026")
                .request()
                .get(Map.class);

        assertThat(payload, hasKey("season"));
        assertThat(payload, hasKey("driverTop10"));
        assertThat(payload, hasKey("constructorTop10"));
        assertThat(payload, hasKey("teamPoints"));
        assertThat(payload, hasKey("raceWinners"));
        assertThat(payload, hasKey("pointsByRound"));
        assertThat(payload, hasKey("podiumConsistency"));
        assertThat(payload, hasKey("lapTrend"));
        assertThat(payload, hasKey("stintSummary"));
        assertThat(payload, hasKey("pitStopStats"));
        assertThat(payload, hasKey("weatherSummary"));
        assertThat(payload, hasKey("telemetrySummary"));
        assertThat(payload, hasKey("raceControlTimeline"));

        try (Response bad = target.path("f1/analytics/1949").request().get()) {
            assertThat(bad.getStatus(), is(400));
        }
    }

    @Test
    void testDriverAnalyticsEndpoints() {
        List<?> drivers = target
                .path("f1/drivers")
                .request()
                .get(List.class);
        if (drivers.size() > 1) {
            String first = String.valueOf(drivers.get(0));
            String second = String.valueOf(drivers.get(1));
            assertTrue(first.compareToIgnoreCase(second) <= 0);
        }

        List<?> seasons = target
                .path("f1/seasons")
                .request()
                .get(List.class);
        if (seasons.size() > 1) {
            int firstSeason = ((Number) seasons.get(0)).intValue();
            int secondSeason = ((Number) seasons.get(1)).intValue();
            assertTrue(firstSeason >= secondSeason);
        }
        assertTrue(seasons.stream().allMatch(value -> ((Number) value).intValue() >= 2010));

        Map<?, ?> payload = target
                .path("f1/analytics/driver")
                .queryParam("name", "Max Verstappen")
                .request()
                .get(Map.class);

        assertThat(payload, hasKey("driver"));
        assertThat(payload, hasKey("totalPoints"));
        assertThat(payload, hasKey("wins"));
        assertThat(payload, hasKey("podiums"));
        assertThat(payload, hasKey("races"));
        assertThat(payload, hasKey("avgFinish"));
        assertThat(payload, hasKey("pointsByRace"));
        assertThat(payload, hasKey("finishByRace"));
        assertThat(payload, hasKey("teamPoints"));
        assertThat(payload, hasKey("pointsBySeason"));
        assertThat(payload, hasKey("podiumWinBreakdown"));
        assertThat(payload, hasKey("raceResults"));
        assertThat(payload, hasKey("lapTrend"));
        assertThat(payload, hasKey("stintSummary"));
        assertThat(payload, hasKey("pitStopStats"));
        assertThat(payload, hasKey("weatherSummary"));
        assertThat(payload, hasKey("telemetrySummary"));
        assertThat(payload, hasKey("raceControlTimeline"));

        String missingName = "__NO_SUCH_DRIVER__" + System.nanoTime();
        Map<?, ?> unknown = target
                .path("f1/analytics/driver")
                .queryParam("name", missingName)
                .queryParam("season", 2026)
                .request()
                .get(Map.class);
        assertEquals(0, ((Number) unknown.get("races")).intValue());

        Map<?, ?> bySeason = target
                .path("f1/analytics/driver")
                .queryParam("name", "Max Verstappen")
                .queryParam("season", 2026)
                .request()
                .get(Map.class);
        assertThat(bySeason, hasKey("driver"));
        assertThat(bySeason, hasKey("pointsByRace"));

        try (Response bad = target.path("f1/analytics/driver").request().get()) {
            assertThat(bad.getStatus(), is(400));
        }

        try (Response badSeason = target.path("f1/analytics/driver")
                .queryParam("name", "Max Verstappen")
                .queryParam("season", 1949)
                .request()
                .get()) {
            assertThat(badSeason.getStatus(), is(400));
        }
    }

    @Test
    void testIngestEndpoints() {
        Map<?, ?> status = target
                .path("f1/ingest/status")
                .request()
                .get(Map.class);
        assertThat(status, hasKey("state"));
        assertThat(status, hasKey("rowCounts"));

        try (Response badSeason = target
                .path("f1/ingest/season/2009")
                .request()
                .post(Entity.text(""))) {
            assertThat(badSeason.getStatus(), is(400));
        }

        try (Response disabledRuntime = target
                .path("f1/ingest/season/2026")
                .request()
                .post(Entity.text(""))) {
            assertThat(disabledRuntime.getStatus(), is(502));
        }

        try (Response badRange = target
                .path("f1/ingest/backfill")
                .queryParam("from", 2020)
                .queryParam("to", 2010)
                .request()
                .post(Entity.text(""))) {
            assertThat(badRange.getStatus(), is(400));
        }
    }

}
