#!/usr/bin/env bash
set -euo pipefail

web_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$web_dir/.." && pwd)"
venv_dir="$project_dir/.web-venv"
if [[ -n "${PLATE_PYTHON:-}" ]]; then
    python_command="$PLATE_PYTHON"
elif command -v python3.11 >/dev/null 2>&1; then
    python_command=python3.11
else
    python_command=python3
fi
if ! "$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
    if [[ -x /opt/python-3.11.15/bin/python3.11 ]]; then
        python_command=/opt/python-3.11.15/bin/python3.11
    else
        echo "Python 3.9 or newer is required." >&2
        exit 1
    fi
fi
platform_id="$(uname -s)-$(uname -m)-$("$python_command" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
platform_file="$venv_dir/.platform-id"

if [[ ! -x "$venv_dir/bin/waitress-serve" ]] ||
   [[ ! -f "$platform_file" ]] ||
   [[ "$(<"$platform_file")" != "$platform_id" ]]; then
    PLATE_PYTHON="$python_command" "$web_dir/setup_web.sh"
fi

cd "$web_dir"
echo "Admin website: http://0.0.0.0:8080"
"$venv_dir/bin/python" "$web_dir/recognition_preflight.py"
worker_pid=""
web_pid=""
startup_timeout="${PLATE_STARTUP_TIMEOUT_SECONDS:-120}"

cleanup() {
    if [[ -n "$worker_pid" ]]; then
        kill "$worker_pid" 2>/dev/null || true
        wait "$worker_pid" 2>/dev/null || true
    fi
    if [[ -n "$web_pid" ]]; then
        kill "$web_pid" 2>/dev/null || true
        wait "$web_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

"$venv_dir/bin/waitress-serve" --listen=0.0.0.0:8080 app:app &
web_pid=$!

if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required to verify that the web server is ready." >&2
    exit 1
fi
for ((attempt = 1; attempt <= startup_timeout; attempt++)); do
    if curl --silent --show-error --fail --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$web_pid" 2>/dev/null; then
        echo "The web server exited before becoming ready." >&2
        wait "$web_pid" || true
        exit 1
    fi
    sleep 1
done
if ! curl --silent --show-error --fail --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "The web server did not become ready within ${startup_timeout} seconds." >&2
    exit 1
fi

if [[ "${CAMERA_WORKER_ENABLED:-1}" != "0" ]]; then
    "$venv_dir/bin/python" "$web_dir/camera_worker.py" --poll-seconds "${CAMERA_WORKER_POLL_SECONDS:-0.5}" &
    worker_pid=$!
    echo "Central camera worker started (PID $worker_pid)."
fi

wait "$web_pid"
