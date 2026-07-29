#!/usr/bin/env bash
set -euo pipefail

repository_url="${PLATE_PROGRAM_REPOSITORY:-https://github.com/T-REXX9/plate-program.git}"
branch="${PLATE_PROGRAM_BRANCH:-main}"
label="com.gatekeeper.plate-program"
refresh=0
[[ "${1:-}" == "--refresh" ]] && refresh=1

retry() {
    local attempts="$1"
    local delay="$2"
    shift 2
    local attempt=1
    until "$@"; do
        if ((attempt >= attempts)); then
            echo "Command failed after $attempt attempts: $*" >&2
            return 1
        fi
        echo "Temporary failure. Retrying in $delay seconds ($attempt/$attempts)..."
        sleep "$delay"
        attempt=$((attempt + 1))
    done
}

report_failure() {
    local exit_code="$?"
    local line_number="${BASH_LINENO[0]:-unknown}"
    echo >&2
    echo "Setup could not finish (line $line_number, exit code $exit_code)." >&2
    echo "It is safe to rerun this installer; completed steps will be reused." >&2
    exit "$exit_code"
}
trap report_failure ERR

repair_google_apt_key() {
    local source_files=()
    local file
    while IFS= read -r -d '' file; do
        if grep -Eqs '^[[:space:]]*(deb|Types:).*dl\.google\.com/linux/chrome/deb' "$file" ||
           grep -Eqs '^[[:space:]]*(URIs:).*dl\.google\.com/linux/chrome/deb' "$file"; then
            source_files+=("$file")
        fi
    done < <(find /etc/apt -maxdepth 3 -type f \
        \( -name '*.list' -o -name '*.sources' \) -print0)

    ((${#source_files[@]} > 0)) || return 0
    echo "Refreshing the official Google Linux repository signing key..."
    local key_file
    key_file="$(mktemp /var/tmp/google-linux-key.XXXXXX)"
    if ! retry 4 5 curl -fsSL --connect-timeout 15 --max-time 120 \
        https://dl.google.com/linux/linux_signing_key.pub -o "$key_file"; then
        rm -f "$key_file"
        echo "Google's signing key could not be downloaded." >&2
        return 1
    fi
    if ! grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$key_file"; then
        rm -f "$key_file"
        echo "Google returned an invalid signing-key file." >&2
        return 1
    fi
    local canonical_key="/etc/apt/keyrings/google-linux-signing-key.asc"
    install -d -m 755 /etc/apt/keyrings /etc/apt/trusted.gpg.d
    install -o root -g root -m 644 "$key_file" "$canonical_key"
    install -o root -g root -m 644 "$key_file" \
        /etc/apt/trusted.gpg.d/google-linux-signing-key.asc

    # A Signed-By setting overrides the global trusted keyring. Point Chrome's
    # source directly at the refreshed official key so both old and new source
    # formats work.
    local rewritten
    for file in "${source_files[@]}"; do
        rewritten="$(mktemp /var/tmp/google-source.XXXXXX)"
        if [[ "$file" == *.list ]]; then
            awk -v key="$canonical_key" '
                /^[[:space:]]*deb[[:space:]].*dl\.google\.com\/linux\/chrome\/deb/ {
                    if ($0 ~ /signed-by=/) {
                        sub(/signed-by=[^] ]+/, "signed-by=" key)
                    } else if ($0 ~ /\[[^]]*\]/) {
                        sub(/\]/, " signed-by=" key "]")
                    } else {
                        sub(/^[[:space:]]*deb[[:space:]]+/,
                            "&[signed-by=" key "] ")
                    }
                }
                { print }
            ' "$file" > "$rewritten"
        else
            awk -v key="$canonical_key" '
                /^Signed-By:/ { print "Signed-By: " key; next }
                { print }
                END {
                    # Chrome-generated Deb822 files normally include Signed-By.
                    # The globally trusted copy covers a file that omits it.
                }
            ' "$file" > "$rewritten"
        fi
        cat "$rewritten" > "$file"
        rm -f "$rewritten"
    done
    rm -f "$key_file"
}

ensure_official_ubuntu_sources() {
    local codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    if [[ -z "$codename" ]]; then
        echo "Ubuntu's release codename is missing from /etc/os-release." >&2
        return 1
    fi

    local architecture archive_url security_url
    architecture="$(dpkg --print-architecture)"
    case "$architecture" in
        amd64|i386)
            archive_url="https://archive.ubuntu.com/ubuntu"
            security_url="https://security.ubuntu.com/ubuntu"
            ;;
        arm64|armhf|ppc64el|riscv64|s390x)
            archive_url="https://ports.ubuntu.com/ubuntu-ports"
            security_url="$archive_url"
            ;;
        *)
            echo "Unsupported Ubuntu architecture: $architecture" >&2
            return 1
            ;;
    esac

    # This dedicated source makes the required main and universe packages
    # available even when a desktop image has incomplete or disabled sources.
    cat > /etc/apt/sources.list.d/plate-program-ubuntu.list <<EOF
# Managed by the Plate Program installer.
deb $archive_url $codename main restricted universe multiverse
deb $archive_url $codename-updates main restricted universe multiverse
deb $security_url $codename-security main restricted universe multiverse
EOF
}

verify_apt_candidate() {
    local package="$1"
    local candidate
    candidate="$(apt-cache policy "$package" |
        awk '/Candidate:/ {print $2; exit}')"
    if [[ -z "$candidate" || "$candidate" == "(none)" ]]; then
        echo "Ubuntu did not provide an installation candidate for '$package'." >&2
        echo "The configured release is ${UBUNTU_CODENAME:-${VERSION_CODENAME:-unknown}}." >&2
        return 1
    fi
}

has_apt_candidate() {
    local candidate
    candidate="$(apt-cache policy "$1" |
        awk '/Candidate:/ {print $2; exit}')"
    [[ -n "$candidate" && "$candidate" != "(none)" ]]
}

install_python_311() {
    local version="3.11.15"
    local prefix="/opt/python-$version"
    local executable="$prefix/bin/python3.11"
    local archive="Python-$version.tar.xz"
    local checksum="272179ddd9a2e41a0fc8e42e33dfbdca0b3711aa5abf372d3f2d51543d09b625"

    if [[ -x "$executable" ]] &&
       "$executable" -c 'import ensurepip, ssl, sqlite3, venv' >/dev/null 2>&1; then
        python_command="$executable"
        return
    fi

    echo "Ubuntu's Python is older than 3.10."
    echo "Installing official Python $version alongside the system Python..."
    apt_install \
        build-essential xz-utils libssl-dev zlib1g-dev \
        libncurses-dev libreadline-dev libsqlite3-dev \
        libgdbm-dev libdb5.3-dev libbz2-dev libexpat1-dev liblzma-dev \
        libffi-dev uuid-dev

    local available_kb
    available_kb="$(df -Pk /opt | awk 'NR == 2 {print $4}')"
    if [[ ! "$available_kb" =~ ^[0-9]+$ ]] || ((available_kb < 1572864)); then
        echo "At least 1.5 GB of free space is required to build Python." >&2
        echo "Free some space on the system drive, then rerun this installer." >&2
        return 1
    fi

    (
        set -euo pipefail
        local work_dir
        work_dir="$(mktemp -d /var/tmp/plate-python.XXXXXX)"
        trap 'rm -rf "$work_dir"' EXIT
        cd "$work_dir"
        retry 4 5 curl -fL --connect-timeout 15 --max-time 600 \
            --retry 3 \
            "https://www.python.org/ftp/python/$version/$archive" \
            -o "$archive"
        printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check -
        tar -xJf "$archive"
        cd "Python-$version"
        ./configure --prefix="$prefix" --with-ensurepip=install
        make -j2
        rm -rf "$prefix"
        make altinstall
    )

    if [[ ! -x "$executable" ]] ||
       ! "$executable" -c 'import ensurepip, ssl, sqlite3, venv' >/dev/null 2>&1; then
        rm -rf "$prefix"
        echo "Python $version did not pass its post-install verification." >&2
        return 1
    fi
    python_command="$executable"
}

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
            echo "Ubuntu 20.04 or newer is required." >&2
            exit 1
        fi
        # shellcheck disable=SC1091
        source /etc/os-release
        if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *ubuntu* ]]; then
            echo "Automatic Linux installation currently supports Ubuntu 20.04 or newer." >&2
            echo "Use macOS or Ubuntu so the installer can guarantee Oracle MySQL rather than MariaDB." >&2
            exit 1
        fi
        if ! dpkg --compare-versions "${VERSION_ID:-0}" ge 20.04; then
            echo "Ubuntu 20.04 or newer is required." >&2
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
    retry 3 5 brew install git python
    if ! command -v mysql >/dev/null 2>&1; then
        retry 3 5 brew install mysql
    fi
    if brew list --versions mysql >/dev/null 2>&1; then
        brew services start mysql
    fi
    python_command="$(command -v python3)"
else
    export DEBIAN_FRONTEND=noninteractive
    apt_get() {
        apt-get \
            -o Acquire::Retries=3 \
            -o Acquire::http::Timeout=45 \
            -o Acquire::https::Timeout=45 \
            "$@"
    }
    # A half-configured package may need refreshed indexes before apt can
    # repair its dependencies, so do not abort on this first best-effort pass.
    dpkg --configure -a || true
    repair_google_apt_key
    retry 4 5 apt_get update
    if ! has_apt_candidate python3 ||
       ! has_apt_candidate mysql-server ||
       ! has_apt_candidate default-mysql-client; then
        echo "Required Ubuntu package indexes are missing; repairing Ubuntu sources..."
        ensure_official_ubuntu_sources
        retry 4 5 apt_get update
    fi
    retry 3 5 apt_get --fix-broken install -y
    dpkg --configure -a
    apt_install() {
        retry 3 5 apt_get install -y --no-install-recommends "$@"
    }
    verify_apt_candidate python3
    verify_apt_candidate mysql-server
    verify_apt_candidate default-mysql-client
    apt_install \
        ca-certificates curl git openssl python3 \
        mysql-server default-mysql-client
    systemctl enable --now mysql
    python_command=python3
    if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
        install_python_311
    else
        verify_apt_candidate python3-venv
        apt_install python3-venv
    fi
fi

if ((refresh == 0)); then
    if [[ "$platform" == "macos" ]]; then
        sudo install -d -o "$current_user" -g "$current_group" -m 755 "$project_dir"
    fi
    if [[ -d "$project_dir/.git" ]]; then
        echo "The plate-program repository already exists; updating it."
        retry 3 5 git -C "$project_dir" fetch origin "$branch"
        git -C "$project_dir" merge --ff-only "origin/$branch"
    else
        rm -rf "$project_dir"
        retry 3 5 git clone --branch "$branch" --single-branch \
            "$repository_url" "$project_dir"
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
        'MYSQL_TIME_ZONE=+08:00' \
        'MOBILE_ACCOUNT_INTEGRATION_ENABLED=0' \
        'MOBILE_ACCOUNT_SERVICE_URL=' \
        'MOBILE_ACCOUNT_SITE_ID=' \
        'MOBILE_ACCOUNT_SYNC_SECRET=' > "$env_file"
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
# Secret generation above uses a restrictive umask. Restore normal directory
# permissions before creating the virtual environment used by the service user.
umask 022

echo "Installing the website environment and applying database migrations..."
PLATE_PYTHON="$python_command" bash "$project_dir/web/setup_web.sh"
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
    echo "Recent service diagnostics:" >&2
    if [[ "$platform" == "linux" ]]; then
        systemctl --no-pager --full status "$service_name" >&2 || true
        journalctl -u "$service_name" -n 60 --no-pager >&2 || true
    else
        tail -n 60 "$log_file" >&2 || true
    fi
    echo "Correct the reported external problem, then rerun this installer." >&2
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
