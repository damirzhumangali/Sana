"""Evaluate provisional cash boxes at a fixed IoU and confidence threshold."""

import argparse
import json
from pathlib import Path

from app.cv.cash_dataset import reviewed_items
from app.cv.inspect_cash import DEFAULT_MODEL, CashDetector, draw_cash


def iou(first, second):
    x1, y1 = max(first[0], second[0]), max(first[1], second[1])
    x2, y2 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = (
        (first[2] - first[0]) * (first[3] - first[1])
        + (second[2] - second[0]) * (second[3] - second[1])
        - intersection
    )
    return intersection / union if union else 0.0


def match_boxes(detections, targets, minimum_iou=0.5):
    unmatched = set(range(len(targets)))
    tp = 0
    for detection in sorted(detections, key=lambda d: d["confidence"], reverse=True):
        best = max(unmatched, key=lambda i: iou(detection["box_xyxy"], targets[i]), default=None)
        if best is not None and iou(detection["box_xyxy"], targets[best]) >= minimum_iou:
            unmatched.remove(best)
            tp += 1
    return tp, len(detections) - tp, len(unmatched)


def main() -> None:
    import cv2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=Path("data/cash/annotations.json"))
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--split", choices=["val", "test"], default="test")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=768)
    parser.add_argument("--output-dir", type=Path, default=Path("data/cash/evaluation"))
    args = parser.parse_args()
    data = json.loads(args.annotations.read_text(encoding="utf-8"))
    items = [item for item in reviewed_items(data) if item["split"] == args.split]
    if not items:
        parser.error("No reviewed images in split")
    detector = CashDetector(args.model, args.confidence, args.imgsz)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    totals = [0, 0, 0]
    per_frame = []
    for item in items:
        frame = cv2.imread(item["image"])
        if frame is None:
            raise ValueError(f"Cannot read {item['image']}")
        detections = detector.detect(frame)
        counts = match_boxes(detections, item["boxes"])
        totals = [a + b for a, b in zip(totals, counts, strict=True)]
        preview = draw_cash(frame, detections, item["timecode_sec"])
        for box in item["boxes"]:
            x1, y1, x2, y2 = (round(v) for v in box)
            cv2.rectangle(preview, (x1, y1), (x2, y2), (255, 0, 255), 1)
        if not cv2.imwrite(str(args.output_dir / f"{item['id']}.jpg"), preview):
            raise OSError("Cannot save evaluation preview")
        per_frame.append({"id": item["id"], "tp_fp_fn": counts, "detections": detections})
    tp, fp, fn = totals
    report = {
        "split": args.split,
        "model": str(args.model),
        "model_sha256": detector.model_sha256,
        "imgsz": args.imgsz,
        "images": len(items),
        "negative_images": sum(not item["boxes"] for item in items),
        "source_videos": len({item["video_sha256"] for item in items}),
        "confidence": args.confidence,
        "iou": 0.5,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "annotation_provenance": data.get("annotation_provenance"),
        "preview_legend": "green=prediction, magenta=provisional annotation",
        "frames": per_frame,
    }
    (args.output_dir / f"{args.split}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "frames"}, indent=2))


if __name__ == "__main__":
    main()
