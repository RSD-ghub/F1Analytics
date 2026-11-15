FROM python:3.12-slim

WORKDIR /app

# f1_common is installed editable from a sibling path, so it must be present
# before requirements.txt is processed.
COPY shared/f1_common /shared/f1_common
COPY constraints.txt /tmp/constraints.txt
COPY services/ingestion-service/requirements.txt /tmp/requirements.txt

# Rewrite the relative editable path to its location inside the image.
RUN sed -i 's|-e ../../shared/f1_common|-e /shared/f1_common|' /tmp/requirements.txt \
    && pip install --no-cache-dir -c /tmp/constraints.txt -r /tmp/requirements.txt

COPY services/ingestion-service/app /app/app

# Run as an unprivileged user: a compromised dependency or request handler
# then cannot change the installed code or packages.
RUN useradd --create-home --uid 10001 --user-group app \
    && mkdir -p /data/fastf1 && chown app:app /data/fastf1
USER app

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
