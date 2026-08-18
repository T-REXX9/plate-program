# Plate Program Web UI/UX Blueprint

**Audience:** UI/UX designer and front-end implementer
**Product:** Plate Program / Gatekeeper local access-control dashboard
**Primary target:** Desktop control-room PC and iPhone XS Max (414 × 896 CSS pixels)
**Document purpose:** Define what the interface must communicate and do while allowing a complete visual redesign.

---

## 1. Product summary

Plate Program is the local control and monitoring website for a vehicle entrance gate. It receives recognition results from either:

1. A Raspberry Pi plate controller with a camera, YOLO plate detection, OCR, RFID, GPIO sensors, traffic lights, and a boom barrier.
2. A camera-less RFID controller that reads an RFID sticker and asks Plate Program whether access is authorized.

The website must let gate staff understand the lane in seconds. It is an operational safety interface, not a general analytics website. The most important information is:

- Is the controller connected?
- Is a vehicle present?
- Is the safety beam blocked?
- Is the barrier open, closed, moving, or in fault?
- Is the traffic light STOP or GO?
- Who was just recognized?
- Was access authorized or denied?
- What did the camera capture, when a camera is available?

The visual design may change completely, but these meanings and safety relationships must remain obvious.

---

## 2. Users and permissions

### Administrator

The administrator can:

- View the overview, live controller state, latest access event, recent access, and logs.
- Request a camera capture from a Raspberry Pi controller.
- View raw and annotated camera frames.
- Register, edit, activate, and deactivate vehicles.
- Assign one RFID sticker to a vehicle.
- Create, activate, and deactivate guard accounts.
- Export filtered access logs to CSV.
- Open or close the boom barrier manually after confirmation.
- Test the red and green traffic signals.
- Use the low-level RFID serial console.

### Security guard (`viewer`)

The guard is strictly read-only. The guard can:

- View the overview and live gate status.
- View the latest photo or RFID result.
- View the Hardware page indicators.
- View access logs and snapshots.

The guard must not see or access:

- Capture controls.
- Barrier or traffic-light controls.
- RFID serial controls.
- Vehicle registration and editing.
- Guard-account administration.
- CSV export.

The redesign must not rely only on hiding controls visually. The backend already enforces permissions, and the UI must accurately reflect them.

---

## 3. Information architecture

```text
Unauthenticated
├── First-time administrator setup
└── Sign in

Authenticated
├── Overview
│   ├── Live connection status
│   ├── Gate hardware indicators
│   ├── Seven-day activity
│   ├── Today’s metrics
│   ├── Latest camera or RFID event
│   └── Recent access events
├── Hardware
│   ├── Live hardware indicators
│   ├── Manual gate and traffic controls [administrator only]
│   └── RFID serial console [administrator only]
├── Vehicles [administrator only]
│   ├── Search/list
│   ├── Register vehicle
│   └── Edit vehicle
├── Guards [administrator only]
│   ├── Account list
│   └── Create guard account
├── Access log
│   ├── Filters
│   ├── Event table/list
│   ├── Snapshot viewer
│   └── CSV export [administrator only]
└── Sign out
```

### Primary navigation

Administrator navigation:

1. Overview
2. Hardware
3. Vehicles
4. Guards
5. Access log
6. Account / Sign out

Guard navigation:

1. Overview
2. Hardware
3. Access log
4. Account / Sign out

Desktop may use a top bar or sidebar. Mobile should use a compact bottom navigation, drawer, or similarly reachable pattern. Do not shrink desktop navigation into unreadable links.

---

## 4. Layout wireframes

These wireframes define hierarchy, not final styling.

### Desktop Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Brand     Overview  Hardware  Vehicles  Guards  Access log      User / Exit │
├──────────────────────────────────────────────────────────────────────────────┤
│ Gate overview                         [Connection]        [Capture plate]    │
├──────────────────────────────────────┬───────────────────────────────────────┤
│ LIVE GATE STATUS                     │ SEVEN-DAY ACTIVITY                    │
│ Server  Camera  Loop  IR             │ Day  Day  Day  Day  Day  Day  Day    │
│ Barrier Traffic Access result        │ totals + authorized/denied            │
├───────────────────┬──────────────────┬──────────────────┬────────────────────┤
│ Active vehicles   │ Entries today    │ Authorized       │ Denied             │
├──────────────────────────────────────────────────────────┬───────────────────┤
│ LATEST ACCESS EVENT                                      │ Decision + time   │
│ ┌──────────────────────────────────────────────────────┐ │ Plate / RFID      │
│ │            contained raw or annotated frame         │ │ Owner             │
│ └──────────────────────────────────────────────────────┘ │ Processing time   │
├──────────────────────────────────────────────────────────────────────────────┤
│ RECENT ACCESS: time | plate/RFID | owner | vehicle | decision               │
└──────────────────────────────────────────────────────────────────────────────┘
```

The event details may appear below rather than beside the frame at narrower desktop widths. The camera frame must never be forced into a cropped decorative banner.

### iPhone XS Max Overview

```text
┌──────────────────────────────────────┐
│ Brand       Online        Menu/User │
├──────────────────────────────────────┤
│ BARRIER: CLOSED       SIGNAL: STOP  │
│ Loop: Clear            IR: Clear    │
├──────────────────────────────────────┤
│ LATEST: AUTHORIZED                    │
│ ZAT255                               │
│ Melson Bacuen · RFID …0949           │
│ 4:10 PM                  1.50 s      │
├──────────────────────────────────────┤
│ [ Annotated | Raw ]                  │
│ ┌──────────────────────────────────┐ │
│ │      contained vehicle frame    │ │
│ └──────────────────────────────────┘ │
├──────────────────────────────────────┤
│ [Capture plate — administrator]      │
├──────────────────────────────────────┤
│ Today: 42  Approved: 39  Denied: 3  │
├──────────────────────────────────────┤
│ Remaining status, 7 days, recent     │
└──────────────────────────────────────┘
```

For RFID-only operation, remove the image selector and image block entirely; expand the plate/authorization result instead.

### Mobile Hardware

```text
┌──────────────────────────────────────┐
│ Gate hardware              Live     │
├──────────────────────────────────────┤
│ Server      ● Connected              │
│ Camera      ● Detected/Unavailable   │
│ Loop        ● Vehicle/Clear          │
│ IR beam     ● Blocked/Clear          │
│ Barrier     ● Current state          │
│ Traffic     ● STOP/GO                │
│ Access      ● Ready/Not recognized   │
├──────────────────────────────────────┤
│ ADMIN DIAGNOSTICS                    │
│ [Raise barrier] [Lower barrier]      │
│ [Test red]      [Test green]         │
├──────────────────────────────────────┤
│ RFID SERIAL CONSOLE (collapsible)    │
└──────────────────────────────────────┘
```

Put advanced serial settings behind a clearly labeled expandable section on mobile so they do not obscure the live safety state.

---

## 5. Global design principles

### Operational clarity

- A user should identify authorized versus denied access without reading a paragraph.
- Pair every color with a word, icon, or shape. Never communicate safety state with color alone.
- Use large, glanceable plate/RFID values and owner names.
- Keep timestamps visible and unambiguous.
- Treat `Offline`, `Fault`, `Blocked`, `Denied`, and `Not recognized` as distinct states.

### Safety language

- Destructive or physical actions require confirmation.
- Barrier-close confirmation must explicitly mention checking for vehicles and people.
- The IR safety beam is safety-critical and should be visually prominent when blocked.
- The traffic-light state must say `STOP` or `GO`, not merely show red or green.

### Responsive behavior

- Primary mobile target: iPhone XS Max, portrait, 414 × 896.
- Also support 375 px mobile width, tablet, laptop, and large desktop.
- No horizontal page scrolling.
- Tables may become stacked cards or controlled horizontal table regions on mobile.
- Tap targets should be at least 44 × 44 CSS pixels.
- Body text should remain at least 16 px on mobile.
- Important live information must not require zooming.

### First mobile viewport priority

On the Overview page, the first screen should prioritize:

1. Compact navigation/header.
2. Controller online/offline state.
3. Current barrier and traffic-light state.
4. Latest authorization result, plate/RFID, and owner.
5. Capture action for administrators when a camera controller is active.

Secondary metrics and historical data may follow below. Do not place a large decorative header above operational information.

---

## 6. Shared visual language

The designer should create a reusable system for the following.

### Status severity

| Meaning | Suggested treatment | Examples |
|---|---|---|
| Normal / ready | Green + check/ready label | Controller connected, beam clear |
| Stopped / closed | Red with neutral wording where expected | Traffic STOP, barrier closed |
| Warning / moving | Amber + motion icon | Barrier opening or closing |
| Critical fault | Red + high-contrast alert | Barrier fault, controller offline |
| Unknown / unavailable | Gray + explicit label | RFID controller has no camera |
| Authorized | Green + `AUTHORIZED` | Registered plate or RFID |
| Denied | Red + `DENIED` | Unknown or expired credential |

Red is not always an error: a closed barrier and STOP signal are normal standby states. Labels must explain the meaning.

### Core components

- Application header/navigation.
- Page title and supporting description.
- Live/reconnecting badge.
- Hardware signal tile.
- Authorization result badge.
- Plate-number display.
- RFID-value display.
- Owner/vehicle identity block.
- Metric card.
- Seven-day activity item/chart.
- Camera-frame viewer.
- Raw/annotated segmented selector.
- Responsive data table or record card.
- Search/filter toolbar.
- Form field, validation state, and required marker.
- Confirmation dialog for physical actions.
- Toast/notification that disappears after about four seconds and dismisses on hover.
- Empty, loading, offline, stale, error, and permission-limited states.
- Terminal/console display for advanced RFID diagnostics.

---

## 7. Screen blueprint: Sign in

### Goal

Give administrators and guards a simple local sign-in experience.

### Required content

- Product name/logo area.
- Username.
- Password.
- Sign-in action.
- Error message for invalid credentials.
- Short statement that the system is local gate access control.

### States

- Default.
- Submitting.
- Invalid credentials.
- Account inactive.
- Server/database error.

The page should not advertise technical implementation details, controller hardware, IP addresses, or database configuration.

---

## 8. Screen blueprint: First-time setup

This screen appears only when no user exists.

### Required fields

- Administrator username, minimum 3 characters.
- Password, minimum 10 characters.
- Confirm password.
- Create administrator action.

### Required states

- Password mismatch.
- Username already used.
- Invalid length.
- Successful setup followed by navigation to sign in.

---

## 9. Screen blueprint: Overview

The Overview is the primary operating screen and must work for both controller types.

### A. Page-level connection state

Show one of:

- `Connected` — dashboard synchronization is healthy.
- `Reconnecting…` — browser cannot obtain the current state.

The page updates automatically every 2 seconds while visible. It must not perform a full-page refresh, jump the scroll position, flash white, or reset open UI controls.

### B. Administrator capture action

Only administrators see `Capture plate`.

States:

- Ready: `Capture plate`.
- Queued/processing: disabled, `Capturing…`.
- Already busy: notification explaining that capture is queued or active.
- Request failed: error notification.

This action is useful only for the Raspberry Pi camera controller. The design should disable or hide it when the active controller is RFID-only or unavailable.

### C. Live gate indicators

Seven signals are required:

| Signal | Normal labels/states |
|---|---|
| Controller link | Connected / Offline |
| Camera | Detected / Unavailable |
| Loop detector | Vehicle present / Clear / Unknown |
| IR safety beam | Blocked / Clear / Unknown |
| Boom barrier | Closed, Opening, Open waiting passage, Vehicle under barrier, Closing, Fault, Unknown |
| Traffic signal | STOP / GO / Unknown |
| Access result | Ready / Not recognized / Unknown |

For an RFID-only controller, `Camera: Unavailable` is correct and should not make the entire system look broken.

### D. Seven-day activity

For each available day show:

- Date.
- Total events.
- Authorized count.
- Denied count.

The visual may be cards, bars, or a compact chart. Authorized and denied values must remain numerically readable. Include an empty state before the first event.

### E. Today’s metrics

Four metrics:

1. Active vehicles.
2. Entries today.
3. Authorized today.
4. Denied today.

These are live values, not static reports.

### F. Latest event — camera controller

Required content:

- Authorized/denied indicator.
- Decision text.
- Raw/Annotated selector.
- Captured vehicle frame.
- Plate number.
- RFID value or `not read`.
- Owner name or `Unregistered vehicle`.
- Local timestamp.
- Total recognition time.
- Optional timing detail: frame acquisition, YOLO, OCR, and server upload.

Image rules:

- Never crop the vehicle frame merely to fill the card.
- Use `object-fit: contain` or equivalent behavior.
- Preserve the image aspect ratio.
- Give the frame a defined maximum height on desktop and mobile.
- Do not show a second oversized empty image area under the actual image.
- Raw and annotated views use the same frame container size.
- Annotated is the default; remember the user’s choice locally.
- New images must update without stale browser caching.

### G. Latest event — RFID-only controller

No fake camera placeholder or broken-image icon should appear.

Authorized event:

- Very large plate-number value returned for the registered RFID.
- `AUTHORIZED` state.
- Owner name.
- RFID value.
- Timestamp.
- Optional vehicle description.

Denied event:

- Very large `ACCESS DENIED` message.
- Unknown RFID value.
- Timestamp.
- No blank camera frame.

### H. No-event state

Show a compact message such as:

- `No access event yet`
- `The first plate or RFID access event will appear here automatically.`

Do not reserve excessive vertical space for an image that does not exist.

### I. Recent access

Show the latest eight events:

- Timestamp.
- Plate.
- RFID.
- Owner.
- Vehicle.
- Decision.
- Link to full access log.

On mobile, use readable event cards or a compact responsive row. The plate, owner, decision, and time take priority over less important vehicle details.

---

## 10. Screen blueprint: Hardware

### Audience

- Guard: status viewing only.
- Administrator: status plus protected diagnostic actions.

### A. Live status

Use the same seven live indicators and meanings as Overview. This screen polls every 1 second.

### B. Controller variation

Raspberry Pi controller:

- Camera and all GPIO indicators are available.
- Manual hardware actions may be enabled when the controller is online.
- RFID serial console may be used.

RFID-only controller:

- Live states are visible.
- Camera displays `Unavailable`.
- Raspberry Pi-specific manual controls and serial console are unavailable/disabled.
- Explain the limitation; do not make disabled controls look broken.

### C. Manual boom-barrier controls

Administrator only:

- Raise barrier.
- Lower barrier.

Both require confirmation. The close confirmation must warn the operator to ensure nobody and no vehicle is beneath the barrier.

States:

- Controller offline: disabled.
- RFID-only controller: disabled/unavailable.
- Gate mode disabled: disabled.
- Command queued.
- Command completed.
- Command failed/timed out.

### D. Manual traffic-light controls

Administrator only:

- Test red for three seconds.
- Test green for three seconds.

Green requires a warning because it tells the driver to move.

### E. RFID serial console

This is an advanced maintenance interface, not a normal gate-operation control.

Required quick commands:

- Read one tag.
- Read multiple tags.
- Reader information.
- Check work mode.
- Set Answer Mode.
- Set scan duration to 2 seconds.
- Set scan duration to 5 seconds.
- Set reader baud rate to 9600.

Required serial settings:

- Baud rate.
- Data bits.
- Parity.
- Stop bits.
- Response timeout.
- Input type: HEX bytes or plain text.
- Editable command input.
- Send command.
- Clear terminal.

Required terminal output:

- Timestamp.
- Port and serial configuration.
- Transmitted HEX.
- Received HEX/text.
- Queued, reading, completed, failed, and timeout states.

Selecting a preset only loads the command. It must never transmit until the administrator explicitly confirms Send.

---

## 11. Screen blueprint: Registered vehicles

### List/search

Search across:

- Plate.
- RFID.
- Owner.
- Make.
- Model.

Each record shows:

- Plate number.
- RFID sticker or `not assigned`.
- Owner.
- Email.
- Vehicle description.
- Contact number.
- Active/inactive status.
- Edit.
- Enable/disable action.

The inactive state must be visually distinct without making the record unreadable.

### Empty state

- Explain that no vehicles are registered.
- Provide a clear `Register vehicle` action.

### Register/edit form

Required fields are marked with `*`.

Identification:

- Plate number (required, normalized to uppercase alphanumeric).
- RFID sticker (optional, 4–64 normalized alphanumeric characters).
- Owner name (required).
- Vehicle type: Car, SUV, Van, Truck, Motorcycle, Bus, Other.
- Color.

Vehicle details:

- Make.
- Model.
- Registration expiry date.
- Optional local photo path.

Contact and notes:

- Contact number.
- Email.
- Notes.

Actions:

- Cancel.
- Register vehicle or Save changes.

Error states must clearly identify duplicate/invalid plate or RFID values, missing required data, and invalid RFID length. Preserve typed values after an error.

---

## 12. Screen blueprint: Guard accounts

Administrator only.

### List

- Username.
- Permission summary.
- Created date.
- Last sign-in date or `Never`.
- Active/inactive state.
- Activate/deactivate action.

### Create guard

- Username, minimum 3 characters.
- Password, minimum 10 characters.
- Confirm password.
- Clear read-only permission summary.
- Cancel and Create actions.

Explain that guards cannot operate the camera, barrier, traffic signals, vehicle registry, exports, or user administration.

---

## 13. Screen blueprint: Complete access log

### Filters

- Plate contains.
- Decision: All, Authorized, Denied, Unreadable, Manual.
- From date.
- To date.
- Apply filters.
- Reset.

### Results

- Total event count.
- 50 events per page.
- Previous/next pagination.

Each event includes:

- Timestamp.
- Plate.
- RFID.
- Owner/person.
- Vehicle description.
- Direction.
- Decision.
- Gate action.
- Snapshot link when available.

Administrators can export the currently filtered result to CSV. Guards cannot export.

### Snapshot behavior

- Open the stored event image without losing the current filtered log state.
- Camera-less RFID events correctly show no snapshot.

### Mobile recommendation

Use event cards with the decision, plate, owner, and timestamp at the top. Secondary fields can sit in a definition-list layout. Avoid forcing an eight-column table into 414 px.

---

## 14. Real-time behavior and state transitions

### Overview polling

- Endpoint: `GET /api/dashboard`
- Frequency: every 2 seconds while the browser tab is visible.
- Pause while hidden; synchronize immediately when visible again.
- Response must not be cached.
- On failed synchronization show `Reconnecting…` while retaining the last known data.
- Never clear the dashboard merely because one request failed.

### Hardware polling

- Uses the same dashboard endpoint.
- Frequency: every 1 second.
- Enable manual controls only when the controller is online, is the Raspberry Pi plate controller, and gate mode is not disabled.

### Recognition progression

Typical plate capture:

```text
Ready → Capture queued → Capturing → Recognition result → Authorized/Denied
```

Typical gate cycle:

```text
Idle closed
→ Vehicle detected / recognizing
→ Authorized
→ Opening
→ Open, waiting for passage
→ IR beam blocked / vehicle under barrier
→ IR beam clear
→ Closing
→ Idle closed
```

Denied or unreadable credentials must return to a safe closed/STOP condition without implying that the barrier opened.

---

## 15. Dashboard data contract

The designer/front-end implementer should treat these fields as the source of truth.

```json
{
  "summary": {
    "active_vehicles": 120,
    "events_today": 42,
    "authorized_today": 39,
    "denied_today": 3
  },
  "latest_event": {
    "id": 123,
    "plate_number": "ZAT255",
    "rfid_number": "E2843611000010000949",
    "owner_name": "Melson Bacuen",
    "decision": "authorized",
    "local_time": "2026-08-15 16:10:00",
    "has_image": true,
    "frame_urls": {
      "raw": "/events/123/frame/raw",
      "annotated": "/events/123/frame/annotated"
    },
    "image_version": 123
  },
  "latest_timing": {
    "frames_ms": 500,
    "yolo_ms": 650,
    "ocr_ms": 300,
    "server_ms": 50,
    "total_ms": 1500
  },
  "recent_events": [],
  "daily": [],
  "system": {
    "controller_type": "plate",
    "controller_online": true,
    "camera_running": true,
    "camera_state": "remote",
    "detector_state": "idle",
    "gate_state": "idle_closed",
    "loop_active": false,
    "ir_blocked": false,
    "barrier_open": false,
    "traffic_green": false,
    "plate_unrecognized": false,
    "last_plate": "ZAT255",
    "last_rfid": "E2843611000010000949",
    "controller_seen_at": "2026-08-15 16:10:00",
    "last_heartbeat": "2026-08-15 16:10:00"
  }
}
```

`controller_type` values currently used:

- `plate`: Raspberry Pi camera/plate controller.
- `rfid`: Camera-less RFID controller.

`latest_event` and `latest_timing` may be `null`. Image URLs exist only when `has_image` is true.

---

## 16. Existing backend routes

The visual redesign should use the existing routes unless a coordinated backend change is approved.

| Method | Route | Purpose | Permission |
|---|---|---|---|
| GET/POST | `/setup` | First administrator | Only before setup |
| GET/POST | `/login` | Sign in | Public |
| POST | `/logout` | Sign out | Signed-in users |
| GET | `/` | Overview | Signed-in users |
| GET | `/api/dashboard` | Live dashboard JSON | Signed-in users |
| POST | `/camera/capture` | Queue plate capture | Administrator |
| GET | `/hardware` | Hardware dashboard | Signed-in users |
| POST | `/hardware/command` | Gate/light command | Administrator |
| POST | `/hardware/serial` | Queue RFID serial command | Administrator |
| GET | `/api/hardware/commands/{id}` | Serial result | Administrator |
| GET | `/vehicles` | Vehicle registry | Administrator |
| GET/POST | `/vehicles/new` | Register vehicle | Administrator |
| GET/POST | `/vehicles/{id}/edit` | Edit vehicle | Administrator |
| POST | `/vehicles/{id}/toggle` | Enable/disable vehicle | Administrator |
| GET | `/users` | Guard accounts | Administrator |
| GET/POST | `/users/new` | Create guard | Administrator |
| POST | `/users/{id}/toggle` | Activate/deactivate guard | Administrator |
| GET | `/logs` | Filtered access log | Signed-in users |
| GET | `/logs/export.csv` | Export filtered log | Administrator |
| GET | `/events/{id}/image` | Stored event image | Signed-in users |
| GET | `/events/{id}/frame/raw` | Raw frame | Signed-in users |
| GET | `/events/{id}/frame/annotated` | Annotated frame | Signed-in users |

All state-changing browser forms require the existing CSRF token. The redesign must preserve this security behavior.

---

## 17. Front-end integration constraints

The current application uses Flask/Jinja templates, one CSS file, and vanilla JavaScript. A designer can deliver Figma plus implementable HTML/CSS specifications without changing the backend technology.

If the designer directly edits templates, retain the existing form actions, method types, field names, permission conditionals, and CSRF fields.

If existing JavaScript remains unchanged, preserve these important DOM hooks:

- `sync-status`
- `camera-capture-form`
- `camera-capture-button`
- `controller-lamp`, `controller-state`
- `camera-lamp`, `camera-state`
- `loop-lamp`, `loop-state`
- `ir-lamp`, `ir-state`
- `barrier-lamp`, `barrier-state`
- `traffic-lamp`, `traffic-state`
- `plate-result-lamp`, `plate-result-state`
- `hardware-updated`
- `daily-activity`
- `metric-active-vehicles`
- `metric-events-today`
- `metric-authorized-today`
- `metric-denied-today`
- `frame-selector` and `.frame-option[data-frame-kind]`
- `latest-photo-frame`, `latest-photo`
- `latest-photo-details`
- `latest-photo-placeholder`
- `latest-placeholder-title`, `latest-placeholder-message`
- `latest-access-led`, `latest-decision`
- `latest-plate`, `latest-rfid`, `latest-owner`, `latest-timing`, `latest-time`
- `recent-events-body`
- Hardware console IDs currently used by `hardware.js`.

The preferred implementation approach is to keep semantic IDs for behavior and use new classes/data attributes for styling.

---

## 18. Accessibility requirements

- Meet WCAG 2.1 AA contrast for text and controls.
- Provide visible keyboard focus.
- Support keyboard navigation for all actions.
- Do not remove button focus outlines without an equivalent replacement.
- Use real buttons for actions and links for navigation.
- Include text alongside all colored lamps.
- Provide descriptive alternative text for camera frames.
- Use `aria-live` appropriately for connection and notification updates without announcing every one-second heartbeat.
- Respect reduced-motion preferences.
- Do not use continuous blinking for faults.
- Confirmation dialogs must clearly identify the physical action.

---

## 19. Required design states

Every major screen/component should be designed in these conditions:

- Loading.
- Normal populated state.
- Empty state.
- Controller offline.
- Browser reconnecting.
- Camera unavailable but RFID controller online.
- Camera controller online.
- Vehicle present.
- IR beam blocked.
- Barrier opening.
- Barrier open.
- Barrier closing.
- Barrier fault.
- Traffic STOP.
- Traffic GO.
- Authorized plate event with image.
- Denied plate event with image.
- Authorized RFID-only event without image.
- Denied RFID-only event without image.
- No plate detected/unreadable.
- Administrator view.
- Guard view.
- Form validation error.
- Physical command queued/completed/failed.
- Long owner, plate, RFID, and vehicle values.

---

## 20. Suggested designer deliverables

1. Sitemap/user-flow diagram.
2. Design tokens: color, typography, spacing, radius, shadows, and icon rules.
3. Desktop and iPhone XS Max layouts for every primary screen.
4. Components with all states and variants.
5. Administrator and guard variants.
6. Raspberry Pi camera-controller and RFID-only variants.
7. Interactive prototype for capture, recognition result, gate state changes, filtering, and confirmation dialogs.
8. Image-frame sizing and aspect-ratio specification.
9. Responsive table/card behavior.
10. Accessibility annotations.
11. Developer handoff with exact spacing, sizes, breakpoints, and asset exports.

---

## 21. Acceptance checklist

The redesigned UI is acceptable when:

- The gate’s current safety state can be understood at a glance.
- Authorized and denied events are unmistakable without relying only on color.
- No camera image is clipped or stretched.
- RFID-only operation never displays a broken or giant empty image frame.
- Overview and Hardware update without full-page refreshes.
- Mobile pages have no horizontal page scrolling.
- Critical mobile information appears before historical analytics.
- Guard users cannot see or trigger administrator actions.
- Raspberry Pi-specific controls are unavailable for an RFID-only controller.
- Every physical action requires the appropriate safety confirmation.
- Existing backend field names, routes, CSRF protection, and role checks continue working.
- Empty, offline, reconnecting, fault, and long-content states are fully designed.

---

## 22. Product direction

The desired visual personality is professional gate-security equipment: calm, legible, dependable, and operational. Avoid a generic marketing-dashboard appearance, excessive gradients, tiny analytics text, decorative animation, or oversized empty cards. Live safety state and the latest access decision should always receive greater visual priority than decoration.
