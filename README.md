# Plate Access Control Web Server

This repository is the centralized Gatekeeper server: one native MySQL database,
authenticated controller API, server-owned camera/plate recognition, and an
administration dashboard for multiple villages, subdivisions, and gates. ESP8266
NodeMCU controllers remain focused on sensors, RFID acquisition, gate safety, and
barrier control.

No Docker or SQLite is used.

## Data flow

1. The NodeMCU detects a vehicle on the inductive loop.
2. It creates a server capture attempt and separately submits any RFID result.
3. The central camera worker captures the gate-bound camera over RTSP/TCP or a
   supported camera agent transport.
4. Server-side detection and OCR return a plate and annotated image.
5. The server combines plate and RFID evidence, checks MySQL, stores one event,
   and returns authorized or denied to the NodeMCU.

RFID is optional and is selected during `controller -configure`. The controller
places the confirmed UHFReader18-compatible device in Answer Mode, requests an
on-demand inventory over RS232, validates the binary response CRC, and sends only
the canonical EPC value. Authorization is OR-based: either an active, unexpired
plate registration or an active RFID sticker in MySQL opens the barrier. Access is
denied only when neither credential is authorized.
Binary sticker values are stored as uppercase hexadecimal without separators,
for example `3045673030553F9030553F90`.
6. The server stores the annotated camera result and keeps the attempt history,
   including late RFID or plate evidence.
7. A green/red LED-style indicator shows whether the latest access result was
   authorized or denied, alongside the server's capture and recognition status.
8. The dashboard synchronizes automatically without full-page refreshes.
9. A live traffic-light panel shows the hardware connection, camera,
   inductive loop, IR safety beam, boom barrier, and red/green traffic output.
   The NodeMCU reports these signals once per second; recognition remains on the
   server and never runs on the controller.
10. The separate **Hardware** page gives administrators confirmed diagnostic
    controls for barrier UP/DOWN and three-second red/green signal tests. Guards
    can view live indicators but cannot send hardware commands.
11. Administrators can use the RFID serial console on the Hardware page to send
    HEX bytes or plain text through `/dev/serial0`. Baud rate, data bits, parity,
    stop bits, and response timeout are selectable; replies are displayed as
    both HEX and readable text. Verified shortcuts cover single- and multi-tag
    inventory, reader information, work mode, Answer Mode, scan duration, and
    the reader's 9600-baud configuration. Shortcuts fill the editable command
    field and never transmit until the administrator confirms Send. The lane
    should remain clear during this test.

## Multi-village ownership model

The hierarchy is strict: a village has one or more gates, and every controller
is provisioned to exactly one gate. The server resolves the authenticated
controller credential to `controller -> gate -> village`; it never trusts a
village or gate ID submitted by controller hardware. An unprovisioned controller,
an invalid key, a revoked key, an inactive gate, or an inactive village is rejected.

Vehicles, RFID stickers, events, commands, and guard access are village-scoped.
The same plate or RFID value may exist independently in two villages without one
village authorizing or viewing the other's record. Events retain their village,
gate, and controller ownership as historical facts even if names later change.

After the first administrator signs in, open **Villages & Gates** and create, in
order:

1. the village;
2. each physical gate;
3. each Plate + RFID or RFID-only controller.

Provisioning displays a controller ID and a random controller key once. Store the
key in that controller's private configuration. Only a SHA-256 digest is stored
in MySQL. Every controller request sends the ID plus the key using the
`X-Controller-Key` header (or `controller_key` form field).

The village selector changes the active tenant. The controller selector then
changes the live gate, latest event, counters, activity, access log, and hardware
command target within that village.

When the latest event is denied, administrators can register its detected plate
or RFID directly from the Overview. The registration form receives the detected
value automatically. The Vehicles page supports normalized plate searches (so
`ZAT-255` finds `ZAT255`) and provides edit, disable/enable, and confirmed
permanent-delete actions. Deleting a vehicle removes its RFID assignment but
keeps its historical access events.

For a custom hardware identifier, set `CONTROLLER_ID` in the controller's
private `.env`. It must be unique and contain only letters, numbers, `.`, `-`,
`_`, or `:`. Normally the automatically generated hardware-based ID should be
left unchanged.

## One-time installation

On a fresh macOS computer, or an Ubuntu 20.04-or-newer computer, run:

```bash
curl -fsSL https://raw.githubusercontent.com/T-REXX9/plate-program/main/install_program.sh -o /tmp/install-program.sh && bash /tmp/install-program.sh
```

Do not add `sudo` on macOS. The installer requests administrator permission only
for the specific files that need it. On Ubuntu it requests `sudo` itself.

Debian-based OS is available as a developer-only compatibility path. On actual
gate hardware, the same command asks whether the installer is being run
by an authorized developer and then requests the developer password without
showing it on screen. Successful authorization is recorded in a root-owned file
so future `program -update` operations remain unattended. This path installs
MariaDB from the OS package manager because Oracle does not publish an equivalent
native MySQL Server package for this platform; the application schema and SQL
client remain compatible. Regular Ubuntu and macOS installations continue to
use Oracle MySQL.

The installer handles Git, Python, MySQL, the database and restricted database
account, a random web-session secret, the Python environment, database migrations, the
first administrator account, and background startup. It prints the local-network
website address needed by the hardware controller.

Ubuntu 20.04 includes an older system Python. On that release, the installer
downloads the official Python 3.11.15 source archive, verifies its Python.org
SHA-256 checksum, and installs it under `/opt/python-3.11.15`. It does not replace
Ubuntu's `/usr/bin/python3`, so operating-system tools remain unaffected. The
first installation can take several minutes while Python is compiled; subsequent
updates reuse the verified installation.

The installer automatically retries temporary download and package failures,
repairs interrupted Ubuntu package operations, rejects incomplete or corrupted
Python downloads, and prints service diagnostics when startup fails. It also
enables all required official Ubuntu repository components and refreshes the
official Google Linux signing key when a Chrome repository is present. Internet
access, sufficient disk space, working hardware, and valid existing MySQL
administrator credentials still have to be available.

After installation, use these commands from any directory:

```bash
program -status
program -logs
program -url
program -update
program -restart
program -stop
program -start
```

`program -update` stops the website, fast-forwards the managed clone from GitHub
`main`, updates dependencies and the database schema, and restarts it. If an
update fails, the previous working revision is restored automatically.

The centralized schema includes villages, gates, controller credentials,
village memberships, village-scoped vehicles/RFID values, and immutable tenant
ownership on events and commands.

The macOS service starts whenever the installing user logs in. The Ubuntu service
starts during boot. Windows requires a separate PowerShell installer and is not
handled by this Bash script.

## Manual MySQL installation on macOS

```bash
brew install mysql
brew services start mysql
mysql_secure_installation
```

## Manual MySQL installation on Ubuntu

```bash
sudo apt update
sudo apt install mysql-server default-mysql-client
sudo systemctl enable --now mysql
sudo mysql_secure_installation
```

## Windows

Install MySQL Community Server 8 from the official MySQL Installer. Enable the
Windows service during installation and keep TCP port `3306` available locally.

## Create the database and application account

Sign in to the native MySQL server as an administrator:

```bash
mysql -u root -p
```

Run the following SQL, replacing the example password:

```sql
CREATE DATABASE plate_access_control
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'gatekeeper'@'127.0.0.1'
  IDENTIFIED BY 'REPLACE_WITH_A_SECURE_PASSWORD';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE VIEW, SHOW VIEW
  ON plate_access_control.* TO 'gatekeeper'@'127.0.0.1';

FLUSH PRIVILEGES;
```

The website and MySQL normally run on the same PC, so the database account is
restricted to the `127.0.0.1` loopback interface. MySQL does not need to be
exposed to the hardware controllers or the rest of the network.

## Configure and start the website

```bash
cp .env.example .env
```

Edit `.env` and set `MYSQL_PASSWORD` to the password used above. Then run:

```bash
./database/init_database.sh
./web/setup_web.sh
./web/start_web.sh
```

Open `http://localhost:8080`. Other devices on the same local network can open
`http://PC_IP_ADDRESS:8080`.

Sign in as the administrator and open **Villages & Gates** before connecting
hardware. A controller cannot self-register and is intentionally rejected until
it has been provisioned to a gate.

The `.env` file contains private MySQL credentials and is excluded from Git.
The default `MYSQL_TIME_ZONE=+08:00` keeps timestamps in Philippine time.

The NodeMCU calls `POST /api/controller/capture-request` when the loop detects a
vehicle, submits RFID to `POST /api/rfid-controller/recognitions`, and polls
`POST /api/controller/access-result` for the server decision. MySQL lookup,
camera capture, plate recognition, and event storage occur on the central
server. Every controller endpoint requires the provisioned controller ID and
controller key. TLS remains mandatory when the API is exposed through
Cloudflare or any public network. For the current LAN deployment, the camera
endpoint is a private RTSP address reachable directly by the central server;
Cloudflare Tunnel is optional and not required.

To exercise this controller workflow from macOS without connecting hardware or
moving the barrier, use the simulator. Provision a Plate + RFID controller
first, then export its ID and private key:

```bash
export PLATE_SERVER_URL=https://server.example.com
export PLATE_CONTROLLER_ID=your-controller-id
export PLATE_CONTROLLER_KEY=your-controller-key
# Optional: test the combined RFID path too.
export PLATE_SIMULATOR_RFID=your-rfid-value
bash tools/controller_simulator.sh
```

The simulator sends a heartbeat, requests a camera capture, optionally submits
RFID, and polls the real authorization endpoint. It reports PASS or DENIED but
never sends a barrier-open command.

## Standalone Android controller simulator

The standalone Android simulator is in `android-simulator/`. Open that
directory in Android Studio, let Gradle sync, select an Android 8.0 or newer
device/emulator, and run the `app` configuration. The simulator is independent
of the web UI and Bash simulator. Enter the server URL, a provisioned Plate +
RFID controller ID and key, then use **Vehicle present** to trigger the same
capture request sequence as the NodeMCU. **Scan RFID now**, **Toggle IR safety
beam**, **Vehicle leaves**, **Automatic gate progression**, and **Reset
simulator**, **Network simulation**, and **Export redacted timing timeline**
exercise the controller state machine. Timing defaults are 1,000 ms polling,
10,000 ms authorization timeout, 1,200 ms opening, 3,000 ms open hold, and
1,200 ms closing; all are editable in the app. All gate outputs are local
simulation state; no physical barrier command is sent.

To build the debug APK from a shell with Java 17 and Android SDK 35:

```bash
cd android-simulator
JAVA_HOME=/path/to/jdk-17 /path/to/gradle --no-daemon assembleDebug
```

The resulting APK is `app/build/outputs/apk/debug/app-debug.apk`. The checked-in
development build is also available at `Output/plate-controller-simulator-debug.apk`.

## Mobile account integration readiness

Plate Program includes a dormant, additive MySQL cache for future homeowner
account entitlements and synchronization metadata. It is disabled by default:

```text
MOBILE_ACCOUNT_INTEGRATION_ENABLED=0
MOBILE_ACCOUNT_SERVICE_URL=
MOBILE_ACCOUNT_SYNC_SECRET=
```

`GET /api/account-sync/v1/capabilities` reports whether this local installation
is prepared and configured without exposing secrets. No background connection,
mobile authentication, payment processing, or Xendit integration runs in this
repository yet. Most importantly, the reader authorization query does not use
the dormant entitlement tables, so existing gate behavior is unchanged.

The future account service will hold homeowner identities, subscription records,
payment history, and Xendit credentials. Entitlement and sync state are already
village-scoped so one village can never affect another village's registrations.
Authorization enforcement remains off until the mobile/payment phase is approved.

## Backups

A complete backup must contain both the MySQL database and the `Output` folder.

```bash
mysqldump --single-transaction --no-tablespaces \
  -h 127.0.0.1 -u gatekeeper -p \
  plate_access_control > plate_access_control.sql
```

Restore with:

```bash
mysql -h 127.0.0.1 -u gatekeeper -p \
  plate_access_control < plate_access_control.sql
```

## Remove the local Mac setup

Stop the website and remove only its generated environment, images, and keys:

```bash
./remove_local_setup.sh
```

To also permanently delete the dedicated MySQL database, MySQL account, and
private `.env` configuration:

```bash
./remove_local_setup.sh --delete-data
```

The cleanup script never uninstalls MySQL because MySQL was already installed
independently on the Mac.
