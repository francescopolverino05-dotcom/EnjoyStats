#!/bin/sh
# Used by Dockerfile.dashboard HEALTHCHECK (portal on $PORT).
set -eu
PORT="${PORT:-8501}"
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/_stcore/health', timeout=3)"
