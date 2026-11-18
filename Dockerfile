FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 \
    SIGNAL_DB_PATH=/tmp/signal.db
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY data/sample_leads.csv ./data/sample_leads.csv

# Cloud Run / Render / Fly all inject $PORT
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
