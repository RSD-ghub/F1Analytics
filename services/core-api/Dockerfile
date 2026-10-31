FROM python:3.12-slim

WORKDIR /app

COPY shared/f1_common /shared/f1_common
COPY constraints.txt /tmp/constraints.txt
COPY services/core-api/requirements.txt /tmp/requirements.txt

RUN sed -i 's|-e ../../shared/f1_common|-e /shared/f1_common|' /tmp/requirements.txt \
    && pip install --no-cache-dir -c /tmp/constraints.txt -r /tmp/requirements.txt

COPY services/core-api/app /app/app

# Run as an unprivileged user: a compromised dependency or request handler
# then cannot change the installed code or packages.
RUN useradd --create-home --uid 10001 --user-group app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
