# Camera Diagnostics Panel

## Problem Statement

The Overview page currently places a manual camera frame capture beside the
operational dashboard. That frame is only a connectivity test: it is not a
plate-recognition attempt and does not create an access event. Showing it beside
the latest access-event area makes the result look like the latest recognized
vehicle frame, even though no vehicle decision or plate result exists.

Operators need a clearly separated way to verify that a gate-bound camera can
be reached and can provide a frame, without polluting access history or
misrepresenting a diagnostic frame as a real vehicle event.

## Solution

Remove the manual camera capture control and its standalone preview from the
Overview page. Add a dedicated Camera Diagnostics panel to the operational
hardware/diagnostics area.

The panel must operate against the camera bound to the selected active village
and gate, provide a manual connectivity test, show the most recent diagnostic
result and frame, and clearly distinguish diagnostic captures from access
events. A successful test confirms that the server can obtain and store a
camera frame; it must not create a plate event, authorization decision, or
access-log entry.

## User Stories

1. As a village administrator, I want camera testing removed from Overview, so that the latest access event area only represents real access activity.
2. As a security guard, I want to find camera testing in a dedicated diagnostics panel, so that operational checks are separate from access decisions.
3. As a village administrator, I want to test the camera bound to the selected gate, so that I can verify the correct camera without ambiguity.
4. As a system owner, I want the selected village boundary applied to diagnostics, so that cameras from another village cannot be tested accidentally.
5. As a gate operator, I want one action to test camera connectivity, so that I can quickly confirm whether the server can reach the camera.
6. As a gate operator, I want a successful test to show the captured frame in the diagnostics panel, so that I can visually confirm that the camera is returning usable imagery.
7. As a gate operator, I want the test result to show when it was performed, so that I can distinguish a current result from an old one.
8. As a gate operator, I want the panel to show a clear success state, so that I know the frame was captured and stored successfully.
9. As a gate operator, I want the panel to show a clear failure state and useful safe diagnostic text, so that I know whether the camera is unavailable or misconfigured.
10. As a gate operator, I want the test button disabled when no active camera is bound to the selected gate, so that I do not start a request that cannot succeed.
11. As a gate operator, I want the button to show progress while a test is running, so that I do not submit duplicate requests.
12. As a gate operator, I want the panel to recover after a failed test, so that I can retry after correcting the camera or network.
13. As a village administrator, I want a diagnostic capture to avoid creating an access event, so that test images do not affect entries, authorized counts, denied counts, or access history.
14. As a village administrator, I want a diagnostic capture to avoid plate recognition and authorization, so that testing connectivity cannot open or deny a gate.
15. As a village administrator, I want a diagnostic frame to remain separate from the latest event frame, so that the event viewer never displays a test image as a vehicle decision.
16. As a village administrator, I want the panel to use the server-owned camera transport adapters, so that testing works for every supported camera connection form.
17. As a village administrator, I want remote camera endpoints to be tested from the server, so that the result reflects the actual production capture path.
18. As a village administrator, I want camera credentials and private endpoints kept out of the UI and logs, so that diagnostics do not disclose secrets.
19. As a system owner, I want the panel to follow the active village and gate selectors, so that the same centralized server can safely diagnose cameras across many villages.
20. As a village administrator, I want a test result to remain available after normal dashboard polling, so that automatic refresh does not make the result disappear.
21. As a village administrator, I want an explicit empty state before the first test, so that the panel explains what the diagnostic action does.
22. As a village administrator, I want the diagnostic panel to work without a connected controller, so that camera/network maintenance can be performed independently.
23. As a security guard, I want diagnostic permissions to follow the existing gate-operation permissions, so that unauthorized users cannot probe camera endpoints.
24. As a system owner, I want diagnostics to respect inactive villages, gates, and cameras, so that disabled equipment is never treated as operational.
25. As a developer, I want the diagnostic API response to identify success, failure, result time, and frame resource without exposing the private endpoint, so that the UI can render the result reliably.
26. As a developer, I want the existing access-event pipeline to remain unchanged by diagnostics, so that controller-triggered plate/RFID behavior continues to use the production event flow.

## Implementation Decisions

- Remove the Overview camera-test action, standalone preview, and related
  Overview-only styling and client rendering.
- Add a Camera Diagnostics panel to the Hardware/diagnostics experience. The
  panel should be visible only to roles that may perform the existing camera
  test operation; read-only users may see status where the current permission
  model allows it but must not trigger a test.
- Reuse the existing server-side camera capture adapter and camera-bound gate
  lookup rather than adding a second transport implementation.
- The test targets exactly one active camera bound to the selected active gate
  and village. It must not accept an arbitrary camera endpoint from the
  browser.
- The test request captures one frame on the server, stores it as a diagnostic
  image, updates camera health metadata, and returns a safe result payload.
- The result payload must include a success indicator, a user-safe message, a
  cache-busting result version/time, and a protected frame URL when successful.
- The diagnostic frame endpoint must enforce login, role authorization, active
  village scope, active gate scope, and camera identity validation.
- Diagnostic captures must not insert or update access events, capture jobs,
  plate results, RFID results, gate commands, or daily access counters.
- The latest access-event viewer must continue to read only from access-event
  records and their event image variants.
- Automatic dashboard synchronization must not clear the diagnostic panel’s
  current result. A full page reload may restore the latest diagnostic result
  only if it is safely scoped to the current session and tenant.
- The panel must show a neutral state before testing, a busy state during the
  request, a success state with the frame after a successful request, and a
  failure state with retry affordance after an unsuccessful request.
- Diagnostic labels must describe the result as a camera connectivity/frame
  test and must not call it a plate event, access event, authorization, or
  vehicle decision.
- The implementation must preserve the existing server-owned recognition flow:
  controller-triggered attempts continue through capture jobs, recognition,
  evidence correlation, authorization, and gate response.

## Testing Decisions

- Test observable behavior at the highest practical seam: an authenticated
  diagnostic request followed by the diagnostics panel result and protected
  frame resource.
- Add a regression test proving a successful diagnostic request returns a frame
  resource but creates no access event, recognition job, or authorization
  decision.
- Add authorization and tenant-isolation tests proving a user cannot test or
  retrieve a camera outside the active village or selected gate.
- Add failure-path tests for missing binding, invalid camera configuration,
  capture timeout, invalid frame data, and storage failure. Each must update
  camera health consistently and return a retryable diagnostic result.
- Add a frontend regression test for the state transition from idle to busy to
  success/failure, including preservation through dashboard polling.
- Retain the existing camera adapter tests for RTSP-over-TCP, HTTP/MJPEG, and
  endpoint validation; diagnostics must use those adapters without bypassing
  their validation.
- Retain the existing access-event and multi-village tests to prove the
  diagnostic feature does not alter authorization, history, or tenant
  isolation.
- Verify JavaScript syntax, Python compilation, the full existing test suite,
  and a deployed smoke test against the authenticated diagnostics endpoint.
- Tests must assert user-visible/API behavior rather than private DOM structure
  or implementation-specific helper names.

## Out of Scope

- Running plate detection or OCR from the diagnostics action.
- Creating synthetic access events or test entries in access history.
- Opening, closing, or otherwise commanding a gate during camera testing.
- Changing the NodeMCU firmware, sensor logic, RFID flow, or controller API.
- Changing camera transport support, camera credential storage, or Cloudflare
  Tunnel configuration.
- Streaming live video; this feature tests one server-side frame capture.
- Replacing the production controller-triggered capture and recognition flow.

## Further Notes

The existing manual capture behavior is useful and should be retained as a
diagnostic capability; only its placement and labeling need to change. The
screen shown in the reported issue is therefore a UI information-architecture
problem rather than evidence that the camera frame belongs in the latest
access event.

The project issue-tracker instructions are currently absent, so publication of
this specification requires the project tracker setup to be restored before a
`ready-for-agent` issue can be created.
