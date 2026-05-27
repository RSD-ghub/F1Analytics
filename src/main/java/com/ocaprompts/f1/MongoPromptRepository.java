package com.ocaprompts.f1;

import com.mongodb.client.MongoCollection;
import com.mongodb.client.model.Filters;
import com.mongodb.client.model.ReplaceOptions;
import com.mongodb.client.model.Sorts;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@ApplicationScoped
public class MongoPromptRepository {

    private final MongoCollection<Prompt> collection;

    @Inject
    public MongoPromptRepository(MongoProvider mongoProvider) {
        this.collection = mongoProvider.prompts();
    }

    public List<Prompt> list() {
        return collection.find()
                .sort(Sorts.orderBy(
                        Sorts.descending("season"),
                        Sorts.descending("round"),
                        Sorts.ascending("position")))
                .into(new ArrayList<>());
    }

    public Optional<Prompt> get(String id) {
        if (id == null || id.isBlank()) {
            return Optional.empty();
        }
        return Optional.ofNullable(collection.find(Filters.eq("_id", id)).first());
    }

    public Prompt add(Prompt prompt) {
        collection.replaceOne(
                Filters.eq("_id", prompt.getId()),
                prompt,
                new ReplaceOptions().upsert(true)
        );
        return prompt;
    }

    public int replaceSeasonResults(int season, List<Prompt> imported) {
        collection.deleteMany(Filters.eq("season", season));
        if (!imported.isEmpty()) {
            collection.insertMany(imported);
        }
        return imported.size();
    }

    public boolean delete(String id) {
        if (id == null || id.isBlank()) {
            return false;
        }
        return collection.deleteOne(Filters.eq("_id", id)).getDeletedCount() > 0;
    }

    public List<StandingsEntry> driverStandings() {
        return StandingsSupport.aggregateByDriver(list());
    }

    public List<StandingsEntry> teamStandings() {
        return StandingsSupport.aggregateByTeam(list());
    }
}
