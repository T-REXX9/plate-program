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

mysql_command=(
    mysql
    --host="$MYSQL_HOST"
    --port="$MYSQL_PORT"
    --user="$MYSQL_USER"
    "$MYSQL_DATABASE"
)

MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" < "$schema_path"

for timing_column in frames_ms yolo_ms ocr_ms server_ms total_ms; do
    present="$(MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" --batch --skip-column-names \
        --execute="SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'reader_commands' AND column_name = '$timing_column'")"
    if [[ "$present" == "0" ]]; then
        MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" \
            --execute="ALTER TABLE reader_commands ADD COLUMN $timing_column INT UNSIGNED NULL"
    fi
done

echo "MySQL schema ready: $MYSQL_DATABASE on $MYSQL_HOST:$MYSQL_PORT"
