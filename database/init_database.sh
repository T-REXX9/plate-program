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

ensure_column() {
    local table="$1"
    local column="$2"
    local definition="$3"
    local present
    present="$(MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" --batch --skip-column-names \
        --execute="SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = '$table' AND column_name = '$column'")"
    if [[ "$present" == "0" ]]; then
        MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" \
            --execute="ALTER TABLE $table ADD COLUMN $column $definition"
    fi
}

ensure_index() {
    local table="$1"
    local index="$2"
    local definition="$3"
    local present
    present="$(MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" --batch --skip-column-names \
        --execute="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = '$table' AND index_name = '$index'")"
    if [[ "$present" == "0" ]]; then
        MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" \
            --execute="ALTER TABLE $table ADD $definition"
    fi
}

ensure_column access_events rfid_number 'VARCHAR(64) NULL AFTER plate_number'
ensure_column access_events rfid_required 'TINYINT(1) NOT NULL DEFAULT 0 AFTER rfid_number'
ensure_column access_events rfid_authorized 'TINYINT(1) NOT NULL DEFAULT 0 AFTER rfid_required'
ensure_column access_events raw_image_path 'VARCHAR(500) NULL AFTER image_path'
ensure_column access_events annotated_image_path 'VARCHAR(500) NULL AFTER raw_image_path'
ensure_column system_status last_rfid 'VARCHAR(64) NULL AFTER last_plate'
ensure_index access_events idx_access_events_rfid 'KEY idx_access_events_rfid (rfid_number, detected_at DESC)'

# Move values created by the first RFID prototype into the normalized sticker
# table. The legacy vehicles.rfid_number column is intentionally left in place
# on upgraded installations so this migration remains non-destructive.
legacy_rfid_column="$(MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" --batch --skip-column-names \
    --execute="SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'vehicles' AND column_name = 'rfid_number'")"
if [[ "$legacy_rfid_column" != "0" ]]; then
    MYSQL_PWD="$MYSQL_PASSWORD" "${mysql_command[@]}" --execute="
        INSERT IGNORE INTO rfid_stickers (vehicle_id, sticker_value)
        SELECT id, rfid_number FROM vehicles
        WHERE rfid_number IS NOT NULL AND rfid_number <> ''
    "
fi

echo "MySQL schema ready: $MYSQL_DATABASE on $MYSQL_HOST:$MYSQL_PORT"
