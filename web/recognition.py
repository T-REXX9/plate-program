"""Server-side license-plate detection and OCR for captured camera frames."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class RecognitionResult:
    plate: str
    detector_confidence: float
    ocr_confidence: float
    crop: np.ndarray | None
    annotated: np.ndarray


def _clean_plate(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _paddle_character(index: int) -> str:
    if 1 <= index <= 10:
        return chr(ord("0") + index - 1)
    if 11 <= index <= 36:
        return chr(ord("A") + index - 11)
    if 37 <= index <= 62:
        return chr(ord("a") + index - 37)
    return ""


def _letterbox(frame: np.ndarray, size: int = 640):
    scale = min(size / frame.shape[1], size / frame.shape[0])
    width = int(round(frame.shape[1] * scale))
    height = int(round(frame.shape[0] * scale))
    pad_x = (size - width) // 2
    pad_y = (size - height) // 2
    resized = cv2.resize(frame, (width, height))
    padded = np.full((size, size, 3), 114, dtype=np.uint8)
    padded[pad_y:pad_y + height, pad_x:pad_x + width] = resized
    return padded, scale, pad_x, pad_y


def _detect_plates(detector: cv2.dnn.Net, frame: np.ndarray):
    input_image, scale, pad_x, pad_y = _letterbox(frame)
    blob = cv2.dnn.blobFromImage(
        input_image, 1 / 255.0, (640, 640), swapRB=True, crop=False
    )
    detector.setInput(blob)
    output = detector.forward()
    if output.ndim == 3:
        rows = output[0].T if output.shape[1] < output.shape[2] else output[0]
    else:
        rows = output
    boxes: list[list[int]] = []
    scores: list[float] = []
    for values in rows:
        score = float(values[4])
        if score < 0.60:
            continue
        center_x = (float(values[0]) - pad_x) / scale
        center_y = (float(values[1]) - pad_y) / scale
        width = float(values[2]) / scale
        height = float(values[3]) / scale
        left = max(0, min(frame.shape[1] - 1, round(center_x - width / 2)))
        top = max(0, min(frame.shape[0] - 1, round(center_y - height / 2)))
        right = max(left + 1, min(frame.shape[1], round(center_x + width / 2)))
        bottom = max(top + 1, min(frame.shape[0], round(center_y + height / 2)))
        boxes.append([left, top, right - left, bottom - top])
        scores.append(score)
    if not boxes:
        return []
    kept = cv2.dnn.NMSBoxes(boxes, scores, 0.60, 0.50)
    return [(boxes[int(index)], scores[int(index)]) for index in kept]


def _read_plate(recognizer: cv2.dnn.Net, crop: np.ndarray) -> tuple[str, float]:
    zoomed = cv2.resize(crop, (800, max(1, round(crop.shape[0] * 800 / crop.shape[1]))), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(zoomed, cv2.COLOR_BGR2GRAY)
    band = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    ratio = band.shape[1] / max(1, band.shape[0])
    width = min(320, max(1, round(48 * ratio)))
    resized = cv2.resize(band, (width, 48)).astype(np.float32) / 127.5 - 1.0
    padded = np.zeros((48, 320, 3), dtype=np.float32)
    padded[:, :width] = resized
    recognizer.setInput(cv2.dnn.blobFromImage(padded, 1.0, (320, 48)))
    output = recognizer.forward()
    if output.ndim != 3 or output.shape[0] != 1:
        return "UNREADABLE", 0.0
    probabilities = output[0]
    decoded: list[str] = []
    confidence = 0.0
    count = 0
    previous = -1
    for row in probabilities:
        index = int(np.argmax(row))
        if index != previous and index != 0:
            character = _paddle_character(index)
            if character:
                decoded.append(character)
                confidence += float(row[index])
                count += 1
        previous = index
    plate = _clean_plate("".join(decoded))
    return (plate or "UNREADABLE", confidence / count if count else 0.0)


def recognize_frame(
    frame_bytes: bytes,
    detector_model: str,
    recognizer_model: str,
) -> RecognitionResult:
    frame = cv2.imdecode(np.frombuffer(frame_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        raise ValueError("The captured image is not a readable image.")
    detector = cv2.dnn.readNet(detector_model)
    recognizer = cv2.dnn.readNet(recognizer_model)
    detections = _detect_plates(detector, frame)
    annotated = frame.copy()
    if not detections:
        cv2.putText(annotated, "NO PLATE DETECTED", (24, 56), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 180), 3)
        return RecognitionResult("UNREADABLE", 0.0, 0.0, None, annotated)
    (left, top, width, height), detector_confidence = max(detections, key=lambda item: item[1])
    crop = frame[top:top + height, left:left + width].copy()
    plate, ocr_confidence = _read_plate(recognizer, crop)
    color = (0, 200, 255) if plate != "UNREADABLE" else (0, 0, 180)
    cv2.rectangle(annotated, (left, top), (left + width, top + height), color, 3)
    cv2.putText(annotated, plate, (left, max(32, top - 10)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
    return RecognitionResult(plate, detector_confidence, ocr_confidence, crop, annotated)


def encode_jpeg(image: np.ndarray, quality: int = 88) -> bytes:
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise ValueError("The recognition image could not be encoded as JPEG.")
    return encoded.tobytes()
