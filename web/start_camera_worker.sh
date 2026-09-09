#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON_BIN:-$project_dir/.web-venv/bin/python}"

if [[ ! -x "$python_bin" ]]; then
    python_bin="${PYTHON_BIN:-python3}"
fi

exec "$python_bin" "$project_dir/web/camera_worker.py" "$@"
