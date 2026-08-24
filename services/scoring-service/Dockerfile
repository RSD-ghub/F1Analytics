FROM python:3.12-slim

WORKDIR /app

COPY shared/f1_common /shared/f1_common
COPY services/scoring-service/requirements.txt /tmp/requirements.txt

RUN sed -i 's|-e ../../shared/f1_common|-e /shared/f1_common|' /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY services/scoring-service/app /app/app

EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
