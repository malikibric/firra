# syntax=docker/dockerfile:1.7
# Build context: repository root (the relay depends on packages/contracts/python).
FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/worker ./services/worker
COPY services/relay ./services/relay
COPY packages/contracts/python ./packages/contracts/python
RUN uv sync --frozen --no-dev --package firra-relay --no-editable

FROM python:3.12-slim AS runtime
RUN useradd --system --uid 10001 --no-create-home firra
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PORT=8080
USER firra
EXPOSE 8080
CMD ["python", "-m", "firra_relay.app"]
