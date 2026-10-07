#!/bin/sh
# Portal answers /readyz itself (no Streamlit upstream required).
set -eu
PORT="${PORT:?PORT missing}"
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/readyz', timeout=3)"
