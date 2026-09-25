"""Structural checks for the one-click local runner."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_local.sh"


def test_run_local_script_is_executable_posix_bash() -> None:
    assert SCRIPT.is_file()
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "run_local.sh must be executable out of the box"
    completed = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_run_local_script_covers_stack_phases() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "docker" in text
    assert "docker compose" in text or "docker-compose" in text
    assert ".venv" in text
    assert "requirements.txt" in text
    assert "pip install" in text
    assert "enjoystats-db" in text
    assert "5432" in text
    assert "postgres_tables.sql" in text
    assert "uvicorn" in text
    assert "8000" in text
    assert "/docs" in text
    assert "streamlit" in text
    assert "8501" in text
    assert "maxUploadSize=5120" in text
    assert "maxMessageSize=5120" in text
    assert "--server.address=0.0.0.0" in text
    assert "Phone / tablet / other computer" in text
    assert "upload-film" in text
    assert "inbox" in text
    assert "trap cleanup INT TERM EXIT" in text
    assert "http://localhost:8000/docs" in text
    assert "http://localhost:8501" in text
    assert "CTRL+C" in text
    assert "compose stop db" in text


def test_streamlit_config_raises_upload_limits() -> None:
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "maxUploadSize = 5120" in config
    assert "maxMessageSize = 5120" in config
    assert 'address = "0.0.0.0"' in config
    assert "enableXsrfProtection = false" in config
