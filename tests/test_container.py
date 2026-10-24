"""Static, offline checks of the container definition (no Docker required)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_dockerfile_uses_lockfile_and_health_check():
    assert "uv sync --locked" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE
    assert "/_stcore/health" in DOCKERFILE
    assert "PORTFOLIO_MODE=demo" in DOCKERFILE
    assert re.search(r"^USER app$", DOCKERFILE, re.MULTILINE)


def test_dockerfile_copies_only_explicit_files():
    copies = [line for line in DOCKERFILE.splitlines() if line.startswith("COPY ")]
    assert copies
    for line in copies:
        assert not re.search(r"COPY (--\S+ )*\.\s", line), line


def test_dockerignore_excludes_private_material():
    required = {
        "**/.git",
        "**/.env",
        "**/secrets.toml",
        "**/*credentials*.json",
        "**/*.pem",
        "**/*.key",
        "**/data",
        "**/exports",
        "**/.cache",
        "docs/private",
        "**/tickers.txt",
        "**/heatmap.html",
    }
    assert required <= set(DOCKERIGNORE)


def test_dockerfile_hides_streamlit_welcome_message():
    # Otherwise headless Streamlit looks up the external IP at startup (third-party call, IP in log).
    assert "STREAMLIT_LOGGER_HIDE_WELCOME_MESSAGE=true" in DOCKERFILE
