"""Find visible cash throughout a local video; save boxes, timestamps and a preview."""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from app.cv.train_cash import local_yolo

DEFAULT_MODEL = Path("data/cash/models/cash.pt")


def cash_class_ids(names) -> list[int]:
    pairs = names.items() if isinstance(names, dict) else enumerate(names)
    result = [int(index) for index, name in pairs if name == "cash"]
    if not result:
        raise ValueError(
            "Weights have no 'cash' class. Train cash weights; COCO cannot detect cash."
        )
    return result


class CashDetector:
    def __init__(self, weights: Path, confidence=0.25, imgsz=768, device="cpu"):
        if not weights.is_file():
            raise FileNotFoundError(f"Cash weights not found: {weights}")
        if not math.isfinite(confidence) or not 0 < confidence <= 1:
            raise ValueError("Confidence must be in (0, 1]")
        self.model_sha256 = hashlib.sha256(weights.read_bytes()).hexdigest()
        self.model = local_yolo()(str(weights))
        if hashlib.sha256(weights.read_bytes()).hexdigest() != self.model_sha256:
            raise ValueError("Weights changed while loading; use a completed model checkpoint")
        self.classes = cash_class_ids(self.model.names)
        self.confidence, self.imgsz, self.device = confidence, imgsz, device

    def detect(self, frame) -> list[dict]:
        result = self.model.predict(
            frame,
            classes=self.classes,
            conf=self.confidence,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )[0]
        return [
            {
                "label": "cash",
                "box_xyxy": [round(float(v), 2) for v in box],
                "confidence": round(float(score), 4),
            }
            for box, score in zip(
                result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist(), strict=True
            )
        ]


def draw_cash(frame, detections, timestamp: float):
    import cv2

    annotated = frame.copy()
    for detection in detections:
        x1, y1, x2, y2 = (round(v) for v in detection["box_xyxy"])
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 220, 0), 2)
        cv2.putText(
            annotated,
            f"cash {detection['confidence']:.2f}",
            (x1, max(40, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 220, 0),
            1,
            cv2.LINE_AA,
        )
    label = "cash detected" if detections else "cash not detected"
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 30), (30, 30, 30), -1)
    cv2.putText(
        annotated,
        f"{timestamp:.2f}s | {label}",
        (8, 21),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return annotated


def analyze_video(
    video: Path,
    weights: Path,
    output: Path,
    sample_fps=2.0,
    confidence=0.25,
    imgsz=768,
    device="cpu",
    max_seconds=None,
) -> dict:
    import cv2

    if not video.is_file():
        raise FileNotFoundError(video)
    if not math.isfinite(sample_fps) or sample_fps <= 0:
        raise ValueError("sample_fps must be finite and positive")
    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError("max_seconds must be finite and positive")
    for name in ("annotated.mp4", "detections.json", "preview.jpg"):
        if (output / name).resolve() in {video.resolve(), weights.resolve()}:
            raise ValueError("Output would overwrite an input")
    detector = CashDetector(weights, confidence, imgsz, device)
    capture = cv2.VideoCapture(str(video))
    writer = None
    started = time.perf_counter()
    try:
        if not capture.isOpened():
            raise ValueError(f"Cannot open video: {video}")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Video FPS unavailable")
        stride = max(1, round(fps / sample_fps))
        frames, index, best_score = [], 0, -1.0
        preview_time = None
        output.mkdir(parents=True, exist_ok=True)
        while max_seconds is None or index / fps < max_seconds:
            ok, frame = capture.read()
            if not ok:
                break
            if index % stride == 0:
                detections = detector.detect(frame)
                timestamp = round(index / fps, 4)
                annotated = draw_cash(frame, detections, timestamp)
                if writer is None:
                    height, width = frame.shape[:2]
                    writer = cv2.VideoWriter(
                        str(output / "annotated.mp4"),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        fps / stride,
                        (width, height),
                    )
                    if not writer.isOpened():
                        raise OSError("Cannot create annotated.mp4")
                writer.write(annotated)
                score = max((d["confidence"] for d in detections), default=0.0)
                if score > best_score:
                    if not cv2.imwrite(str(output / "preview.jpg"), annotated):
                        raise OSError("Cannot save preview")
                    best_score, preview_time = score, timestamp
                frames.append(
                    {
                        "frame_index": index,
                        "timecode_sec": timestamp,
                        "cash_status": "detected" if detections else "not_detected",
                        "detections": detections,
                    }
                )
                if len(frames) % 30 == 0:
                    print(f"Processed {len(frames)} samples, video {timestamp:.1f}s", flush=True)
            index += 1
        if not frames:
            raise ValueError("No video frames decoded")
        report = {
            "schema_version": 1,
            "video": video.name,
            "model": str(weights),
            "model_sha256": detector.model_sha256,
            "confidence_threshold": confidence,
            "imgsz": imgsz,
            "source_fps": fps,
            "effective_sample_fps": fps / stride,
            "processed_seconds": round(index / fps, 3),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "sampled_frames": len(frames),
            "frames_with_cash": sum(bool(f["detections"]) for f in frames),
            "preview_timecode_sec": preview_time,
            "interpretation": "Visible cash candidates only. Not detected does not mean absent. "
            "No amount, ownership, origin, tracking or violation verdict inferred.",
            "frames": frames,
        }
        (output / "detections.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return {k: v for k, v in report.items() if k != "frames"}
    finally:
        capture.release()
        if writer is not None:
            writer.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sample-fps", type=float, default=2)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=768)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-seconds", type=float)
    args = parser.parse_args()
    output = args.output_dir or Path("data/cash/results") / args.video.stem
    print(
        json.dumps(
            analyze_video(
                args.video,
                args.model,
                output,
                args.sample_fps,
                args.confidence,
                args.imgsz,
                args.device,
                args.max_seconds,
            ),
            indent=2,
        )
    )
    print(f"Results: {output.resolve()}")


if __name__ == "__main__":
    main()
