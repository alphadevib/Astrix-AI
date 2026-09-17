# Astrix-AI backend — FastAPI + WebSocket + in-process mission simulator.
#
# Vercel hosts the static console; this container hosts the API (Render, Fly.io,
# Railway, Cloud Run, a VM...). It needs a long-lived process and WebSockets,
# which serverless functions do not provide.
#
#   docker build -t astrix-api .
#   docker run -p 8000:8000 -e ASTRIX_API_TOKEN=change-me -v astrix-data:/app/data astrix-api

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend ./backend
COPY telemetry ./telemetry
COPY scripts ./scripts

# Train the detector at build time so a cold container starts in seconds.
RUN mkdir -p data/models && python -c "from backend.app.bootstrap import Astrix; Astrix()" \
    && rm -f data/astrix.db data/vector_store.json

RUN useradd --create-home astrix && chown -R astrix /app
USER astrix

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/meta')"

CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
