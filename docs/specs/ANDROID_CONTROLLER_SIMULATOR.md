# Standalone Android Controller Simulator

## Problem Statement

The centralized Plate Program server depends on an ESP8266 NodeMCU controller
to initiate vehicle attempts, submit RFID reads, report sensors, poll server
authorization, and operate the gate state machine. Physical hardware is not
always available during development, so the complete workflow from vehicle
detection through server recognition and gate response is difficult to test.

The existing command-line simulator is useful for basic HTTP checks, but it
does not behave like a controller, does not expose the complete gate state, and
does not let an operator interactively simulate sensors, RFID, safety events,
retries, delays, and gate movement.

## Solution

Build a standalone Android application that behaves as a virtual Plate + RFID
controller. It owns a local controller state machine and communicates with the
existing centralized server through the same authenticated HTTP contract used
by the ESP8266 firmware.

The simulator must let an operator select a provisioned controller, set the
inductive loop to vehicle-present, optionally scan an RFID value, observe the
server capture and authorization lifecycle, and manually or automatically
advance the simulated gate through opening, open, closing, and closed states.
It must show all request, response, polling, camera, YOLO, OCR, authorization,
and simulated gate delays without ever controlling physical hardware.

## User Stories

1. As a developer, I want a standalone Android simulator, so that I can test
   the centralized server without an ESP8266 connected.
2. As a developer, I want to enter a server URL, so that I can test local,
   remote, or tunnel-accessible deployments.
3. As a developer, I want to select a provisioned controller identity, so that
   every request is scoped to the same gate and village as real hardware.
4. As a developer, I want controller credentials stored securely on the
   device, so that keys are not exposed in logs or screenshots.
5. As a developer, I want to send a controller heartbeat, so that the server
   shows the simulator as online.
6. As a developer, I want to see the heartbeat request duration and response,
   so that connectivity and authentication failures are obvious.
7. As a tester, I want to toggle the inductive loop between clear and vehicle
   present, so that I can start an access attempt exactly as the controller
   does.
8. As a tester, I want vehicle-present to trigger a capture request
   automatically, so that the simulator follows the real controller sequence.
9. As a tester, I want to see the capture request payload summary, response
   status, job ID, camera ID, and round-trip delay, so that I can verify the
   server accepted the attempt.
10. As a tester, I want to submit an RFID value while the vehicle is present,
    so that I can test RFID-only authorization.
11. As a tester, I want to omit RFID, so that I can test plate-only
    authorization.
12. As a tester, I want to submit RFID before or after the plate result, so
    that I can test either credential arriving first.
13. As a tester, I want to test combined plate and RFID evidence, so that I
    can verify the server combines credentials without requiring both.
14. As a tester, I want to see whether the RFID request was accepted,
    authorized, denied, correlated, or treated as a duplicate.
15. As a tester, I want the simulator to poll authorization at the same cadence
    as the ESP8266, so that timing behavior matches production hardware.
16. As a tester, I want to see every authorization poll and its response time,
    so that server latency and queueing are measurable.
17. As a tester, I want to see server-reported camera queue time, recognition
    processing time, and total job time, so that I can identify slow stages.
18. As a tester, I want to see the recognized plate, detector confidence, OCR
    confidence, and annotated frame, so that I can validate server recognition.
19. As a tester, I want to see pending, authorized, denied, failed, and timed
    out states, so that all server outcomes are testable.
20. As a tester, I want authorization to cause the simulated barrier to open,
    so that I can verify the controller would act on a server approval.
21. As a tester, I want to see the simulated barrier opening delay, so that I
    can compare server response timing with gate behavior.
22. As a tester, I want to see the simulated traffic signal change to green,
    so that the normal authorized gate sequence is visible.
23. As a tester, I want the simulator to show the barrier open state, so that I
    can verify the complete authorized cycle.
24. As a tester, I want the simulator to show closing and closed states, so
    that the full gate lifecycle can be tested.
25. As a tester, I want to toggle the IR safety beam while closing, so that a
    close command is blocked or reversed according to controller safety rules.
26. As a tester, I want to toggle the loop clear state, so that I can test a
    vehicle leaving before authorization.
27. As a tester, I want denied or timed-out attempts to remain closed, so that
    the simulator verifies fail-closed behavior.
28. As a tester, I want to retry an attempt, so that capture and RFID retry
    behavior can be tested without rebuilding the app.
29. As a tester, I want to reset the simulated controller to its idle state,
    so that each test starts from a known condition.
30. As a tester, I want to pause automatic gate progression, so that I can
    inspect each state transition manually.
31. As a tester, I want to inject network delay or polling delay, so that the
    controller behavior around slow server responses can be evaluated.
32. As a tester, I want to disconnect the simulator from the network, so that
    server-unavailable behavior can be tested safely.
33. As a tester, I want to send malformed or unknown RFID values, so that
    server denial and event history can be verified.
34. As a tester, I want to run multiple attempts sequentially, so that attempt
    correlation and stale-result handling can be tested.
35. As a tester, I want every attempt to have a unique attempt ID, so that
    responses from separate attempts cannot be mixed.
36. As a system owner, I want the simulator to support every provisioned gate
    identity, so that centralized multi-village routing can be tested.
37. As a system owner, I want no village or gate ID to be trusted from an
    editable simulator field, so that the test client follows controller
    authentication boundaries.
38. As a developer, I want request and response logs exportable without secrets,
    so that failures can be attached to bug reports.
39. As a developer, I want the simulator to clearly distinguish client delay,
    server delay, camera delay, recognition delay, and gate delay, so that
    performance investigations are actionable.
40. As a developer, I want the simulator to show that no physical hardware is
    connected, so that a test cannot be mistaken for a real gate operation.

## Implementation Decisions

- The Android application is standalone. It is not a web view, a Bash wrapper,
  an extension of the server UI, or a replacement for the ESP8266 firmware.
- The simulator implements a local controller state machine with states that
  mirror the production controller: idle/closed, vehicle-present/waiting for
  RFID, recognizing, opening delay, open/waiting for IR, closing, and fault or
  denied/kept-closed states.
- The simulator communicates with the existing controller endpoints using the
  provisioned controller ID and controller key. It must use TLS by default for
  remote URLs and must not print the key.
- The simulator sends a heartbeat before or during a test attempt and reports
  the same sensor and output fields as the real controller.
- Setting the loop to vehicle-present creates an attempt ID, sends the server
  capture request, starts the RFID window, and begins authorization polling.
- RFID submission is optional and uses the same attempt ID as the capture
  request. The UI must support RFID arriving before or after server plate
  recognition.
- The simulator must not call a barrier command endpoint. Gate outputs are
  simulated locally after the server returns authorization, because the
  simulator has no physical outputs.
- Authorization is server-owned. The simulator must not perform vehicle lookup,
  plate lookup, RFID lookup, or access decisions locally.
- The server API response should expose nullable timing fields for capture job
  queue, recognition processing, and total server job duration. Missing timing
  values are displayed as pending rather than zero.
- The simulator records monotonic client timestamps around every HTTP request.
  Wall-clock timestamps may be displayed for logs, but duration calculations
  use a monotonic clock.
- The timeline must identify at least: heartbeat request, capture request,
  RFID request, each authorization poll, first pending response, final response,
  capture-to-decision duration, and simulated gate-state durations.
- The UI must show the latest raw/annotated frame URL supplied by the server when
  available, while making clear that the simulator itself does not run YOLO or
  OCR.
- The UI must provide automatic mode and step/manual mode. Automatic mode
  progresses through simulated gate states using configurable production
  defaults; manual mode waits for explicit operator actions.
- The default simulated sequence is: vehicle present → capture request → RFID
  window/polling → authorized → opening delay → green/open → IR clear → closing
  → closed. Denied, failed, and timeout results remain closed.
- Safety behavior must be fail-closed: an IR beam blocked during closing must
  prevent a closed-state transition and expose the reason in the timeline.
- A vehicle leaving before authorization must end the simulated attempt without
  opening the gate, while preserving all server evidence already received.
- The simulator must support configurable polling interval, RFID retry interval,
  authorization timeout, opening delay, open hold time, closing delay, and
  optional injected network delay. Defaults must be documented and match the
  current firmware/server contract where applicable.
- Each test session must show controller ID, gate identity resolved by the
  server, attempt ID, current simulated sensor/output state, and final server
  decision.
- Logs must be redacted by construction. They must never include controller
  keys, camera URLs, camera passwords, worker keys, session cookies, or
  authorization headers.
- The simulator must use a clear “simulation only” indicator and require no
  physical hardware permissions beyond normal Android network access.
- No server database schema is required for simulator-only state. The server’s
  existing event and camera-job history remains the source of truth for access
  attempts.
- If server API additions are needed, they must be additive and backward
  compatible with existing ESP8266 firmware.

## Testing Decisions

- Test the highest seam: launch a simulator session against a controlled server
  test environment and assert the observable state timeline, HTTP requests,
  server responses, timing fields, and final simulated gate state.
- Use a fake HTTP server or request interceptor for deterministic Android tests.
  Assert request method, endpoint, authenticated controller identity, attempt
  correlation, form fields, polling cadence, and safe handling of HTTP errors.
- Add contract tests proving simulator requests remain compatible with the
  existing controller API and firmware expectations.
- Add state-machine tests for loop clear/present, RFID before/after plate,
  authorized, denied, unreadable, timeout, network failure, retry, and reset.
- Add gate-state tests proving authorized attempts show opening, open, green,
  closing, and closed; denied attempts never show open; and safety-beam
  interruption blocks unsafe closing.
- Add timing tests using a fake monotonic clock. Verify client round-trip,
  capture-to-decision, poll interval, server queue, server processing, and gate
  transition durations are displayed in the correct timeline entries.
- Add tests proving delayed and out-of-order responses cannot authorize a new
  attempt or mutate the state of a previous attempt.
- Add redaction tests proving logs and exported sessions contain no controller
  key, cookie, camera credential, or authorization header.
- Add tests proving changing the selected controller changes only the simulator
  identity and never permits cross-village data access.
- Add tests for offline mode, TLS errors, invalid server URLs, authentication
  failure, invalid controller assignment, server 4xx/5xx responses, malformed
  JSON, and incomplete timing fields.
- Add an instrumented integration test against the remote server using a
  dedicated test controller identity and a known test camera/vehicle. The test
  must not open physical hardware and must clean up or clearly label generated
  test events.
- Retain the existing server camera, model preflight, recognition, RFID,
  multitenancy, and controller API tests. The simulator must test the public
  contract rather than duplicate server authorization logic.
- Manual acceptance requires: start session, heartbeat, set loop present,
  observe capture request, observe YOLO/OCR result, test RFID-only and
  plate-only paths, force denial, block IR during closing, reset, and export a
  redacted timing timeline.

## Out of Scope

- Running YOLO, OCR, camera capture, or vehicle authorization inside Android.
- Replacing or modifying the ESP8266 firmware as part of the simulator feature.
- Controlling a real barrier, traffic light, RFID reader, loop detector, IR
  beam, or other physical equipment.
- Building a general-purpose Android gate-control product for production use.
- Adding live video streaming or continuous recording to the simulator.
- Bypassing controller authentication, gate binding, village isolation, or
  server-side authorization.
- Creating a second authorization implementation in the Android app.
- Automatically creating, provisioning, rotating, or displaying controller
  credentials from the simulator.
- Guaranteeing that simulated timing exactly equals physical timing; the app
  exposes configurable and measured timing for development comparison.
- Publishing the app to an app store in the first implementation.

## Further Notes

The Android simulator is a development and operations tool. Its most important
contract is behavioral fidelity with the NodeMCU’s HTTP sequence, not visual
fidelity to the existing web dashboard. The simulator should make the complete
distributed workflow visible while keeping the server as the only authority for
camera recognition and access decisions.

The existing issue-tracker setup and `ready-for-agent` label configuration are
not available in the repository context, so this specification is stored
locally until the project tracker is connected.
