#!/usr/bin/env bash
set -euo pipefail

web_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$web_dir/.." && pwd)"
venv_dir="$project_dir/.web-venv"
python_command="${PLATE_PYTHON:-python3}"
if ! "$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    if [[ -x /opt/python-3.11.15/bin/python3.11 ]]; then
        python_command=/opt/python-3.11.15/bin/python3.11
    else
        echo "Python 3.10 or newer is required." >&2
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
exec "$venv_dir/bin/waitress-serve" --listen=0.0.0.0:8080 app:app
