FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
COPY pyproject.toml uv.lock ./
COPY hermes_smc ./hermes_smc
RUN uv sync --frozen
ENV PYTHONUNBUFFERED=1
ENV STATE_DIR=/app/state
CMD ["sh", "-c", "uv run python -m hermes_smc --mode combined --port ${PORT:-8080}"]
