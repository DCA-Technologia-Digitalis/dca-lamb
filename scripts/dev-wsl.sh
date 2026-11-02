#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${DV_WSL_VENV:-${PROJECT_DIR}/.venv-wsl}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_DIR}/requirements.txt"
mkdir -p "${PROJECT_DIR}/data"

export DV_DATABASE_PATH="${DV_DATABASE_PATH:-${PROJECT_DIR}/data/dv-wsl.sqlite3}"
export SECRET_KEY="${SECRET_KEY:-$(${VENV_DIR}/bin/python -c 'import secrets; print(secrets.token_hex(32))')}"
export SESSION_COOKIE_SECURE=false
export PORT="${PORT:-5001}"
export REDIS_URL=""
export RATELIMIT_STORAGE_URL="memory://"

cd "${PROJECT_DIR}"
exec "${VENV_DIR}/bin/python" -m flask --app app run \
  --host "${DV_DEV_HOST:-127.0.0.1}" --port "${PORT}"
