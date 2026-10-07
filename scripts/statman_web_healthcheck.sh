#!/bin/sh
# Portal proxies /_stcore/health to Streamlit.
set -eu
PORT="${PORT:?PORT missing}"
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/_stcore/health', timeout=3)"
