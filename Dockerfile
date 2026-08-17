FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Installed in its own layer, ahead of the app code, so this only reruns when
# pyproject.toml/uv.lock actually change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY api.py hotspot_optimizer.py ./
COPY static/ static/

ENV PATH="/app/.venv/bin:${PATH}"

# Data directory should be mounted as a volume at runtime:
#   docker run -v /path/to/data:/app/data ...
# This avoids baking the 6+ GB database into the image.

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
