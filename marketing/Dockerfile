FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=120 UV_CONCURRENT_DOWNLOADS=8
WORKDIR /app
COPY backend/packages/harness /app/backend/packages/harness
COPY marketing/pyproject.toml marketing/uv.lock /app/marketing/
RUN --mount=type=cache,target=/root/.cache/uv cd /app/marketing && uv sync --frozen --extra runtime --no-dev --no-install-project
COPY marketing/marketing_agent /app/marketing/marketing_agent
COPY config.marketing.yaml /app/config.marketing.yaml
RUN --mount=type=cache,target=/root/.cache/uv mkdir -p /app/marketing/empty-skills && cd /app/marketing && uv sync --frozen --extra runtime --no-dev
WORKDIR /app/marketing
ENV PATH="/app/marketing/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "marketing_agent.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
