from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.schemas import EvidenceFrame, Violation

# Frame geometry constants for resolution 464 x 848
TABLE_Y_DIVIDER = 450
DOOR_X_MIN = 70
DOOR_X_MAX = 160
GLASS_REFLECTION_MAX_X = 68
MIN_PERSON_WIDTH = 60
MIN_PERSON_HEIGHT = 120


@dataclass
class PersonRole:
    role: str  # "cashier", "client", "entering"
    box: list[float]  # [x1, y1, x2, y2]
    center: tuple[float, float]
    confidence: float = 1.0


def is_reflection(box: list[float]) -> bool:
    """Detects if a bounding box is a mirror/glass reflection.

    The left glass partition is located at x < 68.
    Boxes inside this strip or with unusually small dimensions (< 60x120)
    represent reflections of cashier or corridor motion, not real persons.
    """
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    cx = (x1 + x2) / 2.0

    if cx < GLASS_REFLECTION_MAX_X:
        return True
    if w < MIN_PERSON_WIDTH or h < MIN_PERSON_HEIGHT:
        return True
    return False


def classify_persons(
    boxes: list[list[float]],
    confidences: list[float] | None = None,
) -> dict[str, list[PersonRole]]:
    """Classifies detected persons by spatial zones in the 464x848 top-down cashier view.

    - Cashier: sits in foreground chair behind the desk (cy > 450, cx > 120)
    - Client: sits across the desk facing camera (cy <= 450, cx >= 160)
    - Entering: physically entering through the doorway (70 <= cx < 160, cy <= 450)
    - Glass reflections (x < 68) are suppressed.
    """
    classified: dict[str, list[PersonRole]] = {
        "cashier": [],
        "client": [],
        "entering": [],
    }

    if confidences is None:
        confidences = [1.0] * len(boxes)

    for box, conf in zip(boxes, confidences, strict=False):
        x1, y1, x2, y2 = box
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0

        # Suppress glass reflections
        if is_reflection(box):
            continue

        if cy > TABLE_Y_DIVIDER and cx > 120:
            classified["cashier"].append(PersonRole("cashier", box, (cx, cy), conf))
        elif DOOR_X_MIN <= cx < DOOR_X_MAX and cy <= TABLE_Y_DIVIDER:
            classified["entering"].append(PersonRole("entering", box, (cx, cy), conf))
        else:
            classified["client"].append(PersonRole("client", box, (cx, cy), conf))

    return classified


def detect_id_card_on_desk(frame: np.ndarray) -> bool:
    """Detects presence of white ID card / document on the wooden counter in front of cashier.

    ROI: x: 130..170, y: 430..470. On the dark wooden desk, the white rectangular card
    stands out with high gray brightness (> 200).
    """
    h, w = frame.shape[:2]
    if h < 500 or w < 300:
        return False

    roi = frame[430:470, 130:170]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 195, 255, cv2.THRESH_BINARY)
    white_pixels = int(np.sum(thresh > 0))
    # Threshold corresponds to ~8x10px card size
    return white_pixels > 60


def extract_evidence_frame_base64(video_path: Path, timestamp_sec: float) -> str:
    """Extracts a frame at timestamp_sec from video_path and encodes to JPEG base64."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_no = max(0, int(timestamp_sec * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError(f"Failed to read frame at {timestamp_sec}s from {video_path}")

    success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not success:
        raise RuntimeError("Failed to encode frame to JPEG")

    return base64.b64encode(buffer).decode("ascii")


def create_evidence_frame(video_path: Path, timestamp_sec: float) -> EvidenceFrame:
    """Helper to create validated EvidenceFrame schema object."""
    b64 = extract_evidence_frame_base64(video_path, timestamp_sec)
    return EvidenceFrame(
        timestamp_sec=round(float(timestamp_sec), 2),
        image_base64=b64,
    )


def build_violation(
    video_path: Path,
    violation_type: str,
    timecode_sec: float,
    description: str,
) -> Violation:
    """Helper to construct a validated Violation with evidence frame."""
    frame = create_evidence_frame(video_path, timecode_sec)
    return Violation(
        type=violation_type,
        timecode_sec=round(float(timecode_sec), 2),
        description=description,
        evidence_frames=[frame],
    )
