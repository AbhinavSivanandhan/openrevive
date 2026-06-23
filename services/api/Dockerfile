# syntax=docker/dockerfile:1

FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /workspace/services/api

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY services/api/pyproject.toml services/api/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

EXPOSE 8000

CMD ["uv", "run", "--frozen", "fastapi", "dev", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
