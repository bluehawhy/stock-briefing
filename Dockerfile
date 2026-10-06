FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1 SQLITE_PATH=/data/stock-briefing.db
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
