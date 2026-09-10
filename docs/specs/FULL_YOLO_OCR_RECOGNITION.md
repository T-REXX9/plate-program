# Fully Operational YOLO and OCR Plate Recognition

## Problem Statement

The server contains a recognition module and a background camera worker, but
YOLO-style plate detection and OCR are not yet a complete, verifiable,
production-ready capability. Recognition depends on model files and runtime
dependencies that are not reliably provisioned with the application, and there
is no end-to-end proof that a real camera frame becomes a correct plate result
and annotated image on the deployed server.

As a result, a camera can be reachable and a frame can be captured while the
system still produces no usable plate, an unreadable result, or a failed job.
The server needs a fully installed, configured, observable, and tested
server-owned recognition pipeline.

## Solution

Complete the server-side recognition pipeline from captured frame to plate
decision:

1. Provision a supported license-plate detector model and OCR model as part of
   installation/deployment, with integrity verification and explicit versions.
2. Implement reliable detector inference, plate crop extraction, OCR decoding,
   confidence handling, normalization, and annotated-image generation.
3. Process controller-triggered capture jobs through YOLO and OCR on the
   centralized server, then submit the result to the existing authorization
   flow.
4. Add startup validation, worker health visibility, bounded timeouts, safe
   failure behavior, and actionable diagnostics.
5. Prove the complete path with deterministic fixtures, model smoke tests,
   integration tests, and a deployed end-to-end test.

The result must remain server-owned: the NodeMCU requests a capture and handles
sensors and gate outputs, while the server owns image capture, detection, OCR,
vehicle lookup, authorization, event history, and annotated evidence.

## User Stories

1. As a gate operator, I want a captured vehicle frame to be processed automatically, so that I receive a plate result without manual intervention.
2. As a gate operator, I want the detector to locate the plate in a vehicle image, so that OCR receives the correct crop rather than the entire scene.
3. As a gate operator, I want OCR to read the detected plate, so that the server can match it to a vehicle record.
4. As a gate operator, I want the recognized plate normalized consistently, so that formatting differences do not prevent a valid match.
5. As a village administrator, I want a recognized plate checked against my village’s active vehicle records, so that access decisions use the correct tenant’s data.
6. As a village administrator, I want an authorized recognized plate to produce an authorized event and gate response, so that valid vehicles can enter.
7. As a village administrator, I want an unrecognized or unauthorized plate to produce a denied event, so that unknown vehicles remain blocked.
8. As a gate operator, I want a clear annotated image with the detected plate marked, so that I can verify what the detector and OCR recognized.
9. As a gate operator, I want the original frame and annotated frame retained with the event, so that later investigations can compare source evidence with recognition output.
10. As a gate operator, I want detector and OCR confidence values recorded, so that uncertain results can be investigated.
11. As a gate operator, I want unreadable plates represented explicitly, so that an OCR failure is not mistaken for an empty or authorized plate.
12. As a gate operator, I want a failed recognition job to fail closed, so that recognition errors never open the gate.
13. As a gate operator, I want the worker to continue processing later jobs after one bad frame or model error, so that one failure does not stop all gates.
14. As a system administrator, I want model files installed automatically, so that a fresh server does not silently run without recognition.
15. As a system administrator, I want model downloads verified by checksum, so that corrupted or tampered models are rejected.
16. As a system administrator, I want model versions recorded, so that deployed recognition behavior can be reproduced.
17. As a system administrator, I want startup to report missing or invalid models clearly, so that deployment problems are found before vehicles arrive.
18. As a system administrator, I want the worker health state visible, so that I know whether recognition is idle, processing, degraded, or unavailable.
19. As a system administrator, I want recognition latency recorded, so that camera, detector, OCR, and upload bottlenecks can be identified.
20. As a system administrator, I want recognition bounded by the existing ten-second access window, so that the controller receives a deterministic authorization response.
21. As a system administrator, I want inference to avoid logging camera credentials or private endpoints, so that operational logs remain safe.
22. As a village administrator, I want recognition jobs and events isolated by village and gate, so that one installation can never authorize or display another installation’s result.
23. As a system owner, I want multiple gate cameras processed concurrently or fairly queued, so that one busy gate does not starve the centralized server.
24. As a system owner, I want camera transport differences hidden behind the capture adapter, so that YOLO and OCR receive the same image contract regardless of camera type.
25. As a developer, I want detector and OCR adapters replaceable behind stable interfaces, so that models can improve without rewriting authorization or event storage.
26. As a developer, I want deterministic recognition fixtures, so that changes to preprocessing, model versions, and decoding can be regression-tested.
27. As a developer, I want malformed images and malformed model outputs handled safely, so that the worker returns a controlled failure instead of crashing.
28. As a developer, I want the end-to-end camera-job API to expose the final recognition status, so that the controller and dashboard can distinguish pending, recognized, timed-out, and failed jobs.
29. As a village administrator, I want RFID and plate evidence combined according to the existing authorization rules, so that a valid RFID can authorize when the plate is unreadable and vice versa.
30. As a village administrator, I want late evidence preserved in the event history, so that the final decision and the evidence used to reach it remain auditable.

## Implementation Decisions

- Recognition remains entirely on the centralized server. Controllers never
  run YOLO, OCR, or vehicle authorization.
- Keep a high-level recognition service boundary between frame acquisition,
  detection, OCR, annotation, event authorization, and persistence.
- Use a supported YOLO-compatible license-plate detector model with explicit
  input dimensions, confidence threshold, non-maximum suppression threshold,
  class semantics, and model version.
- Use a supported OCR model/runtime with explicit character set, blank-token
  decoding rules, image preprocessing, confidence calculation, and output
  normalization. The implementation must not assume a model output shape
  without validating it.
- Choose one production model/runtime combination and provision it through the
  installer and deployment process. Alternative model adapters may remain
  possible, but production behavior must not depend on a developer’s local
  files.
- Model artifacts must have pinned download locations, versions, checksums,
  storage locations, and a documented license review before installation.
- Startup or a preflight command must validate that the detector and OCR model
  files exist, are readable, load successfully, and match the configured
  runtime. A missing model must make recognition explicitly unavailable rather
  than silently returning normal-looking results.
- The camera worker must load models predictably and avoid reloading large
  models for every frame. If process-level model caching is used, it must be
  safe for the worker’s concurrency model.
- The worker must validate the captured image before inference, use bounded
  processing time, and return a controlled failed or timed-out job when a
  stage exceeds the access window.
- The pipeline must handle zero detections, multiple detections, low detector
  confidence, low OCR confidence, invalid crops, invalid tensors, and invalid
  decoded strings without crashing.
- Multiple detected plates must follow a documented deterministic policy. The
  initial production policy should select the highest-confidence valid plate
  crop and record that policy in the recognition result.
- Plate normalization must use the same canonical form as vehicle lookup and
  must preserve the raw OCR result separately when needed for auditability.
- Annotated output must show the selected bounding box, normalized result, and
  an explicit unreadable/no-detection label when recognition does not produce a
  usable plate.
- Recognition results must include plate value, detector confidence, OCR
  confidence, raw/annotated image references, model versions, and stage timing
  where the existing schema/API can support them without weakening tenant
  isolation.
- The existing camera-job recognition endpoint remains the integration seam
  from worker output into authorization and event persistence. It must reject
  incomplete or invalid recognition payloads safely.
- The existing fail-closed policy remains mandatory: camera, detector, OCR,
  model, storage, or callback failure cannot authorize a gate.
- The worker must continue after an individual job failure and must update
  camera/job/event health consistently.
- Recognition must preserve the existing plate/RFID correlation and OR-based
  authorization behavior. Adding YOLO/OCR must not change village ownership,
  gate binding, RFID handling, or controller commands.
- Operational logs and error messages must describe the failed stage and safe
  diagnostics without exposing camera URLs, credentials, controller keys, or
  worker secrets.
- The dashboard must distinguish camera connectivity from recognition health:
  a reachable camera is not equivalent to a working detector/OCR pipeline.
- A manual camera connectivity test remains a frame-only diagnostic and must
  not invoke YOLO/OCR or create an access event. Recognition testing uses a
  separate controlled test path or fixture.

## Testing Decisions

- Test public recognition behavior at the highest seam: submit a real captured
  frame through the camera-job pipeline and observe the resulting recognition
  payload, annotated evidence, event decision, and job status.
- Add model preflight tests proving missing, unreadable, corrupt, incompatible,
  and checksum-mismatched model artifacts are reported as unavailable.
- Add detector tests using fixed JPEG fixtures with known plate bounding boxes;
  assert observable detections and confidence thresholds rather than private
  tensor implementation details.
- Add OCR tests using fixed plate-crop fixtures with known canonical readings,
  including spacing, punctuation, ambiguous characters, blank outputs, and
  low-confidence outputs.
- Add end-to-end fixtures covering authorized plate, denied plate, unreadable
  plate, no plate detected, multiple plates, malformed image, and recognition
  timeout.
- Add tests proving annotated images contain the expected evidence and remain
  retrievable through the protected event-frame interface.
- Add tests proving recognition failures fail closed and update the job/event
  status without stopping subsequent jobs.
- Add tenant-isolation tests proving identical plates in different villages do
  not cross-authorize or cross-display.
- Add integration tests proving RFID-only, plate-only, and combined evidence
  preserve the current authorization contract.
- Add worker tests proving models are loaded once per worker lifecycle or that
  the chosen lifecycle behavior is otherwise observable and bounded.
- Add timing and timeout tests for camera capture, detection, OCR, callback, and
  the complete ten-second access window.
- Add an installation/preflight smoke test on a clean server or isolated
  environment that installs dependencies, fetches verified models, starts the
  worker, processes a fixture, and reports a recognized/denied result.
- Add a deployed smoke test using a real gate-bound camera frame where the
  environment permits it. Credentials and endpoint values must be redacted from
  captured output.
- Retain the existing camera adapter, multi-village, RFID authorization,
  camera-job, and fail-closed tests. New recognition behavior must integrate
  with those existing seams instead of replacing them with implementation-only
  unit tests.
- Run Python compilation, JavaScript checks, the full test suite, model
  preflight, and one end-to-end recognition fixture before release.

## Out of Scope

- Moving YOLO or OCR inference to the NodeMCU or any gate controller.
- Changing sensor logic, RFID acquisition, barrier safety logic, or controller
  firmware.
- Replacing the centralized server or multi-village ownership model.
- Adding a live video stream or continuous camera recording.
- Automatic retraining, dataset collection, or model-training infrastructure.
- Guaranteeing perfect recognition under glare, darkness, occlusion, motion
  blur, or an unsupported plate format; those conditions must be measured and
  reported as recognition quality limitations.
- Treating a camera connectivity test as a recognition or access event.
- Changing authorization policy beyond integrating the completed recognition
  result with the existing plate/RFID rules.

## Further Notes

The current implementation is a useful starting adapter, not evidence that
YOLO and OCR are fully operational in production. Completion requires the
models, runtime dependencies, provisioning, preflight validation, worker
observability, deterministic fixtures, and a real end-to-end recognition
verification path to ship together.

The project issue-tracker instructions are still absent, so this specification
can be stored in the repository but cannot be published with the required
`ready-for-agent` tracker label until tracker setup is restored.
