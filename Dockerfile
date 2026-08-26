FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
COPY hermes_smc ./hermes_smc
COPY hermes_smc/config ./hermes_smc/config
COPY hermes_smc/dashboard ./hermes_smc/dashboard
COPY hermes_smc/state_defaults ./hermes_smc/state_defaults
COPY pyproject.toml ./
RUN uv sync
ENV PYTHONUNBUFFERED=1
CMD ["uv", "run", "python", "-m", "hermes_smc.dashboard.server"]
