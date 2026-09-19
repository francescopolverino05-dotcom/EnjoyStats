#!/usr/bin/env bash
# EnjoyStats — single-click localhost stack (Postgres + FastAPI + Streamlit).
#
# Usage (from the repository root):
#   ./run_local.sh
#
# CTRL+C stops Uvicorn, Streamlit, and the local Docker PostgreSQL container.

set -eu

if (set -o pipefail) 2>/dev/null; then
    set -o pipefail
fi

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

API_HOST="127.0.0.1"
API_PORT="8000"
UI_PORT="8501"
DB_PORT="5432"
DB_CONTAINER="enjoystats-db"
DB_USER="enjoystats"
DB_NAME="enjoystats"
SCHEMA_FILE="$ROOT/storage/postgres_tables.sql"
LOG_DIR="$ROOT/.local-run"
API_LOG="$LOG_DIR/uvicorn.log"
UI_LOG="$LOG_DIR/streamlit.log"
API_PID=""
UI_PID=""
TAIL_PID=""
CLEANING=0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

if [ -t 1 ]; then
    C_RESET="$(printf '\033[0m')"
    C_BOLD="$(printf '\033[1m')"
    C_CYAN="$(printf '\033[36m')"
    C_GREEN="$(printf '\033[32m')"
    C_YELLOW="$(printf '\033[33m')"
    C_RED="$(printf '\033[31m')"
    C_DIM="$(printf '\033[2m')"
else
    C_RESET=""
    C_BOLD=""
    C_CYAN=""
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
    C_DIM=""
fi

ts() {
    date "+%H:%M:%S"
}

log() {
    printf "%s[%s]%s %s\n" "$C_DIM" "$(ts)" "$C_RESET" "$*"
}

phase() {
    printf "\n%s==> %s%s\n" "$C_BOLD$C_CYAN" "$*" "$C_RESET"
}

ok() {
    printf "%s[%s]%s %sOK%s  %s\n" "$C_DIM" "$(ts)" "$C_RESET" "$C_GREEN" "$C_RESET" "$*"
}

warn() {
    printf "%s[%s]%s %sWARN%s %s\n" "$C_DIM" "$(ts)" "$C_RESET" "$C_YELLOW" "$C_RESET" "$*" >&2
}

die() {
    printf "%s[%s]%s %sERROR%s %s\n" "$C_DIM" "$(ts)" "$C_RESET" "$C_RED" "$C_RESET" "$*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Docker Compose helper (plugin v2 or legacy docker-compose)
# ---------------------------------------------------------------------------

compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

# ---------------------------------------------------------------------------
# Process / port helpers
# ---------------------------------------------------------------------------

stop_pid() {
    _pid="${1:-}"
    _name="${2:-process}"
    if [ -z "$_pid" ]; then
        return 0
    fi
    if ! kill -0 "$_pid" 2>/dev/null; then
        return 0
    fi
    log "Stopping ${_name} (pid ${_pid})..."
    kill "$_pid" 2>/dev/null || true
    _waited=0
    while [ "$_waited" -lt 10 ]; do
        if ! kill -0 "$_pid" 2>/dev/null; then
            ok "${_name} stopped."
            return 0
        fi
        sleep 1
        _waited=$((_waited + 1))
    done
    warn "${_name} did not exit; sending SIGKILL."
    kill -9 "$_pid" 2>/dev/null || true
}

port_in_use() {
    _port="$1"
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$_port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(0.4)
try:
    sock.connect(("127.0.0.1", port))
except OSError:
    sys.exit(1)
else:
    sys.exit(0)
finally:
    sock.close()
PY
        return $?
    fi
    return 1
}

wait_for_http() {
    _url="$1"
    _label="$2"
    _seconds="${3:-40}"
    _n=1
    while [ "$_n" -le "$_seconds" ]; do
        if curl -sf "$_url" >/dev/null 2>&1; then
            ok "${_label} is responding at ${_url}"
            return 0
        fi
        sleep 1
        _n=$((_n + 1))
    done
    return 1
}

# ---------------------------------------------------------------------------
# Cleanup on CTRL+C / TERM / EXIT
# ---------------------------------------------------------------------------

cleanup() {
    _status=$?
    if [ "$CLEANING" -eq 1 ]; then
        return 0
    fi
    CLEANING=1
    trap - INT TERM EXIT

    _started=0
    if [ -n "${TAIL_PID}${UI_PID}${API_PID}" ]; then
        _started=1
    fi

    if [ "$_started" -eq 1 ]; then
        printf "\n"
        phase "Shutting down local stack"
        stop_pid "$TAIL_PID" "log stream"
        stop_pid "$UI_PID" "Streamlit"
        stop_pid "$API_PID" "Uvicorn"
    fi

    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        if docker inspect "$DB_CONTAINER" >/dev/null 2>&1; then
            if [ "$_started" -eq 0 ]; then
                printf "\n"
                phase "Shutting down local stack"
            fi
            log "Stopping Docker PostgreSQL container ${DB_CONTAINER}..."
            if compose stop db >/dev/null 2>&1; then
                ok "PostgreSQL container stopped (volume preserved)."
            elif docker stop "$DB_CONTAINER" >/dev/null 2>&1; then
                ok "PostgreSQL container stopped via docker stop."
            else
                warn "Could not stop ${DB_CONTAINER}."
            fi
            _started=1
        fi
    fi

    if [ "$_started" -eq 1 ]; then
        log "Local stack is down. Restart with ./run_local.sh"
    fi
    if [ "$_status" -eq 130 ] || [ "$_status" -eq 143 ]; then
        exit 0
    fi
    exit "$_status"
}

trap cleanup INT TERM EXIT

# ---------------------------------------------------------------------------
# 1. Environment & dependency validation
# ---------------------------------------------------------------------------

phase "1/4  Environment & dependency validation"

if ! command -v docker >/dev/null 2>&1; then
    die "Docker is not installed. Install Docker Engine: https://docs.docker.com/engine/install/"
fi
ok "Docker CLI found: $(command -v docker)"

if docker compose version >/dev/null 2>&1; then
    ok "Docker Compose plugin found: $(docker compose version --short 2>/dev/null || echo v2)"
elif command -v docker-compose >/dev/null 2>&1; then
    ok "docker-compose found: $(command -v docker-compose)"
else
    die "Docker Compose is not installed. Install the Compose plugin or docker-compose."
fi

if ! command -v curl >/dev/null 2>&1; then
    die "curl is required to health-check http://127.0.0.1:${API_PORT}/docs"
fi
ok "curl found: $(command -v curl)"

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    log "Active virtualenv detected: ${VIRTUAL_ENV}"
    # shellcheck disable=SC1091
    . "${VIRTUAL_ENV}/bin/activate"
    ok "Using already-active virtual environment."
elif [ -f "$ROOT/.venv/bin/activate" ]; then
    log "Found repository .venv — activating."
    # shellcheck disable=SC1091
    . "$ROOT/.venv/bin/activate"
    ok "Activated ${ROOT}/.venv"
else
    PY=""
    if command -v python3.11 >/dev/null 2>&1; then
        PY="$(command -v python3.11)"
    elif command -v python3 >/dev/null 2>&1; then
        PY="$(command -v python3)"
    else
        die "Python 3.11+ is required to create .venv (python3 not found)."
    fi
    log "No .venv found — creating one with ${PY}"
    "$PY" -m venv "$ROOT/.venv"
    # shellcheck disable=SC1091
    . "$ROOT/.venv/bin/activate"
    ok "Created and activated ${ROOT}/.venv"
fi

if ! command -v python >/dev/null 2>&1; then
    die "python is not on PATH after virtualenv activation."
fi
ok "Python $(python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

if [ ! -f "$ROOT/requirements.txt" ]; then
    die "requirements.txt is missing at ${ROOT}/requirements.txt"
fi

log "Syncing pip packages from requirements.txt (fast install of missing wheels)..."
python -m pip install --upgrade pip --disable-pip-version-check -q
python -m pip install -r "$ROOT/requirements.txt" --disable-pip-version-check -q
ok "Virtualenv packages match requirements.txt"

# ---------------------------------------------------------------------------
# 2. Background PostgreSQL via Docker
# ---------------------------------------------------------------------------

phase "2/4  Background PostgreSQL configuration"

if ! docker info >/dev/null 2>&1; then
    die "Docker daemon is not running. Start Docker Desktop / dockerd and retry."
fi
ok "Docker daemon is active."

if [ ! -f "$SCHEMA_FILE" ]; then
    die "Schema blueprint missing: ${SCHEMA_FILE}"
fi

log "Starting Postgres container (compose service 'db', port ${DB_PORT})..."
compose up -d db

log "Waiting for PostgreSQL to accept connections..."
_db_tries=1
while [ "$_db_tries" -le 60 ]; do
    if docker exec "$DB_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        ok "pg_isready: ${DB_CONTAINER} is accepting connections."
        break
    fi
    if [ "$_db_tries" -eq 60 ]; then
        die "PostgreSQL did not become ready within 60s. Check: docker logs ${DB_CONTAINER}"
    fi
    sleep 1
    _db_tries=$((_db_tries + 1))
done

_mapped="$(docker port "$DB_CONTAINER" 5432 2>/dev/null || true)"
if [ -z "$_mapped" ]; then
    die "Postgres container is running but port ${DB_PORT} is not published. Expected 5432:5432 in docker-compose.yml."
fi
ok "Port 5432 published: ${_mapped}"

if ! port_in_use "$DB_PORT"; then
    die "Nothing is listening on 127.0.0.1:${DB_PORT} after publishing the container port."
fi
ok "Host port ${DB_PORT} is reachable."

log "Applying schema blueprint storage/postgres_tables.sql (idempotent)..."
docker exec -i "$DB_CONTAINER" \
    psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
    < "$SCHEMA_FILE" >/dev/null
ok "PostgreSQL schema migrated from postgres_tables.sql"

# Host-side API/dashboard env (compose.env uses hostname 'db', which is Compose-only).
export DATABASE_URL="${DATABASE_URL:-postgresql://${DB_USER}:${DB_USER}@127.0.0.1:${DB_PORT}/${DB_NAME}}"
export ENJOYSTATS_HOST="${ENJOYSTATS_HOST:-0.0.0.0}"
export ENJOYSTATS_PORT="${ENJOYSTATS_PORT:-${API_PORT}}"
export ENJOYSTATS_API_URL="${ENJOYSTATS_API_URL:-http://${API_HOST}:${API_PORT}}"
export ENJOYSTATS_DEBUG="${ENJOYSTATS_DEBUG:-false}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED="1"
export MPLBACKEND="${MPLBACKEND:-Agg}"

mkdir -p "$LOG_DIR" "$LOG_DIR/inbox" "$LOG_DIR/uploads"

# ---------------------------------------------------------------------------
# 3. Sequential service launch
# ---------------------------------------------------------------------------

phase "3/4  Sequential service launch"

if port_in_use "$API_PORT"; then
    die "Port ${API_PORT} is already in use. Stop the other process or change ENJOYSTATS_PORT."
fi
if port_in_use "$UI_PORT"; then
    die "Port ${UI_PORT} is already in use. Stop the other Streamlit process and retry."
fi

log "Booting FastAPI (Uvicorn) on port ${API_PORT} in the background..."
python -m uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "$API_PORT" \
    --log-level info \
    >"$API_LOG" 2>&1 &
API_PID=$!
ok "Uvicorn started (pid ${API_PID}) — logs: ${API_LOG}"

log "Waiting for OpenAPI docs at http://${API_HOST}:${API_PORT}/docs ..."
if ! wait_for_http "http://${API_HOST}:${API_PORT}/docs" "FastAPI /docs" 40; then
    warn "Uvicorn failed to become ready. Last log lines:"
    tail -n 40 "$API_LOG" >&2 || true
    die "FastAPI did not respond on /docs within 40s."
fi

if ! kill -0 "$API_PID" 2>/dev/null; then
    die "Uvicorn exited unexpectedly. See ${API_LOG}"
fi

log "Launching Streamlit visualization client on port ${UI_PORT}..."
python -m streamlit run "$ROOT/app/dashboard.py" \
    --server.address=127.0.0.1 \
    --server.port="$UI_PORT" \
    --server.headless=true \
    --server.maxUploadSize=3072 \
    --server.maxMessageSize=3072 \
    --browser.gatherUsageStats=false \
    >"$UI_LOG" 2>&1 &
UI_PID=$!
ok "Streamlit started (pid ${UI_PID}) — logs: ${UI_LOG}"

log "Waiting for Streamlit at http://localhost:${UI_PORT} ..."
if ! wait_for_http "http://127.0.0.1:${UI_PORT}/_stcore/health" "Streamlit" 40; then
    if ! wait_for_http "http://127.0.0.1:${UI_PORT}" "Streamlit UI" 10; then
        warn "Streamlit did not report healthy. Last log lines:"
        tail -n 40 "$UI_LOG" >&2 || true
        die "Streamlit did not start on port ${UI_PORT}."
    fi
fi

# ---------------------------------------------------------------------------
# 4. Banner, stream logs, wait until CTRL+C
# ---------------------------------------------------------------------------

phase "4/4  Stack is live — press CTRL+C to tear down"

printf "\n"
printf "%s" "$C_CYAN"
printf "  ============================================================\n"
printf "   %sEnjoyStats — local stack is ready%s\n" "$C_BOLD" "$C_RESET$C_CYAN"
printf "  ============================================================\n"
printf "    API Docs : %shttp://localhost:8000/docs%s\n" "$C_BOLD$C_GREEN" "$C_RESET$C_CYAN"
printf "    Film up  : %shttp://localhost:8000/upload-film%s\n" "$C_BOLD$C_GREEN" "$C_RESET$C_CYAN"
printf "    UI       : %shttp://localhost:8501%s\n" "$C_BOLD$C_GREEN" "$C_RESET$C_CYAN"
printf "    Inbox    : %s${LOG_DIR}/inbox%s\n" "$C_BOLD$C_GREEN" "$C_RESET$C_CYAN"
printf "  ============================================================\n"
printf "    Press CTRL+C to stop Uvicorn, Streamlit, and PostgreSQL\n"
printf "  ============================================================\n"
printf "%s\n" "$C_RESET"

log "Streaming Uvicorn + Streamlit logs (background PIDs ${API_PID} / ${UI_PID})..."
printf "%s" "$C_DIM"
tail -n +1 -f "$API_LOG" "$UI_LOG" &
TAIL_PID=$!

# Keep the wrapper in the foreground until interrupted or a child dies.
while :; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        kill "$TAIL_PID" 2>/dev/null || true
        die "Uvicorn exited unexpectedly. See ${API_LOG}"
    fi
    if ! kill -0 "$UI_PID" 2>/dev/null; then
        kill "$TAIL_PID" 2>/dev/null || true
        die "Streamlit exited unexpectedly. See ${UI_LOG}"
    fi
    sleep 1
done
