#!/usr/bin/env bash
set -euo pipefail

database_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$database_dir/.." && pwd)"
env_file="$project_dir/.env"
schema_path="$database_dir/schema.sql"

if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
fi

: "${MYSQL_HOST:=127.0.0.1}"
: "${MYSQL_PORT:=3306}"
: "${MYSQL_DATABASE:=plate_access_control}"
: "${MYSQL_USER:=gatekeeper}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD must be configured in .env or the environment}"

if ! command -v mysql >/dev/null 2>&1; then
    echo "The MySQL client is required to run this helper."
    exit 1
fi

MYSQL_PWD="$MYSQL_PASSWORD" mysql \
    --host="$MYSQL_HOST" \
    --port="$MYSQL_PORT" \
    --user="$MYSQL_USER" \
    "$MYSQL_DATABASE" < "$schema_path"

echo "MySQL schema ready: $MYSQL_DATABASE on $MYSQL_HOST:$MYSQL_PORT"
