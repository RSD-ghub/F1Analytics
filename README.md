# prompts

Formula 1 Analytics Dashboard

## Build and run


With JDK21
```bash
mvn package
java -jar target/prompts.jar
```

## Frontend Source Of Truth

- Use `frontend/f1-ui/src` as the editable frontend source.
- The backend serves static files from `src/main/resources/web/f1`.
- After frontend rebuild, copy the generated web assets into `src/main/resources/web/f1` so runtime matches source.
- Dashboard race background image source: Pexels photo `29252117` (stored locally as `images/f1-race-bg.jpg`).

## FastF1 Local Ingestion (No Docker)

- The FastF1 pipeline now runs with local Python, not Docker.
- Ingest script location: `scripts/fastf1-ingest/ingest.py`
- Requirements file: `scripts/fastf1-ingest/requirements.txt`

Install dependencies once:
```bash
py -3 -m pip install -r scripts/fastf1-ingest/requirements.txt
```

Manual ingest examples:
```bash
curl -X POST "http://localhost:8082/f1/ingest/season/2025"
curl -X POST "http://localhost:8082/f1/ingest/backfill?from=2010&to=2026"
curl -X GET "http://localhost:8082/f1/ingest/status"
```

Quick verification:
- Run a season ingest.
- Check `/f1/ingest/status` returns `state: SUCCESS`.
- Confirm `rowCounts` has non-zero values for available domains.

## Predictive Race Analytics

Predicts finish positions, optimal pit window, compound strategy, and lap time forecast for any ingested race.
Trained on all years of data (2010–present) using a `RandomForestRegressor`; falls back to a weighted historical average if scikit-learn is not installed.

### Install dependencies once
```bash
py -3 -m pip install -r scripts/predict/requirements.txt
```

### API endpoints
```bash
# Get prediction for a race (cached after first run)
curl -X GET "http://localhost:8082/f1/predict/race?season=2024&round=5"

# Force recompute (evicts cache and reruns the model)
curl -X POST "http://localhost:8082/f1/predict/race/refresh?season=2024&round=5"
```

### Response shape
```json
{
  "season": 2024,
  "round": 5,
  "raceName": "Monaco Grand Prix",
  "circuit": "Monte Carlo",
  "predictions": [
    {
      "driver": "Max Verstappen",
      "team": "Red Bull Racing",
      "actualPosition": 1,
      "predictedPosition": 1,
      "positionConfidence": 0.87,
      "predictedPoints": 25,
      "historicalWinsAtCircuit": 3,
      "historicalAvgPositionAtCircuit": 1.8,
      "seasonAvgPosition": 1.4
    }
  ],
  "strategy": {
    "recommendedPitLap": 28,
    "recommendedCompoundStrategy": "MEDIUM->HARD",
    "avgPitStopsAtCircuit": 1.9
  },
  "lapTimeForecast": [
    { "compound": "SOFT",   "predictedAvgLapTimeSeconds": 77.4, "sampleSize": 312 },
    { "compound": "MEDIUM", "predictedAvgLapTimeSeconds": 78.1, "sampleSize": 489 },
    { "compound": "HARD",   "predictedAvgLapTimeSeconds": 79.3, "sampleSize": 201 }
  ],
  "modelInfo": {
    "type": "RandomForestRegressor",
    "trainedOnSeasons": [2010, 2011, "...", 2024],
    "totalHistoricalRaces": 285,
    "circuitHistoricalRaces": 14
  }
}
```

### Notes
- Past-season predictions are cached permanently; current-season predictions expire after 7 days.
- The model is trained fresh on each cache-miss call. For large datasets this takes ~5–15 seconds.
- Data must be ingested first via the FastF1 ingest pipeline (see above).

## Exercise the application

Basic:
```
curl -X GET http://localhost:8080/simple-greet
Hello World!
```


JSON:
```
curl -X GET http://localhost:8080/greet
{"message":"Hello World!"}

curl -X GET http://localhost:8080/greet/Joe
{"message":"Hello Joe!"}

curl -X PUT -H "Content-Type: application/json" -d '{"greeting" : "Hola"}' http://localhost:8080/greet/greeting

curl -X GET http://localhost:8080/greet/Jose
{"message":"Hola Jose!"}
```



## Try health

```
curl -s -X GET http://localhost:8080/health
{"outcome":"UP",...

```


## Building a Native Image

The generation of native binaries requires an installation of GraalVM 22.1.0+.

You can build a native binary using Maven as follows:

```
mvn -Pnative-image install -DskipTests
```

The generation of the executable binary may take a few minutes to complete depending on
your hardware and operating system. When completed, the executable file will be available
under the `target` directory and be named after the artifact ID you have chosen during the
project generation phase.



## Try metrics

```
# Prometheus Format
curl -s -X GET http://localhost:8080/metrics
# TYPE base:gc_g1_young_generation_count gauge
. . .

# JSON Format
curl -H 'Accept: application/json' -X GET http://localhost:8080/metrics
{"base":...
. . .
```



## Building the Docker Image

```
docker build -t prompts .
```

## Running the Docker Image

```
docker run --rm -p 8080:8080 prompts:latest
```

Exercise the application as described above.
                                

## Run the application in Kubernetes

If you don’t have access to a Kubernetes cluster, you can [install one](https://helidon.io/docs/latest/#/about/kubernetes) on your desktop.

### Verify connectivity to cluster

```
kubectl cluster-info                        # Verify which cluster
kubectl get pods                            # Verify connectivity to cluster
```

### Deploy the application to Kubernetes

```
kubectl create -f app.yaml                              # Deploy application
kubectl get pods                                        # Wait for quickstart pod to be RUNNING
kubectl get service  prompts                     # Get service info
kubectl port-forward service/prompts 8081:8080   # Forward service port to 8081
```

You can now exercise the application as you did before but use the port number 8081.

After you’re done, cleanup.

```
kubectl delete -f app.yaml
```


## Building a Custom Runtime Image

Build the custom runtime image using the jlink image profile:

```
mvn package -Pjlink-image
```

This uses the helidon-maven-plugin to perform the custom image generation.
After the build completes it will report some statistics about the build including the reduction in image size.

The target/prompts-jri directory is a self contained custom image of your application. It contains your application,
its runtime dependencies and the JDK modules it depends on. You can start your application using the provide start script:

```
./target/prompts-jri/bin/start
```

Class Data Sharing (CDS) Archive
Also included in the custom image is a Class Data Sharing (CDS) archive that improves your application’s startup
performance and in-memory footprint. You can learn more about Class Data Sharing in the JDK documentation.

The CDS archive increases your image size to get these performance optimizations. It can be of significant size (tens of MB).
The size of the CDS archive is reported at the end of the build output.

If you’d rather have a smaller image size (with a slightly increased startup time) you can skip the creation of the CDS
archive by executing your build like this:

```
mvn package -Pjlink-image -Djlink.image.addClassDataSharingArchive=false
```

For more information on available configuration options see the helidon-maven-plugin documentation.
                                
