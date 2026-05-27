package com.ocaprompts.f1;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.validation.Valid;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.DELETE;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.Context;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import jakarta.ws.rs.core.UriInfo;

import java.net.URI;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static jakarta.ws.rs.core.Response.Status.BAD_REQUEST;
import static jakarta.ws.rs.core.Response.Status.BAD_GATEWAY;
import static jakarta.ws.rs.core.Response.Status.NOT_FOUND;

@ApplicationScoped
@Path("/f1")
@Consumes(MediaType.APPLICATION_JSON)
@Produces(MediaType.APPLICATION_JSON)
public class PromptResource {

    @Inject
    PromptRepository repo;

    @Inject
    F1SyncService f1SyncService;

    @Inject
    F1AnalyticsService analyticsService;

    @Inject
    F1DomainAnalyticsService domainAnalyticsService;

    @Inject
    FastF1IngestionService fastF1IngestionService;

    @Context
    UriInfo uriInfo;

    @GET
    @Path("/results")
    public List<Prompt> listResults() {
        return repo.list();
    }

    @GET
    @Path("/results/{id}")
    public Response getResult(@PathParam("id") String id) {
        return repo.get(id)
                .map(Response::ok)
                .orElse(Response.status(NOT_FOUND))
                .build();
    }

    @POST
    @Path("/results")
    public Response addResult(@Valid Prompt p) {
        if (p.getRaceName() == null || p.getRaceName().isBlank()) {
            return Response.status(BAD_REQUEST).entity("raceName required").build();
        }

        if (p.getDriver() == null || p.getDriver().isBlank()) {
            return Response.status(BAD_REQUEST).entity("driver required").build();
        }

        if (p.getTeam() == null || p.getTeam().isBlank()) {
            return Response.status(BAD_REQUEST).entity("team required").build();
        }

        if (p.getSeason() < 1950 || p.getRound() <= 0 || p.getPosition() <= 0 || p.getPoints() < 0) {
            return Response.status(BAD_REQUEST)
                    .entity("invalid season/round/position/points")
                    .build();
        }

        p.setId(UUID.randomUUID().toString());
        p.setTimestamp(System.currentTimeMillis());

        repo.add(p);

        URI location = uriInfo.getAbsolutePathBuilder()
                .path(p.getId())
                .build();

        return Response.created(location).entity(p).build();
    }

    @DELETE
    @Path("/results/{id}")
    public Response deleteResult(@PathParam("id") String id) {
        return repo.delete(id)
                ? Response.noContent().build()
                : Response.status(NOT_FOUND).build();
    }

    @GET
    @Path("/standings/drivers")
    public List<StandingsEntry> driverStandings() {
        return repo.driverStandings();
    }

    @GET
    @Path("/standings/constructors")
    public List<StandingsEntry> constructorStandings() {
        return repo.teamStandings();
    }

    @GET
    @Path("/analytics/{season}")
    public Response analytics(@PathParam("season") int season) {
        try {
            List<Prompt> rows = repo.list();
            F1AnalyticsResponse payload = analyticsService.buildSeasonAnalytics(season, rows);
            domainAnalyticsService.enrichSeasonAnalytics(payload, season, rows);
            return Response.ok(payload).build();
        } catch (IllegalArgumentException e) {
            return Response.status(BAD_REQUEST).entity(e.getMessage()).build();
        }
    }

    @GET
    @Path("/drivers")
    public List<String> drivers() {
        return analyticsService.listDrivers(repo.list());
    }

    @GET
    @Path("/seasons")
    public List<Integer> seasons() {
        return analyticsService.listSeasons(repo.list());
    }

    @GET
    @Path("/analytics/driver")
    public Response driverAnalytics(@QueryParam("name") String driverName,
                                    @QueryParam("season") Integer season) {
        try {
            List<Prompt> rows = repo.list();
            DriverAnalyticsResponse payload = analyticsService.buildDriverAnalytics(driverName, season, rows);
            domainAnalyticsService.enrichDriverAnalytics(payload, driverName, season, rows);
            return Response.ok(payload).build();
        } catch (IllegalArgumentException e) {
            return Response.status(BAD_REQUEST).entity(e.getMessage()).build();
        }
    }

    @GET
    @Path("/ingest/status")
    public FastF1IngestionStatus ingestStatus() {
        return fastF1IngestionService.getStatus();
    }

    @POST
    @Path("/ingest/backfill")
    @Consumes(MediaType.WILDCARD)
    public Response backfillSeasons(@QueryParam("from") Integer fromSeason,
                                    @QueryParam("to") Integer toSeason) {
        try {
            FastF1IngestionStatus status;
            if (fromSeason != null || toSeason != null) {
                int from = fromSeason == null ? fastF1IngestionService.getSeasonFloor() : fromSeason;
                int to = toSeason == null ? java.time.Year.now().getValue() : toSeason;
                status = fastF1IngestionService.ingestRange(from, to);
            } else {
                status = fastF1IngestionService.backfillFromFloor();
            }
            return Response.ok(status).build();
        } catch (IllegalArgumentException e) {
            return Response.status(BAD_REQUEST).entity(e.getMessage()).build();
        } catch (IllegalStateException e) {
            return Response.status(BAD_GATEWAY).entity(e.getMessage()).build();
        }
    }

    @POST
    @Path("/ingest/season/{season}")
    @Consumes(MediaType.WILDCARD)
    public Response ingestSeason(@PathParam("season") int season) {
        try {
            FastF1IngestionStatus status = fastF1IngestionService.ingestSeason(season);
            return Response.ok(status).build();
        } catch (IllegalArgumentException e) {
            return Response.status(BAD_REQUEST).entity(e.getMessage()).build();
        } catch (IllegalStateException e) {
            return Response.status(BAD_GATEWAY).entity(e.getMessage()).build();
        }
    }

    @POST
    @Path("/sync/current")
    @Consumes(MediaType.WILDCARD)
    public Response syncCurrentSeason() {
        int currentSeason = java.time.Year.now().getValue();
        return syncSeasonInternal(currentSeason);
    }

    @POST
    @Path("/sync/{season}")
    @Consumes(MediaType.WILDCARD)
    public Response syncSeason(@PathParam("season") int season) {
        return syncSeasonInternal(season);
    }

    private Response syncSeasonInternal(int season) {
        try {
            fastF1IngestionService.ingestSeason(season);
            int stored = repo.countSeasonResults(season);
            int imported = stored;
            return Response.ok(syncPayload(season, imported, stored, "FastF1 data synced", false)).build();
        } catch (IllegalArgumentException e) {
            return Response.status(BAD_REQUEST).entity(e.getMessage()).build();
        } catch (IllegalStateException e) {
            int stored = repo.countSeasonResults(season);
            Map<String, Object> payload = syncPayload(
                    season,
                    0,
                    stored,
                    "FastF1 sync unavailable. Using cached data for this season.",
                    true
            );
            payload.put("detail", e.getMessage());
            return Response.ok(payload).build();
        }
    }

    private Map<String, Object> syncPayload(int season,
                                            int imported,
                                            int stored,
                                            String message,
                                            boolean degraded) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("season", season);
        payload.put("imported", imported);
        payload.put("stored", stored);
        payload.put("degraded", degraded);
        payload.put("message", message);
        return payload;
    }
}
