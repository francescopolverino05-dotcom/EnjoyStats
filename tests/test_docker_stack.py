"""Structural checks for the Docker Compose stack (no daemon required)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_stack_wires_db_api_and_dashboard() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "container_name: enjoystats-db" in compose
    assert "container_name: enjoystats-api" in compose
    assert "container_name: enjoystats-dashboard" in compose
    assert (
        "./storage/postgres_tables.sql:/docker-entrypoint-initdb.d/01-postgres_tables.sql"
        in compose
    )
    assert "condition: service_healthy" in compose
    assert '"5432:5432"' in compose
    assert '"8000:8000"' in compose
    assert '"8501:8501"' in compose
    assert "ENJOYSTATS_API_URL" in compose
    assert "postgres_data" in compose
    assert "pg_isready" in compose
    assert "/health" in compose
    assert "/_stcore/health" in compose


def test_dockerfiles_are_multistage_slim_python() -> None:
    api = (ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    dashboard = (ROOT / "Dockerfile.dashboard").read_text(encoding="utf-8")
    web_entry = (ROOT / "scripts" / "statman_web_entrypoint.sh").read_text(encoding="utf-8")
    assert "python:3.11-slim AS builder" in api
    assert "python:3.11-slim AS runtime" in api
    assert "python:3.11-slim AS builder" in dashboard
    assert "python:3.11-slim AS runtime" in dashboard
    assert 'CMD ["python", "-m", "api.main"]' in api
    assert "streamlit" in dashboard
    assert "opencv-python-headless" in dashboard
    assert "yt-dlp" in dashboard
    assert "maxUploadSize=5120" in web_entry
    assert "maxMessageSize=5120" in web_entry
    assert "COPY api ./api" in dashboard
    assert "/src/api" in dashboard
    assert "statman_web_entrypoint.sh" in dashboard
    assert "STATMAN_STREAMLIT_FILM_UPLOAD=1" in dashboard
    assert "fastapi" in dashboard
    assert "http://127.0.0.1:8000" in dashboard


def test_railway_web_entrypoint_runs_streamlit_on_port() -> None:
    script = (ROOT / "scripts" / "statman_web_entrypoint.sh").read_text(encoding="utf-8")
    assert "streamlit run app/dashboard.py" in script
    assert "server.address=0.0.0.0" in script
    assert "LISTEN_PORT" in script
    assert "STATMAN_LISTEN_PORT" in script
    assert "STATMAN_STREAMLIT_FILM_UPLOAD" in script
    # Must not drop privileges via su — that broke PATH/PORT on Railway.
    assert "su " not in script
    railway = (ROOT / "railway.toml").read_text(encoding="utf-8")
    assert "statman_web_entrypoint.sh" in railway
    assert "Dockerfile.dashboard" in railway
