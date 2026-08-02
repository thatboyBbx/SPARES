# Development image for the SPOP FastAPI backend.
# A separate, multi-stage production Dockerfile should be introduced
# before deploying beyond local/dev environments (this one intentionally
# favours fast rebuilds via bind-mounts over image size).
FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg (binary wheel avoids needing libpq-dev, but
# build-essential is kept for any future package that needs compiling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
