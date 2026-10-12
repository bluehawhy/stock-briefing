FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install --no-cache-dir .
ARG BUILD_TIME=""
ARG BUILD_COMMIT="unknown"
RUN python -c 'import json, os; from datetime import datetime, timezone; from pathlib import Path; Path("/app/build-info.json").write_text(json.dumps({"build_time": os.environ.get("BUILD_TIME") or datetime.now(timezone.utc).isoformat(), "build_commit": os.environ.get("BUILD_COMMIT") or "unknown"}))'
ENV PYTHONUNBUFFERED=1 SQLITE_PATH=/data/stock-briefing.db
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
