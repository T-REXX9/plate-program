# Gatekeeper Central server

## Authoritative hierarchy

```text
Server installation
└── One village
    └── Gate
        ├── Camera (one camera bound to each gate)
        └── NodeMCU controller
            ├── Inductive loop, RFID, gate safety, and relays
            ├── Capture requests and authorization polling
            └── Heartbeats and hardware state
```

Each server installation supports exactly one village and multiple gates. The
existing village-scoped tables and historical IDs remain for data integrity;
web and mobile APIs reject creation of a second village.

A controller stores only its stable controller ID and secret key. The server
uses that authenticated identity to resolve the gate and village. Controller
requests cannot select or override their village.

## First-time setup

1. Install and start Plate Program.
2. Create the first central administrator and sign in.
3. Open **Villages & Gates**.
4. Create a village using a permanent ID such as `village-a`.
5. Create its physical gates using permanent IDs such as
   `village-a-north-entry`.
6. Provision each controller to its correct gate.
7. Copy the displayed controller ID and controller key immediately. The key is
   shown once; MySQL stores only its one-way digest.
8. Put those two values in the controller's private configuration.
9. Register that village's vehicles and RFID stickers. The village is fixed for
   this server and is shown in the website header without a switcher.

For the current all-local deployment, configure the NodeMCU's Plate Program
base URL to the server's LAN address, for example `http://192.168.0.10:8080`.
Configure the camera binding with its direct RTSP endpoint, for example
`rtsp://<username>:<password>@192.168.0.124:554/stream1`. The NodeMCU and the
server must be able to reach the server LAN address, and the server must be
able to reach the camera on TCP port 554. No Cloudflare hostname or tunnel is
needed for this arrangement.

For production, leave the database endpoint blank and place the URL in the
worker environment as `CAMERA_ENDPOINT_<CAMERA_ID>`, uppercasing the ID and
replacing `.`, `-`, and `:` with `_`. This keeps the camera password out of the
database and source control.

Repeat the gate, controller, camera, and registration steps for every gate in
this village.

## Controller types

### NodeMCU controller

Provision the controller as **Plate + RFID**, then configure the ESP8266 with
the public or local Plate Program URL, controller ID, and one-time key. The
NodeMCU owns the inductive loop, RFID serial reader, gate state machine, safety
beam, traffic light, and barrier relays. When the loop detects a vehicle it
creates a server capture attempt and independently submits any RFID it reads.

The server owns camera capture, plate recognition, vehicle lookup, tenant
authorization, and the final allow/deny decision. Either a valid RFID or a
valid plate authorizes the attempt; late credentials are merged into the same
history event. If the server, camera, or recognition worker does not complete
within 10 seconds, the controller keeps the gate closed.

The former Raspberry Pi camera controller is retained as a dormant migration
reference only. It is not part of the production path.

### Camera binding and local RTSP

Open **Villages & Gates**, bind one camera ID to each gate, and select its
transport. For a network camera, the central camera worker must be able to
reach the endpoint. RTSP capture forces RTSP-over-TCP and the worker submits
the annotated result back to the server.

For the current setup, no tunnel is required: put the central server and camera
on the same LAN and bind the camera's private RTSP address directly. The worker
forces RTSP-over-TCP, so an endpoint in the form
`rtsp://<username>:<password>@<camera-ip>:554/<stream>` is supported. The server
must be able to reach the camera's address and TCP port 554.

Cloudflare private routing can be added later if the server becomes remote. A
public Cloudflare TCP hostname is less suitable for a persistent RTSP stream:
remote clients need `cloudflared access tcp`, and long-lived WebSocket-backed
connections may reset. Do not expose raw camera credentials in source control
or chat.

### RFID-only controller

Provision the controller as **RFID only**. Connect to its `RFID-GATE` recovery
Wi-Fi, open `http://192.168.4.1`, sign in, and open **System Mode**. Select
**Plate Program**, then enter:

1. The site Wi-Fi name and password.
2. The Plate Program URL. Both local `http://` and Cloudflare `https://` URLs
   are supported.
3. The exact provisioned controller ID.
4. The one-time controller key.

Save and restart. The RFID status LED blinks until Wi-Fi connects. The unit
then sends authenticated heartbeats and RFID decisions to its assigned village
and gate. Switching it back to **Standalone** keeps its existing local member
database and gate behavior; central credentials do not interfere with that
mode.

## Isolation guarantees

- Plates and RFID values are unique inside a village, not globally.
- Authorization queries always include the controller's resolved village ID.
- Recognition events store village, gate, and controller ownership.
- Hardware commands store the same tenant ownership and target one controller.
- Guard accounts receive explicit village memberships.
- Image endpoints check the active village before serving a captured frame.
- Unknown controllers and missing, wrong, or revoked controller keys receive an
  HTTP 401 response.
- Controllers assigned to inactive gates or villages receive an HTTP 403
  response and cannot authorize access.
- A controller provisioned as RFID-only cannot call Plate + RFID endpoints,
  and a Plate + RFID controller cannot impersonate an RFID-only controller.

## Controller request authentication

Every request to `/api/reader/*` or `/api/rfid-controller/*` must contain:

```text
controller_id=<provisioned controller ID>
X-Controller-Key: <one-time controller key>
```

For clients that cannot add a header, `controller_key` may be sent as a form
field. The header is preferred because it keeps the secret separate from the
recognition payload.

Do not send `village_id` or `gate_id`; the server deliberately ignores client
claims about tenant ownership.

## Data model

- `villages` — tenant boundary and local timezone.
- `gates` — physical lane/entrance belonging to one village.
- `controllers` — hardware identity assigned to one gate.
- `cameras` — one camera transport and endpoint bound to one gate.
- `camera_capture_jobs` — server capture/recognition attempts correlated by
  controller attempt ID.
- `controller_credentials` — hashed, revocable controller secrets.
- `user_village_roles` — explicit village access for non-central users.
- `vehicles` and `rfid_stickers` — registrations scoped by `village_id`.
- `access_events` — immutable village/gate/controller context for every event.
- `reader_commands` — tenant-owned commands targeted to one controller.

## Mobile app boundary

The future Flutter application will authenticate a person, resolve their
`user_village_roles`, and expose only villages in that membership set. It will
consume mobile-specific APIs; it will never use a controller credential.
Payment and subscription enforcement remains disabled until the client supplies
the Xendit account and the entitlement rules are approved.
