from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.schemas import EvidenceFrame, Violation

# Geometry is calibrated to a frame with (width, height) = (464, 848).
REFERENCE_FRAME_SIZE = (464, 848)
TABLE_Y_DIVIDER = 450
DOOR_X_MIN = 70
DOOR_X_MAX = 160
DOOR_Y_MIN = 150
DOOR_Y_MAX = 420
GLASS_REFLECTION_MAX_X = 70
MIN_PERSON_WIDTH = 65
MIN_PERSON_HEIGHT = 120


@dataclass
class PersonRole:
    role: str  # "cashier", "client", "entering"
    box: list[float]  # [x1, y1, x2, y2]
    center: tuple[float, float]
    confidence: float = 1.0


def _frame_scale(frame_size: tuple[int, int]) -> tuple[float, float]:
    """Return scale factors from the source frame to the calibrated view."""
    try:
        size = np.asarray(frame_size, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("frame_size must contain a positive width and height") from exc
    if size.shape != (2,) or not np.isfinite(size).all() or np.any(size <= 0):
        raise ValueError("frame_size must contain a positive width and height")
    return REFERENCE_FRAME_SIZE[0] / size[0], REFERENCE_FRAME_SIZE[1] / size[1]


def _reference_box(box: list[float], scale: tuple[float, float]) -> list[float]:
    try:
        coordinates = np.asarray(box, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Each person box must contain four finite coordinates") from exc
    if coordinates.shape != (4,) or not np.isfinite(coordinates).all():
        raise ValueError("Each person box must contain four finite coordinates")
    x1, y1, x2, y2 = coordinates
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Person boxes must have positive width and height")
    sx, sy = scale
    return [x1 * sx, y1 * sy, x2 * sx, y2 * sy]


def _is_reflection_reference(box: list[float]) -> bool:
    x1, y1, x2, y2 = box
    return bool(
        x2 < GLASS_REFLECTION_MAX_X or x2 - x1 < MIN_PERSON_WIDTH or y2 - y1 < MIN_PERSON_HEIGHT
    )


def is_reflection(box: list[float], frame_size: tuple[int, int] = REFERENCE_FRAME_SIZE) -> bool:
    """Reject glass-only boxes and undersized detections in calibrated coordinates.

    The dead zone uses the right edge (x2 < 70), so a real doorway person
    overlapping the glass is retained. Width < 65 OR height < 120 is rejected.
    This spatial filter does not establish motion correlation or track identity.
    """
    return _is_reflection_reference(_reference_box(box, _frame_scale(frame_size)))


def classify_persons(
    boxes: list[list[float]],
    confidences: list[float] | None = None,
    frame_size: tuple[int, int] = REFERENCE_FRAME_SIZE,
) -> dict[str, list[PersonRole]]:
    """Classify people using the calibrated cashier view; ignore unrelated zones.

    - Cashier: sits in foreground chair behind the desk (cy > 450, cx > 120)
    - Entering: doorway center x = 70..160, y = 150..420 (takes precedence)
    - Client: service area center cx > 100, y = 150..450
    - Glass-only, undersized, off-frame and unrelated-zone detections are ignored.

    frame_size is (width, height). Geometry is scaled to 464x848, while returned
    boxes and centers remain in original frame pixels. Invalid boxes, dimensions,
    or confidence values raise ValueError instead of silently dropping detections.
    """
    scale = _frame_scale(frame_size)
    classified: dict[str, list[PersonRole]] = {
        "cashier": [],
        "client": [],
        "entering": [],
    }

    if confidences is None:
        confidences = [1.0] * len(boxes)
    if len(confidences) != len(boxes):
        raise ValueError("A confidence value is required for each person box")

    for box, conf in zip(boxes, confidences, strict=True):
        reference_box = _reference_box(box, scale)
        try:
            confidence = float(conf)
        except (TypeError, ValueError) as exc:
            raise ValueError("Person confidence must be finite and between 0 and 1") from exc
        if not np.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Person confidence must be finite and between 0 and 1")

        x1, y1, x2, y2 = reference_box
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0

        if _is_reflection_reference(reference_box):
            continue
        if not (0 <= cx <= REFERENCE_FRAME_SIZE[0] and 0 <= cy <= REFERENCE_FRAME_SIZE[1]):
            continue

        original_center = (cx / scale[0], cy / scale[1])

        if cy > TABLE_Y_DIVIDER and cx > 120:
            role = "cashier"
        elif DOOR_X_MIN <= cx <= DOOR_X_MAX and DOOR_Y_MIN <= cy <= DOOR_Y_MAX:
            role = "entering"
        elif cx > 100 and DOOR_Y_MIN <= cy <= TABLE_Y_DIVIDER:
            role = "client"
        else:
            continue
        classified[role].append(PersonRole(role, box, original_center, confidence))

    return classified


def detect_id_card_on_desk(frame: np.ndarray) -> bool:
    """Return whether a bright card-shaped candidate is visible in the desk ROI.

    The calibrated ROI is x = 120..250, y = 400..480 and scales with the frame.
    A candidate is only a visual hint: it cannot prove document handover, identity
    verification, or that the white object is an ID card.
    """
    h, w = frame.shape[:2]
    if h == 0 or w == 0:
        return False

    sx, sy = w / REFERENCE_FRAME_SIZE[0], h / REFERENCE_FRAME_SIZE[1]
    roi = frame[round(400 * sy) : round(480 * sy), round(120 * sx) : round(250 * sx)]
    if roi.size == 0:
        return False
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 195, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        # Compare contours in reference pixels even for anisotropically resized frames.
        reference_contour = contour.astype(np.float32)
        reference_contour[:, :, 0] /= sx
        reference_contour[:, :, 1] /= sy
        area = cv2.contourArea(reference_contour)
        if not 60 <= area <= 130 * 80 * 0.4:
            continue
        _, (rect_w, rect_h), _ = cv2.minAreaRect(reference_contour)
        if min(rect_w, rect_h) <= 0:
            continue
        aspect_ratio = max(rect_w, rect_h) / min(rect_w, rect_h)
        if 1.2 <= aspect_ratio <= 2.2 and area / (rect_w * rect_h) >= 0.7:
            return True
    return False


def _validated_timestamp(timestamp_sec: float) -> float:
    try:
        timestamp = float(timestamp_sec)
    except (TypeError, ValueError) as exc:
        raise ValueError("Evidence timestamp must be finite and nonnegative") from exc
    if not np.isfinite(timestamp) or timestamp < 0:
        raise ValueError("Evidence timestamp must be finite and nonnegative")
    return timestamp


def _encode_evidence_frame_base64(frame: np.ndarray) -> str:
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        raise ValueError("Evidence frame must be a nonempty image")
    success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not success:
        raise RuntimeError("Failed to encode frame to JPEG")
    return base64.b64encode(buffer).decode("ascii")


def extract_evidence_frame_base64(video_path: Path, timestamp_sec: float) -> str:
    """Extract a frame at a nonnegative timestamp and encode to JPEG base64."""
    timestamp = _validated_timestamp(timestamp_sec)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("Video FPS must be finite and positive to locate evidence accurately")
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(timestamp * fps))
        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError(f"Failed to read frame at {timestamp}s from {video_path}")
    finally:
        cap.release()

    return _encode_evidence_frame_base64(frame)


def create_evidence_frame(
    video_path: Path, timestamp_sec: float, *, frame: np.ndarray | None = None
) -> EvidenceFrame:
    """Create evidence from the analyzed frame, or seek the video when omitted.

    Supplying the frame avoids a second decoder and preserves the exact sampled
    image. The caller supplies its matching video timestamp.
    """
    timestamp = _validated_timestamp(timestamp_sec)
    b64 = (
        _encode_evidence_frame_base64(frame)
        if frame is not None
        else extract_evidence_frame_base64(video_path, timestamp)
    )
    return EvidenceFrame(
        timestamp_sec=round(timestamp, 2),
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
