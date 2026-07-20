#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pid_file="$project_dir/Output/web.pid"

if [[ -f "$pid_file" ]]; then
    web_pid="$(<"$pid_file")"
    if [[ "$web_pid" =~ ^[0-9]+$ ]] && kill -0 "$web_pid" 2>/dev/null; then
        kill "$web_pid"
        for _ in {1..20}; do
            kill -0 "$web_pid" 2>/dev/null || break
            sleep 0.1
        done
    fi
fi

while read -r web_pid; do
    if [[ "$web_pid" =~ ^[0-9]+$ ]] && kill -0 "$web_pid" 2>/dev/null; then
        kill "$web_pid"
    fi
done < <(pgrep -f "$project_dir/.web-venv/bin/waitress-serve" || true)

rm -rf "$project_dir/.web-venv" "$project_dir/Output"
rm -f "$project_dir/database/web_secret.key" "$project_dir/database/reader_api.key"

if [[ "${1:-}" == "--delete-data" ]]; then
    echo "This permanently deletes the plate_access_control database and local gatekeeper account."
    read -r -p "Type DELETE to continue: " confirmation
    if [[ "$confirmation" != "DELETE" ]]; then
        echo "Database deletion cancelled."
        exit 1
    fi
    mysql -uroot -e "DROP DATABASE IF EXISTS plate_access_control; DROP USER IF EXISTS 'gatekeeper'@'127.0.0.1';"
    rm -f "$project_dir/.env"
    echo "Website runtime, MySQL database, MySQL account, and local secrets removed."
else
    echo "Website runtime removed. MySQL data and .env were preserved."
    echo "Use ./remove_local_setup.sh --delete-data to permanently delete them too."
fi
