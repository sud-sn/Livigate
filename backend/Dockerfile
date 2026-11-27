# Build stage: uv installs the locked dependencies into a virtualenv. uv itself never reaches the runtime image.
FROM python:3.13.18-slim-bookworm AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv==0.13.8
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY levigate ./levigate
RUN uv sync --frozen --no-dev

# Runtime stage: the same Python base with current OS security updates, the virtualenv and the app code only.
FROM python:3.13.18-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*
# pip is not needed at runtime (the virtualenv is complete); removing it shrinks what can be exploited
RUN python -m pip uninstall -y pip
RUN useradd --system --uid 10001 --home /app levigate
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY levigate ./levigate
RUN mkdir -p /app/data && chown levigate /app/data

USER levigate
EXPOSE 8000
CMD ["uvicorn", "levigate.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
