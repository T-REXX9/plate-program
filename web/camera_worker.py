"""Background worker for centralized camera capture and plate recognition."""

from __future__ import annotations

import argparse
import os
import time
import urllib.request
import uuid
from pathlib import Path

from camera import CameraConfig, CameraError, camera_endpoint_env_name, capture_frame
from recognition import encode_jpeg, recognize_frame

from app import DatabaseConnection, PROJECT_DIR


def _multipart(fields: dict[str, str], filename: str, contents: bytes) -> tuple[bytes, str]:
    boundary = f"----PlateProgram{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="annotated_image"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: image/jpeg\r\n\r\n",
        contents,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _submit_result(job: dict, result, server_url: str, worker_key: str) -> None:
    fields = {
        "plate": result.plate,
        "detector_confidence": str(result.detector_confidence),
        "ocr_confidence": str(result.ocr_confidence),
    }
    body, content_type = _multipart(fields, "annotated.jpg", encode_jpeg(result.annotated))
    request = urllib.request.Request(
        f"{server_url.rstrip('/')}/api/internal/camera-jobs/{job['id']}/recognition",
        data=body,
        headers={
            "Content-Type": content_type,
            "X-Camera-Worker-Key": worker_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status < 200 or response.status >= 300:
            raise CameraError(f"Recognition endpoint returned HTTP {response.status}.")


def claim_job(connection: DatabaseConnection) -> dict | None:
    connection.execute("START TRANSACTION")
    # A controller must never wait indefinitely for a camera or recognizer.
    # Expiry is performed by the same worker that processes jobs, so an
    # unavailable camera still produces a deterministic deny result.
    connection.execute(
        """
        UPDATE camera_capture_jobs
        SET status = 'timed_out', completed_at = CURRENT_TIMESTAMP,
            error_message = 'Camera recognition exceeded 10 seconds'
        WHERE status IN ('pending', 'capturing', 'captured')
          AND requested_at < TIMESTAMPADD(SECOND, -10, CURRENT_TIMESTAMP)
        """
    )
    connection.execute(
        """
        UPDATE access_events e
        JOIN camera_capture_jobs j ON j.attempt_uid COLLATE utf8mb4_unicode_ci =
            e.attempt_uid COLLATE utf8mb4_unicode_ci
        SET e.decision = 'denied', e.gate_action = 'kept_closed',
            e.notes = CONCAT_WS('; ', e.notes, 'camera_timeout')
        WHERE j.status = 'timed_out' AND e.decision = 'unreadable'
        """
    )
    job = connection.execute(
        """
        SELECT j.id, j.attempt_uid, j.village_id, j.gate_id, j.controller_uid,
               j.status, c.camera_uid, c.transport, c.endpoint_url,
               v.village_uid, g.gate_uid
        FROM camera_capture_jobs j
        JOIN cameras c ON c.camera_uid = j.camera_uid
        JOIN villages v ON v.id = j.village_id
        JOIN gates g ON g.id = j.gate_id
        WHERE c.is_active = 1 AND (
            (c.transport = 'rtsp' AND j.status = 'pending')
            OR (c.transport = 'agent' AND j.status = 'captured')
        )
        ORDER BY j.requested_at, j.id
        LIMIT 1 FOR UPDATE
        """
    ).fetchone()
    if job is None:
        connection.commit()
        return None
    if job["status"] == "pending":
        connection.execute(
            "UPDATE camera_capture_jobs SET status = 'capturing', started_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job["id"],),
        )
    connection.commit()
    return job


def fail_job(connection: DatabaseConnection, job: dict, message: str) -> None:
    connection.execute(
        """
        UPDATE camera_capture_jobs
        SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ?
        WHERE id = ? AND status IN ('pending', 'capturing', 'captured')
        """,
        (message[:500], job["id"]),
    )
    connection.execute(
        """
        UPDATE cameras
        SET status = 'degraded', last_seen_at = CURRENT_TIMESTAMP, last_error = ?
        WHERE camera_uid = ?
        """,
        (message[:500], job["camera_uid"]),
    )
    connection.execute(
        """
        UPDATE access_events
        SET decision = 'denied', gate_action = 'kept_closed',
            notes = CONCAT_WS('; ', notes, 'camera_failed')
        WHERE village_id = ? AND gate_id = ? AND controller_uid = ?
          AND attempt_uid = ? AND decision = 'unreadable'
        """,
        (
            job["village_id"], job["gate_id"], job["controller_uid"],
            job["attempt_uid"],
        ),
    )
    connection.commit()


def process_job(connection: DatabaseConnection, job: dict) -> None:
    if job["transport"] == "agent":
        image_path = connection.execute(
            "SELECT image_path FROM camera_capture_jobs WHERE id = ?",
            (job["id"],),
        ).fetchone()["image_path"]
        if not image_path:
            raise CameraError("Camera agent completed the job without an image.")
        frame_bytes = (PROJECT_DIR / image_path).read_bytes()
    else:
        endpoint_url = job["endpoint_url"] or os.environ.get(
            camera_endpoint_env_name(job["camera_uid"]), ""
        ).strip()
        frame_bytes = capture_frame(
            CameraConfig(job["camera_uid"], job["transport"], endpoint_url),
            timeout_seconds=10,
        )
    detector_model = os.environ.get(
        "PLATE_DETECTOR_MODEL", str(PROJECT_DIR / "models" / "license_plate_detector.onnx")
    )
    recognizer_model = os.environ.get(
        "PLATE_RECOGNIZER_MODEL", str(PROJECT_DIR / "models" / "en_PP-OCRv5_rec_mobile.onnx")
    )
    result = recognize_frame(frame_bytes, detector_model, recognizer_model)
    server_url = os.environ.get("PLATE_SERVER_URL", "http://127.0.0.1:8080")
    worker_key = os.environ.get("CAMERA_WORKER_KEY", "").strip()
    if not worker_key:
        raise CameraError("CAMERA_WORKER_KEY is not configured.")
    _submit_result(job, result, server_url, worker_key)


def run(poll_seconds: float, once: bool) -> None:
    connection = DatabaseConnection()
    try:
        while True:
            job = claim_job(connection)
            if job is not None:
                try:
                    process_job(connection, job)
                except Exception as error:  # worker must continue serving other gates
                    fail_job(connection, job, str(error))
            elif once:
                return
            else:
                time.sleep(poll_seconds)
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process centralized Plate Program camera jobs.")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit.")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    arguments = parser.parse_args()
    run(max(0.1, arguments.poll_seconds), arguments.once)
