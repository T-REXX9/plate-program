from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from camera import (  # noqa: E402
    CameraConfig,
    CameraError,
    capture_frame,
    camera_endpoint_env_name,
    camera_is_configured,
    validate_camera_config,
    validate_camera_uid,
)


class CameraConfigTests(unittest.TestCase):
    @patch.dict("camera.os.environ", {}, clear=True)
    def test_environment_endpoint_counts_as_configured(self) -> None:
        with patch.dict(
            "camera.os.environ",
            {"CAMERA_ENDPOINT_GATE_1_CAMERA": "rtsp://camera/stream1"},
            clear=True,
        ):
            self.assertTrue(camera_is_configured("gate-1-camera", None))

    @patch.dict("camera.os.environ", {}, clear=True)
    def test_missing_endpoint_is_not_configured(self) -> None:
        self.assertFalse(camera_is_configured("gate-1-camera", None))

    def test_camera_endpoint_env_name_is_stable(self) -> None:
        self.assertEqual(
            camera_endpoint_env_name("village-a:main-camera"),
            "CAMERA_ENDPOINT_VILLAGE_A_MAIN_CAMERA",
        )

    def test_rtsp_endpoint_is_accepted(self) -> None:
        config = validate_camera_config(
            CameraConfig("gate-1-camera", "rtsp", "rtsp://camera.example/stream1")
        )
        self.assertEqual(config.transport, "rtsp")

    def test_invalid_transport_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_camera_config(CameraConfig("gate-1-camera", "rtmp", "rtmp://camera"))

    def test_network_transport_requires_matching_scheme(self) -> None:
        with self.assertRaises(ValueError):
            validate_camera_config(
                CameraConfig("gate-1-camera", "rtsp", "https://camera.example/stream")
            )

    def test_camera_id_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            validate_camera_uid("../other-camera")


class RtspCaptureTests(unittest.TestCase):
    @patch("camera.subprocess.run")
    def test_rtsp_capture_forces_tcp_and_returns_jpeg(self, run: Mock) -> None:
        run.return_value = Mock(returncode=0, stdout=b"\xff\xd8\xffjpeg", stderr=b"")

        image = capture_frame(
            CameraConfig("gate-1-camera", "rtsp", "rtsp://user:secret@camera/stream1")
        )

        self.assertTrue(image.startswith(b"\xff\xd8\xff"))
        command = run.call_args.args[0]
        self.assertIn("-rtsp_transport", command)
        self.assertIn("tcp", command)
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("camera.subprocess.run")
    def test_empty_capture_is_reported_without_leaking_command(self, run: Mock) -> None:
        run.return_value = Mock(returncode=1, stdout=b"", stderr=b"authentication failed")

        with self.assertRaisesRegex(CameraError, "did not return a frame"):
            capture_frame(
                CameraConfig("gate-1-camera", "rtsp", "rtsp://user:secret@camera/stream1")
            )


class HttpCameraTests(unittest.TestCase):
    @patch("camera.urllib.request.urlopen")
    def test_http_snapshot_returns_first_jpeg_from_response(self, urlopen: Mock) -> None:
        response = Mock()
        response.read.return_value = b"headers\xff\xd8\xffframe\xff\xd9trailer"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        image = capture_frame(
            CameraConfig("gate-1-camera", "http_mjpeg", "https://camera.example/snapshot")
        )

        self.assertEqual(image, b"\xff\xd8\xffframe\xff\xd9")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)

    @patch("camera.urllib.request.urlopen")
    def test_http_camera_rejects_response_without_complete_jpeg(self, urlopen: Mock) -> None:
        response = Mock()
        response.read.return_value = b"not an image"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response

        with self.assertRaisesRegex(CameraError, "complete JPEG"):
            capture_frame(
                CameraConfig("gate-1-camera", "http_mjpeg", "https://camera.example/stream")
            )


if __name__ == "__main__":
    unittest.main()
