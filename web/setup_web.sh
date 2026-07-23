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

if [[ ! -f "$platform_file" ]] || [[ "$(<"$platform_file")" != "$platform_id" ]]; then
    "$python_command" -m venv --clear "$venv_dir"
elif [[ ! -x "$venv_dir/bin/python" ]]; then
    "$python_command" -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install --upgrade pip \
    --retries 5 --timeout 60
"$venv_dir/bin/python" -m pip install -r "$web_dir/requirements.txt" \
    --retries 5 --timeout 60
echo "$platform_id" > "$platform_file"

echo "Website environment ready."
