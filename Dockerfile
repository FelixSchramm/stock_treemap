# Reproducible single-container build from uv.lock. Builds nothing but the demo
# by default: no secrets, credentials or private data are copied or baked in.
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-editable
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORTFOLIO_MODE=demo \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent app
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY app.py ./
COPY .streamlit/config.toml ./.streamlit/config.toml
USER app
EXPOSE 8501
# Liveness only: Streamlit's built-in endpoint returns "ok" and no portfolio data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; r = u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3); raise SystemExit(0 if r.status == 200 else 1)"]
CMD ["streamlit", "run", "app.py"]
