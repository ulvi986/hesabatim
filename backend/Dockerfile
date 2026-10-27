FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000
# Migrations run first on every start; safe with the single replica this app supports (in-process rate limits).
# PORT may be supplied by the host.
# "::" listens on IPv4 and IPv6, as Railway's private network requires. Proxy headers are not trusted here:
# the client address comes only from the frontend's secret-signed X-Client-IP (see app/api/deps.py).
CMD ["sh", "-c", "uv run --no-dev alembic upgrade head && exec uv run --no-dev uvicorn app.main:app --host :: --port ${PORT:-8000} --no-proxy-headers"]
