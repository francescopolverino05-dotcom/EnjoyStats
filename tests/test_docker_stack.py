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
    assert "streamlit" in dashboard or "requirements.txt" in dashboard
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "opencv-python-headless" in requirements
    assert "yt-dlp" in requirements
    assert "ultralytics" in requirements
    streamlit_cfg = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "maxUploadSize = 5120" in streamlit_cfg
    assert "maxMessageSize = 5120" in streamlit_cfg
    assert "PORT" in web_entry
    assert "streamlit run" in web_entry
    assert "api.portal" in web_entry
    assert "COPY api ./api" in dashboard
    assert "/src/api" in dashboard
    assert "statman_web_entrypoint.sh" in dashboard
    assert "ENJOYSTATS_SAME_ORIGIN_UPLOAD=1" in dashboard
    assert "api.upload_app:app" in dashboard
    assert "STATMAN_EMBED_WORKER=1" in dashboard
    assert "requirements.txt" in dashboard
    assert "ENV PORT=" not in dashboard


def test_railway_web_entrypoint_runs_on_port() -> None:
    script = (ROOT / "scripts" / "statman_web_entrypoint.sh").read_text(encoding="utf-8")
    assert "PORT" in script
    assert "streamlit run" in script
    assert "api.portal" in script
    assert "upload_app" in script or "UVICORN_APP" in script
    assert "collect_worker" in script
    assert "boot begin" in script
    assert "exit 1" not in script  # missing volume must not crash-loop the site
    assert "su " not in script
    railway = (ROOT / "railway.toml").read_text(encoding="utf-8")
    assert "statman_web_entrypoint.sh" in railway
    assert "Dockerfile.dashboard" in railway
    assert 'healthcheckPath = "/readyz"' in railway
