package com.ocaprompts.f1;

import com.mongodb.MongoClientSettings;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import io.helidon.config.Config;
import jakarta.annotation.PreDestroy;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import org.bson.codecs.configuration.CodecRegistry;
import org.bson.codecs.pojo.PojoCodecProvider;

import static org.bson.codecs.configuration.CodecRegistries.fromProviders;
import static org.bson.codecs.configuration.CodecRegistries.fromRegistries;

@ApplicationScoped
public class MongoProvider {

    private final MongoClient client;
    private final MongoDatabase db;

    @Inject
    public MongoProvider(Config config) {
        String conn = config.get("mongo.connection").asString().get();
        String name = config.get("mongo.database").asString().get();

        this.client = MongoClients.create(conn);

        CodecRegistry pojoRegistry =
                fromProviders(PojoCodecProvider.builder()
                        .automatic(true)
                        .build());

        CodecRegistry registry =
                fromRegistries(MongoClientSettings.getDefaultCodecRegistry(),
                        pojoRegistry);

        this.db = client.getDatabase(name).withCodecRegistry(registry);
    }

    public MongoCollection<Prompt> prompts() {
        return db.getCollection("prompts", Prompt.class);
    }

    public MongoCollection<FastF1DomainData.LapRow> laps() {
        return db.getCollection("laps", FastF1DomainData.LapRow.class);
    }

    public MongoCollection<FastF1DomainData.StintRow> stints() {
        return db.getCollection("stints", FastF1DomainData.StintRow.class);
    }

    public MongoCollection<FastF1DomainData.PitStopRow> pitStops() {
        return db.getCollection("pit_stops", FastF1DomainData.PitStopRow.class);
    }

    public MongoCollection<FastF1DomainData.WeatherRow> weather() {
        return db.getCollection("weather", FastF1DomainData.WeatherRow.class);
    }

    public MongoCollection<FastF1DomainData.RaceControlRow> raceControl() {
        return db.getCollection("race_control", FastF1DomainData.RaceControlRow.class);
    }

    public MongoCollection<FastF1DomainData.TelemetryRow> telemetry() {
        return db.getCollection("telemetry", FastF1DomainData.TelemetryRow.class);
    }

    @PreDestroy
    void close() {
        client.close();
    }
}
