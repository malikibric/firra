# syntax=docker/dockerfile:1.7
# Build context: repository root (the worker depends on packages/contracts/python).
FROM ghcr.io/astral-sh/uv:0.13 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# The venv must live at the same path in both stages (console-script shebangs are absolute).
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/worker ./services/worker
COPY packages/contracts/python ./packages/contracts/python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package firra-worker --no-editable

FROM python:3.12-slim AS runtime
RUN useradd --system --uid 10001 --no-create-home firra
COPY --from=build /app/.venv /app/.venv
COPY services/worker/prompts /app/prompts
COPY services/worker/config /app/config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PORT=8080 FIRRA_PROMPTS_DIR=/app/prompts FIRRA_CONFIG_DIR=/app/config
USER firra
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn --factory firra.app:create_app --host 0.0.0.0 --port ${PORT} --no-access-log --proxy-headers --forwarded-allow-ips='*'"]
