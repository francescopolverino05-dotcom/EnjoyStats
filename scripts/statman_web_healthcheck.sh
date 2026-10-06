#!/bin/sh
# Healthcheck for diagnostic server (and later Streamlit).
set -eu
PORT="${PORT:-8501}"
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/', timeout=3)"
