FROM python:3.12-slim

WORKDIR /app

COPY shared/f1_common /shared/f1_common
COPY constraints.txt /tmp/constraints.txt
COPY services/prediction-service/requirements.txt /tmp/requirements.txt

RUN sed -i 's|-e ../../shared/f1_common|-e /shared/f1_common|' /tmp/requirements.txt \
    && pip install --no-cache-dir -c /tmp/constraints.txt -r /tmp/requirements.txt

COPY services/prediction-service/app /app/app

# The retraining scripts ship too. Without them there is no way to run the
# promotion gate on a deployed box, and a model that can only ever be retrained
# on a laptop is not a continuous loop — it is a manual step with extra ceremony.
COPY services/prediction-service/scripts /app/scripts

# Where the model artifact, its archive and the promotion history are kept.
# The copy baked in above seeds this on first boot and is read-only thereafter;
# compose mounts a volume here so a promotion outlives the container that made
# it. Left unset, everything falls back to the packaged location, which is what
# a local checkout has always done.
ENV MODEL_STATE_DIR=/data/model
# Run as an unprivileged user. The model state directory is created and owned
# before VOLUME, so a new named volume inherits that owner and stays writable.
RUN useradd --create-home --uid 10001 --user-group app \
    && mkdir -p /data/model && chown app:app /data/model
USER app
VOLUME ["/data/model"]

EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
