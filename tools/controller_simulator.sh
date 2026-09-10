#!/usr/bin/env bash
set -euo pipefail

# Exercise the real NodeMCU HTTP contract without connecting a controller or
# moving the barrier. The server still performs camera capture and recognition.

server_url="${PLATE_SERVER_URL:-https://server.mlbserver.uk}"
controller_id="${PLATE_CONTROLLER_ID:-}"
controller_key="${PLATE_CONTROLLER_KEY:-}"
rfid="${PLATE_SIMULATOR_RFID:-}"
poll_seconds="${PLATE_SIMULATOR_POLL_SECONDS:-1}"
max_polls="${PLATE_SIMULATOR_MAX_POLLS:-12}"

if [[ -z "$controller_id" || -z "$controller_key" ]]; then
  echo "Set PLATE_CONTROLLER_ID and PLATE_CONTROLLER_KEY before running this simulator." >&2
  exit 2
fi
if ! [[ "$poll_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "PLATE_SIMULATOR_POLL_SECONDS must be a positive number." >&2
  exit 2
fi
if ! [[ "$max_polls" =~ ^[0-9]+$ ]] || (( max_polls < 1 )); then
  echo "PLATE_SIMULATOR_MAX_POLLS must be a positive integer." >&2
  exit 2
fi

api() {
  local path="$1"
  shift
  curl --silent --show-error --fail-with-body \
    --connect-timeout 10 --max-time 20 \
    -H "Accept: application/json" \
    -H "X-Controller-Key: $controller_key" \
    --data-urlencode "controller_id=$controller_id" \
    "$server_url$path" "$@"
}

pretty_json() {
  python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin), indent=2))'
}

now_ms() {
  python3 -c 'import time; print(time.monotonic_ns() // 1000000)'
}

attempt_uid="sim-$(date -u +%Y%m%dT%H%M%SZ)-$$"
echo "Testing server: $server_url"
echo "Controller: $controller_id"
echo "Attempt: $attempt_uid"

echo
echo "1. Sending controller heartbeat..."
request_started="$(now_ms)"
heartbeat_response="$(api /api/rfid-controller/status \
  --data-urlencode "gate_state=idle_closed" \
  --data-urlencode "rfid_connected=true" \
  --data-urlencode "loop_active=false" \
  --data-urlencode "ir_blocked=false" \
  --data-urlencode "barrier_open=false" \
  --data-urlencode "traffic_green=false" \
  --data-urlencode "credential_unrecognized=false")"
request_elapsed=$(($(now_ms) - request_started))
echo "  Client round trip: ${request_elapsed} ms"
printf '%s\n' "$heartbeat_response" | pretty_json

echo
echo "2. Requesting a server camera capture..."
capture_started="$(now_ms)"
capture_response="$(api /api/controller/capture-request \
  --data-urlencode "attempt_uid=$attempt_uid")"
capture_elapsed=$(($(now_ms) - capture_started))
echo "  Client round trip: ${capture_elapsed} ms"
printf '%s\n' "$capture_response" | pretty_json

if [[ -n "$rfid" ]]; then
  echo
  echo "3. Sending RFID recognition..."
  rfid_started="$(now_ms)"
  rfid_response="$(api /api/rfid-controller/recognitions \
    --data-urlencode "attempt_uid=$attempt_uid" \
    --data-urlencode "rfid=$rfid")"
  rfid_elapsed=$(($(now_ms) - rfid_started))
  echo "  Client round trip: ${rfid_elapsed} ms"
  printf '%s\n' "$rfid_response" | pretty_json
else
  echo
  echo "3. No RFID supplied; waiting for plate recognition only."
fi

echo
echo "4. Polling server authorization result..."
workflow_started="$(now_ms)"
for ((poll = 1; poll <= max_polls; poll++)); do
  poll_started="$(now_ms)"
  result="$(api /api/controller/access-result \
    --data-urlencode "attempt_uid=$attempt_uid")"
  poll_elapsed=$(($(now_ms) - poll_started))
  status="$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", "unknown"))')"
  echo "Poll $poll: $status (client round trip ${poll_elapsed} ms)"
  if [[ "$status" == "authorized" || "$status" == "denied" ]]; then
    printf '%s\n' "$result" | pretty_json
    workflow_elapsed=$(($(now_ms) - workflow_started))
    total_elapsed=$(($(now_ms) - capture_started))
    echo "Timing summary: capture-to-decision ${total_elapsed} ms; authorization polling ${workflow_elapsed} ms"
    if [[ "$status" == "authorized" ]]; then
      echo "PASS: server authorized the attempt; a real NodeMCU would open the barrier."
      exit 0
    fi
    echo "DENIED: server kept the gate closed."
    exit 1
  fi
  sleep "$poll_seconds"
done

echo "TIMEOUT: no final server decision within ${max_polls} polls." >&2
exit 1
