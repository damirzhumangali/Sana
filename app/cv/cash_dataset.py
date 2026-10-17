"""Export reviewed visible-cash boxes to YOLO; keep source videos in one split."""

import argparse
import hashlib
import json
import math
import shutil
from collections import Counter
from pathlib import Path


def reviewed_items(data: dict) -> list[dict]:
    if data.get("class") != "cash":
        raise ValueError("Expected a single cash class")
    selected, ids, sources = [], set(), {}
    for item in data["items"]:
        key, split = item["id"], item["split"]
        if key in ids or not key.replace("_", "").isalnum():
            raise ValueError(f"Duplicate or invalid image id: {key}")
        ids.add(key)
        if split not in {"train", "val", "test"}:
            raise ValueError(f"Invalid split: {split}")
        digest = item["video_sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid source video hash")
        if sources.setdefault(digest, split) != split:
            raise ValueError("Source video leakage across splits")
        if item.get("review") not in {"reviewed", "reviewed_provisional"}:
            continue  # Never convert an unreviewed frame to a negative example.
        boxes = item.get("boxes")
        if not isinstance(boxes, list):
            raise ValueError(f"Reviewed frame has no explicit boxes: {key}")
        width, height = item["width"], item["height"]
        if width <= 0 or height <= 0:
            raise ValueError("Invalid image dimensions")
        for box in boxes:
            if len(box) != 4 or not all(math.isfinite(v) for v in box):
                raise ValueError(f"Invalid box: {key}")
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ValueError(f"Box outside image: {key}")
        selected.append(item)
    return selected


def export_dataset(annotations: Path, output: Path) -> dict:
    from PIL import Image

    data = json.loads(annotations.read_text(encoding="utf-8"))
    items = reviewed_items(data)
    # Verify source hashes once, so edited/replaced videos cannot invalidate the split audit.
    verified = {}
    image_hashes = {}
    for item in items:
        source = Path(item["video"])
        if source not in verified:
            with source.open("rb") as stream:
                verified[source] = hashlib.file_digest(stream, "sha256").hexdigest()
        if verified[source] != item["video_sha256"]:
            raise ValueError(f"Source video changed: {source}")
        image = Path(item["image"])
        with Image.open(image) as frame:
            if frame.size != (item["width"], item["height"]):
                raise ValueError(f"Image size changed: {image}")
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if image_hashes.setdefault(digest, item["split"]) != item["split"]:
            raise ValueError("Duplicate image across splits")
    for split in ("train", "val", "test"):
        if not any(x["split"] == split for x in items):
            raise ValueError(f"No reviewed images for {split}")
    # A fresh directory avoids stale labels from a previous export.
    output.mkdir(parents=True, exist_ok=False)
    for item in items:
        image_dir = output / "images" / item["split"]
        label_dir = output / "labels" / item["split"]
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item["image"], image_dir / f"{item['id']}.jpg")
        w, h = item["width"], item["height"]
        lines = [
            f"0 {(x1 + x2) / (2 * w):.8f} {(y1 + y2) / (2 * h):.8f} "
            f"{(x2 - x1) / w:.8f} {(y2 - y1) / h:.8f}"
            for x1, y1, x2, y2 in item["boxes"]
        ]
        (label_dir / f"{item['id']}.txt").write_text("\n".join(lines), encoding="utf-8")
    config = {
        "path": output.resolve().as_posix(),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {0: "cash"},
    }
    # JSON syntax is also valid YAML and safely quotes Windows paths.
    (output / "cash.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8")
    report = {
        "annotation_provenance": data.get("annotation_provenance", "unspecified"),
        "images": dict(Counter(x["split"] for x in items)),
        "boxes": {
            s: sum(len(x["boxes"]) for x in items if x["split"] == s)
            for s in ("train", "val", "test")
        },
        "excluded": len(data["items"]) - len(items),
        "items": items,
    }
    (output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {k: v for k, v in report.items() if k != "items"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/cash/dataset"))
    args = parser.parse_args()
    print(json.dumps(export_dataset(args.annotations, args.output), indent=2))


if __name__ == "__main__":
    main()
