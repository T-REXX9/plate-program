# Plate Access Control Web Server

This repository contains the native MySQL database layer, protected reader API,
and admin dashboard. It runs on a separate PC so the Raspberry Pi can dedicate
its resources to YOLO detection and OCR.

No Docker or SQLite is used.

## Data flow

1. An administrator presses **Capture plate** on the dashboard.
2. The Raspberry Pi claims the request while YOLO and OCR remain idle.
3. The Pi captures five frames and selects the best detected plate crop.
4. PP-OCRv5 returns a clean alphanumeric value and uploads it with the crop.
5. The server checks MySQL, stores the event, and returns authorized or denied.
6. The dashboard shows the Pi's frame, YOLO, OCR, upload, and total timings.
7. The dashboard synchronizes automatically without full-page refreshes.

## Install MySQL directly on macOS

```bash
brew install mysql
brew services start mysql
mysql_secure_installation
```

## Install MySQL directly on Ubuntu or Debian

```bash
sudo apt update
sudo apt install mysql-server mysql-client
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
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER 'gatekeeper'@'127.0.0.1'
  IDENTIFIED BY 'REPLACE_WITH_A_SECURE_PASSWORD';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE VIEW, SHOW VIEW
  ON plate_access_control.* TO 'gatekeeper'@'127.0.0.1';

FLUSH PRIVILEGES;
```

The website and MySQL normally run on the same PC, so the database account is
restricted to the `127.0.0.1` loopback interface. MySQL does not need to be
exposed to the Raspberry Pi or the rest of the network.

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

The `.env` file contains private MySQL credentials and is excluded from Git.
The default `MYSQL_TIME_ZONE=+08:00` keeps timestamps in Philippine time.

## Reader API key

The first website launch creates a separate private API key in:

```text
database/reader_api.key
```

Copy its value into `PLATE_API_KEY` on the Raspberry Pi. The reader API key and
MySQL password are different secrets and should never be exchanged.

The Pi polls `POST /api/reader/commands/next` for lightweight capture requests,
then sends results to `POST /api/reader/recognitions`. MySQL lookup and event
storage occur only on the PC.

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
