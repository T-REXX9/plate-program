#!/usr/bin/env bash
set -euo pipefail

repository_url="${PLATE_PROGRAM_REPOSITORY:-https://github.com/T-REXX9/plate-program.git}"
branch="${PLATE_PROGRAM_BRANCH:-main}"
label="com.gatekeeper.plate-program"
refresh=0
[[ "${1:-}" == "--refresh" ]] && refresh=1

case "$(uname -s)" in
    Darwin)
        platform="macos"
        if [[ $EUID -eq 0 ]]; then
            echo "On macOS, run this installer as your normal account, without sudo." >&2
            exit 1
        fi
        project_dir="/usr/local/plate-program"
        current_user="$(id -un)"
        current_group="$(id -gn)"
        plist="$HOME/Library/LaunchAgents/$label.plist"
        log_file="$HOME/Library/Logs/plate-program.log"
        ;;
    Linux)
        platform="linux"
        if [[ $EUID -ne 0 ]]; then
            exec sudo bash "$0" "$@"
        fi
        if [[ ! -r /etc/os-release ]]; then
            echo "Ubuntu 22.04 or newer is required." >&2
            exit 1
        fi
        # shellcheck disable=SC1091
        source /etc/os-release
        if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *ubuntu* ]]; then
            echo "Automatic Linux installation currently supports Ubuntu 22.04 or newer." >&2
            echo "Use macOS or Ubuntu so the installer can guarantee Oracle MySQL rather than MariaDB." >&2
            exit 1
        fi
        if ! dpkg --compare-versions "${VERSION_ID:-0}" ge 22.04; then
            echo "Ubuntu 22.04 or newer is required." >&2
            exit 1
        fi
        project_dir="/opt/plate-program"
        service_user="plateprogram"
        service_name="plate-program.service"
        ;;
    *)
        echo "This installer supports macOS and Ubuntu Linux." >&2
        exit 1
        ;;
esac

echo
echo "Plate Program — complete website and MySQL setup"
echo

if [[ "$platform" == "macos" && -f "$plist" ]]; then
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
elif [[ "$platform" == "linux" && -f /etc/systemd/system/plate-program.service ]]; then
    systemctl stop plate-program.service 2>/dev/null || true
fi

if [[ "$platform" == "macos" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
        echo "Installing Homebrew..."
        NONINTERACTIVE=1 /bin/bash -c \
            "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    brew install git python
    if ! command -v mysql >/dev/null 2>&1; then
        brew install mysql
    fi
    if brew list --versions mysql >/dev/null 2>&1; then
        brew services start mysql
    fi
else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        ca-certificates curl git openssl python3 python3-venv python3-pip \
        mysql-server mysql-client
    systemctl enable --now mysql
fi

if ((refresh == 0)); then
    if [[ "$platform" == "macos" ]]; then
        sudo install -d -o "$current_user" -g "$current_group" -m 755 "$project_dir"
    fi
    if [[ -d "$project_dir/.git" ]]; then
        echo "The plate-program repository already exists; updating it."
        git -C "$project_dir" fetch origin "$branch"
        git -C "$project_dir" merge --ff-only "origin/$branch"
    else
        rm -rf "$project_dir"
        git clone --branch "$branch" --single-branch "$repository_url" "$project_dir"
    fi
elif [[ ! -d "$project_dir/.git" ]]; then
    echo "The managed plate-program repository is missing from $project_dir." >&2
    exit 1
fi

env_file="$project_dir/.env"
if ((refresh == 0)); then
    echo "Waiting for MySQL to become ready..."
    mysql_ready=0
    for _ in {1..30}; do
        if mysqladmin ping --silent >/dev/null 2>&1 ||
           mysqladmin ping --silent --user=root >/dev/null 2>&1; then
            mysql_ready=1
            break
        fi
        sleep 1
    done
    if ((mysql_ready == 0)); then
        echo "MySQL did not start. Start the installed MySQL service, then rerun this installer." >&2
        exit 1
    fi

    if [[ -f "$env_file" ]]; then
        # shellcheck disable=SC1090
        source "$env_file"
        database_password="${MYSQL_PASSWORD:?MYSQL_PASSWORD is missing from .env}"
        echo "Existing database credentials were preserved."
    else
        database_password="$(openssl rand -hex 32)"
    fi

    root_password=""
    if mysql --protocol=socket --user=root --batch --skip-column-names \
        --execute='SELECT 1' >/dev/null 2>&1; then
        root_auth="socket"
    else
        read -r -s -p "MySQL root password: " root_password
        echo
        if ! MYSQL_PWD="$root_password" mysql --protocol=socket --user=root \
            --batch --skip-column-names --execute='SELECT 1' >/dev/null 2>&1; then
            echo "The MySQL root password was not accepted." >&2
            exit 1
        fi
        root_auth="password"
    fi

    root_mysql() {
        if [[ "$root_auth" == "password" ]]; then
            MYSQL_PWD="$root_password" mysql --protocol=socket --user=root "$@"
        else
            mysql --protocol=socket --user=root "$@"
        fi
    }

    root_mysql <<SQL
CREATE DATABASE IF NOT EXISTS plate_access_control
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'gatekeeper'@'127.0.0.1'
  IDENTIFIED BY '$database_password';
ALTER USER 'gatekeeper'@'127.0.0.1'
  IDENTIFIED BY '$database_password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE VIEW, SHOW VIEW
  ON plate_access_control.* TO 'gatekeeper'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

    umask 077
    printf '%s\n' \
        'MYSQL_HOST=127.0.0.1' \
        'MYSQL_PORT=3306' \
        'MYSQL_DATABASE=plate_access_control' \
        'MYSQL_USER=gatekeeper' \
        "MYSQL_PASSWORD=$database_password" \
        'MYSQL_TIME_ZONE=+08:00' > "$env_file"
elif [[ ! -f "$env_file" ]]; then
    echo "Database configuration is missing. Rerun the full installer." >&2
    exit 1
fi

mkdir -p "$project_dir/Output" "$project_dir/database"
if [[ ! -s "$project_dir/database/web_secret.key" ]]; then
    umask 077
    openssl rand -hex 32 > "$project_dir/database/web_secret.key"
fi
rm -f "$project_dir/database/reader_api.key"
chmod 600 "$env_file" "$project_dir/database/web_secret.key"

echo "Installing the website environment and applying database migrations..."
bash "$project_dir/web/setup_web.sh"
bash "$project_dir/database/init_database.sh"

if ((refresh == 0)); then
    "$project_dir/.web-venv/bin/python" "$project_dir/web/create_admin.py"
fi

if [[ "$platform" == "linux" ]]; then
    getent group "$service_user" >/dev/null || groupadd --system "$service_user"
    if ! id "$service_user" >/dev/null 2>&1; then
        useradd --system --gid "$service_user" --no-create-home \
            --shell /usr/sbin/nologin "$service_user"
    fi
    chown -R "$service_user:$service_user" "$project_dir/Output"
    chown root:"$service_user" "$env_file" \
        "$project_dir/database/web_secret.key"
    chmod 640 "$env_file" "$project_dir/database/web_secret.key"

    cat > /etc/systemd/system/plate-program.service <<EOF
[Unit]
Description=Plate Access Control Web Program
Wants=network-online.target
After=network-online.target mysql.service

[Service]
Type=simple
User=$service_user
Group=$service_user
WorkingDirectory=$project_dir
ExecStart=/usr/bin/stdbuf -oL -eL $project_dir/web/start_web.sh
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
    install -o root -g root -m 755 "$project_dir/program" /usr/local/bin/program
    systemctl daemon-reload
    systemctl enable --now "$service_name"
    if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
        ufw allow 8080/tcp >/dev/null
    fi
else
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array><string>$project_dir/web/start_web.sh</string></array>
    <key>WorkingDirectory</key><string>$project_dir</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$log_file</string>
    <key>StandardErrorPath</key><string>$log_file</string>
</dict>
</plist>
EOF
    sudo install -o root -g wheel -m 755 "$project_dir/program" /usr/local/bin/program
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$plist"
    launchctl kickstart -k "gui/$UID/$label"
fi

echo "Checking the website..."
healthy=0
for _ in {1..30}; do
    response="$(curl --silent --connect-timeout 1 --max-time 2 \
        http://127.0.0.1:8080/health 2>/dev/null || true)"
    compact="$(printf '%s' "$response" | tr -d '[:space:]')"
    if [[ "$compact" == *'"service":"plate-program"'* &&
          "$compact" == *'"status":"ok"'* ]]; then
        healthy=1
        break
    fi
    sleep 1
done
if ((healthy == 0)); then
    echo "Installation completed, but the website health check failed." >&2
    echo "Run 'program -logs' to see the cause." >&2
    exit 1
fi

if [[ "$platform" == "macos" ]]; then
    local_ip=""
    for interface in en0 en1; do
        local_ip="$(ipconfig getifaddr "$interface" 2>/dev/null || true)"
        [[ -n "$local_ip" ]] && break
    done
else
    local_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

echo
echo "Plate program installation complete."
echo "Website on this computer: http://localhost:8080"
[[ -n "$local_ip" ]] && echo "Website on the local network: http://$local_ip:8080"
echo "Future updates require only: program -update"
