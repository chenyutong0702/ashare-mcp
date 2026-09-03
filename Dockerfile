FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    CACHE_DIR=/app/.cache/ashare-mcp

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p "$CACHE_DIR" \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 9876

CMD ["sh", "-c", "exec ashare-mcp --transport http --host 0.0.0.0 --port ${PORT:-9876}"]
