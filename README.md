# Plate Access Control Web Server

This public repository contains the database, protected reader API, and admin
dashboard for the plate access-control system. It runs on a separate PC so the
Raspberry Pi can dedicate its resources to YOLO detection and OCR.

## Data flow

1. The Raspberry Pi captures five frames and selects the best plate crop.
2. YOLO detects the plate and PP-OCRv5 returns a clean alphanumeric value.
3. The Pi sends the plate, detector confidence, and enhanced JPEG crop here.
4. This server checks the registered-vehicle database, stores the event, and
   returns an authorized or denied result.
5. The dashboard updates automatically without a full-page refresh.

## Start the PC server

Install Python 3, then run:

```bash
./web/setup_web.sh
./web/start_web.sh
```

Open `http://localhost:8080`. The first visit creates the administrator account.
Other devices on the same network can use `http://PC_IP_ADDRESS:8080`.

The first launch creates a private API key in:

```text
database/reader_api.key
```

Copy the value from that file into the `PLATE_API_KEY` environment variable on
the Raspberry Pi. Do not commit or share the key publicly.

## Reader API

The Pi submits a multipart request to `POST /api/reader/recognitions` with:

- Header `X-API-Key`
- Field `plate`
- Field `detector_confidence`
- JPEG file field `image`

The response includes `authorized`, `decision`, `owner`, `plate`, `event_id`,
and whether the event was suppressed as a recent duplicate.

## Runtime data

SQLite data, account secrets, reader API keys, and captured plate images are
excluded from Git. Back up the `database` and `Output` directories on the PC.
