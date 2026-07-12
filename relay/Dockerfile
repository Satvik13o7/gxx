FROM python:3.12-slim

WORKDIR /app
COPY relay/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY relay/pii.py relay/ratelimit.py relay/auth.py relay/app.py /app/relay/
COPY relay/__init__.py /app/relay/__init__.py
COPY website /app/website
COPY install /app/install
COPY datastore /app/datastore
COPY watcher /app/watcher
COPY mcp_server /app/mcp_server
COPY skill /app/skill
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
ENV PYTHONPATH=/app

# Required env at deploy time (Railway/Fly.io secrets):
#   CONTOUR_RELAY_TOKENS  comma-separated device tokens (empty = open dev mode)
#   TAVILY_API_KEY or SERPER_API_KEY   (search)
#   OPENAI_API_KEY    (opt-in cloud escalation)
#   CONTOUR_RELAY_RATE    per-token requests/minute (default 120)
EXPOSE 8080
CMD ["uvicorn", "relay.app:app", "--host", "0.0.0.0", "--port", "8080"]
