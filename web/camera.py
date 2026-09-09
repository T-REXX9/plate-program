"""Camera transport adapters used by the centralized capture service.

The server owns recognition, while this module owns obtaining one frame from a
gate-bound camera.  Adapters deliberately return bytes so the recognition
engine can remain independent of camera vendor and transport.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


class CameraError(RuntimeError):
    """A camera could not provide a valid frame."""


@dataclass(frozen=True)
class CameraConfig:
    camera_uid: str
    transport: str
    endpoint_url: str | None


SUPPORTED_TRANSPORTS = {"rtsp", "http_mjpeg", "usb", "onvif", "agent"}


def camera_endpoint_env_name(camera_uid: str) -> str:
    """Return the environment variable name for a private camera endpoint."""
    normalized = re.sub(r"[^A-Za-z0-9]", "_", camera_uid).upper()
    return f"CAMERA_ENDPOINT_{normalized}"


def camera_is_configured(camera_uid: str | None, endpoint_url: str | None) -> bool:
    """Return whether a camera has an endpoint without exposing deployment secrets."""
    if (endpoint_url or "").strip():
        return True
    if not camera_uid:
        return False
    return bool(os.environ.get(camera_endpoint_env_name(camera_uid), "").strip())


def validate_camera_uid(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate or len(candidate) > 64:
        raise ValueError("Camera ID must contain 1 to 64 characters.")
    if not all(character.isalnum() or character in "._:-" for character in candidate):
        raise ValueError("Camera ID may contain only letters, numbers, dot, dash, colon, or underscore.")
    return candidate


def validate_camera_config(config: CameraConfig) -> CameraConfig:
    camera_uid = validate_camera_uid(config.camera_uid)
    if config.transport not in SUPPORTED_TRANSPORTS:
        raise ValueError(f"Unsupported camera transport: {config.transport}")
    endpoint = (config.endpoint_url or "").strip() or None
    if config.transport in {"rtsp", "http_mjpeg", "onvif"}:
        if endpoint is None:
            raise ValueError("A network camera requires an endpoint URL.")
        scheme = urlsplit(endpoint).scheme.lower()
        if config.transport == "rtsp" and scheme != "rtsp":
            raise ValueError("RTSP cameras require an rtsp:// endpoint.")
        if config.transport == "http_mjpeg" and scheme not in {"http", "https"}:
            raise ValueError("HTTP/MJPEG cameras require an http:// or https:// endpoint.")
        if config.transport == "onvif" and scheme not in {"http", "https"}:
            raise ValueError("ONVIF cameras require an http:// or https:// endpoint.")
    return CameraConfig(camera_uid, config.transport, endpoint)


def capture_rtsp_frame(endpoint_url: str, timeout_seconds: int = 10) -> bytes:
    """Capture one JPEG using FFmpeg and force RTSP-over-TCP.

    The URL is passed as an argv value, never through a shell.  Credentials
    therefore cannot be interpreted as shell syntax.  Callers must still avoid
    logging the endpoint because it may contain camera credentials.
    """

    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("Camera timeout must be between 1 and 30 seconds.")
    ffmpeg = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        endpoint_url,
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        raise CameraError("FFmpeg is not installed on the server.") from error
    except subprocess.TimeoutExpired as error:
        raise CameraError("Camera capture timed out.") from error
    if result.returncode != 0 or not result.stdout:
        raise CameraError("The camera did not return a frame.")
    if not result.stdout.startswith(b"\xff\xd8\xff"):
        raise CameraError("The camera returned a non-JPEG frame.")
    return result.stdout


def capture_frame(config: CameraConfig, timeout_seconds: int = 10) -> bytes:
    """Capture a frame through the configured transport adapter."""

    validated = validate_camera_config(config)
    if validated.transport == "rtsp":
        assert validated.endpoint_url is not None
        return capture_rtsp_frame(validated.endpoint_url, timeout_seconds)
    if validated.transport == "http_mjpeg":
        assert validated.endpoint_url is not None
        return capture_http_frame(validated.endpoint_url, timeout_seconds)
    if validated.transport == "usb":
        return capture_usb_frame(validated.endpoint_url)
    raise CameraError(
        f"Camera transport '{validated.transport}' is registered but its adapter is not installed yet."
    )


def _first_jpeg(contents: bytes) -> bytes:
    start = contents.find(b"\xff\xd8\xff")
    end = contents.find(b"\xff\xd9", start + 3) if start >= 0 else -1
    if start < 0 or end < 0:
        raise CameraError("The camera response did not contain a complete JPEG frame.")
    return contents[start:end + 2]


def capture_http_frame(endpoint_url: str, timeout_seconds: int = 10) -> bytes:
    """Capture one JPEG from a snapshot URL or the first MJPEG frame."""

    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("Camera timeout must be between 1 and 30 seconds.")
    request = urllib.request.Request(endpoint_url, headers={"Accept": "image/jpeg, multipart/x-mixed-replace"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            contents = response.read(10 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError) as error:
        raise CameraError("The HTTP camera did not return a frame.") from error
    if len(contents) > 10 * 1024 * 1024:
        raise CameraError("The camera frame exceeded the maximum size.")
    return _first_jpeg(contents)


def capture_usb_frame(endpoint_url: str | None) -> bytes:
    """Capture one JPEG from a camera physically attached to the server."""

    try:
        import cv2
    except ImportError as error:
        raise CameraError("OpenCV is required for USB camera capture.") from error
    try:
        device = int(endpoint_url or "0")
    except ValueError as error:
        raise CameraError("USB camera endpoint must be a numeric device index.") from error
    capture = cv2.VideoCapture(device)
    try:
        if not capture.isOpened():
            raise CameraError("The USB camera could not be opened.")
        success, frame = capture.read()
        if not success:
            raise CameraError("The USB camera did not return a frame.")
        success, encoded = cv2.imencode(".jpg", frame)
        if not success:
            raise CameraError("The USB camera frame could not be encoded as JPEG.")
        return encoded.tobytes()
    finally:
        capture.release()
