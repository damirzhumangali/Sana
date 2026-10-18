"""Preview camera zones and detect cashier/customer presence in a local video."""

import argparse
import json
import os
from pathlib import Path

from app.services.person_roles import is_reflection_candidate
from app.services.role_tracking import GeometricRoleTracker


def load_zones(path: Path) -> dict[str, list[tuple[float, float]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    zones = raw["zones"]
    if set(zones) != {"cashier", "customer"}:
        raise ValueError("Zones must contain exactly cashier and customer")
    result = {}
    for role, polygon in zones.items():
        if len(polygon) < 3 or any(
            len(point) != 2
            or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in point)
            for point in polygon
        ):
            raise ValueError(f"Invalid normalized polygon for {role}")
        result[role] = [tuple(point) for point in polygon]
    return result


def draw_zones(frame, zones, cv2) -> None:
    height, width = frame.shape[:2]
    colors = {"cashier": (0, 200, 0), "customer": (255, 120, 0)}
    for role, polygon in zones.items():
        points = [((int(x * width), int(y * height))) for x, y in polygon]
        for first, second in zip(points, points[1:] + points[:1], strict=True):
            cv2.line(frame, first, second, colors.get(role, (150, 150, 150)), 2)
        cv2.putText(
            frame,
            role,
            points[0],
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            colors.get(role, (150, 150, 150)),
            2,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Local .mov video")
    parser.add_argument(
        "--zones",
        type=Path,
        default=Path("config/fortebank_camera.json"),
        help="Normalized camera zones JSON",
    )
    parser.add_argument("--preview-only", action="store_true", help="Draw zones without YOLO")
    parser.add_argument("--preview-seconds", type=float, default=5)
    parser.add_argument("--preview-path", type=Path, default=Path("data/roles-preview.jpg"))
    parser.add_argument("--model", type=Path, help="Local Ultralytics YOLO .pt weights")
    parser.add_argument("--sample-fps", type=float, default=2)
    parser.add_argument(
        "--cashier-anchor",
        type=float,
        nargs=2,
        default=(0.48, 0.57),
        metavar=("X", "Y"),
        help="Normalized cashier seat reference point",
    )
    parser.add_argument("--output", type=Path, default=Path("data/person-roles.json"))
    args = parser.parse_args()

    if args.sample_fps <= 0 or args.preview_seconds < 0:
        parser.error("--sample-fps must be positive and --preview-seconds nonnegative")
    if not all(0 <= value <= 1 for value in args.cashier_anchor):
        parser.error("--cashier-anchor coordinates must be in [0, 1]")
    if not args.video.is_file():
        parser.error(f"Video does not exist: {args.video}")

    try:
        import cv2
    except ImportError as exc:
        parser.error("OpenCV is missing. Install requirements-cv.txt first")
        raise AssertionError from exc

    zones = load_zones(args.zones)
    regions = json.loads(args.zones.read_text(encoding="utf-8")).get("reflection_regions", [])
    for polygon in regions:
        if len(polygon) < 3 or any(
            len(point) != 2
            or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in point)
            for point in polygon
        ):
            parser.error("Invalid reflection region: expected normalized polygon coordinates")
    overlay_zones = {**zones, **{f"glass_{i}": p for i, p in enumerate(regions)}}
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        parser.error(f"OpenCV cannot open video: {args.video}")

    try:
        if args.preview_only:
            capture.set(cv2.CAP_PROP_POS_MSEC, args.preview_seconds * 1000)
            ok, frame = capture.read()
            if not ok:
                parser.error("Cannot read the requested preview frame")
            draw_zones(frame, overlay_zones, cv2)
            args.preview_path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(args.preview_path), frame):
                parser.error(f"Cannot write preview: {args.preview_path}")
            print(args.preview_path.resolve())
            return 0

        if args.model is None or not args.model.is_file():
            parser.error("Pass --model with existing local YOLO weights, for example yolo11n.pt")
        config_dir = Path("data/ultralytics").resolve()
        config_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(config_dir))
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            parser.error("Ultralytics is missing. Install requirements-cv.txt first")
            raise AssertionError from exc

        model = YOLO(str(args.model))
        role_tracker = GeometricRoleTracker()
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            parser.error("Video frame rate is unavailable")
        stride = max(1, round(fps / args.sample_fps))
        frames = []
        frame_index = 0
        preview_saved = False
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % stride == 0:
                height, width = frame.shape[:2]
                result = model.predict(frame, classes=[0], verbose=False)[0]
                people = []
                boxes = result.boxes.xyxy.cpu().tolist()
                scores = result.boxes.conf.cpu().tolist()
                for detected_box, score in zip(boxes, scores, strict=True):
                    box = tuple(float(value) for value in detected_box)
                    reflection = is_reflection_candidate(box, width, height, regions)
                    people.append(
                        {
                            "role": "unknown",
                            "box_xyxy": [round(value, 1) for value in box],
                            "confidence": round(score, 3),
                            "visibility": "reflection_candidate" if reflection else "person",
                            "counted": not reflection,
                        }
                    )
                role_tracker.update(
                    people, frame_index / fps, width, height, zones, tuple(args.cashier_anchor)
                )
                if not preview_saved and frame_index / fps >= args.preview_seconds:
                    draw_zones(frame, overlay_zones, cv2)
                    for person in people:
                        x1, y1, x2, y2 = (round(v) for v in person["box_xyxy"])
                        color = (0, 255, 255) if person["counted"] else (150, 150, 150)
                        label = person["role"] if person["counted"] else "reflection?"
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(
                            frame,
                            f"{label} {person['confidence']:.2f}",
                            (x1, max(20, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.45,
                            color,
                            2,
                        )
                    args.preview_path.parent.mkdir(parents=True, exist_ok=True)
                    if not cv2.imwrite(str(args.preview_path), frame):
                        parser.error(f"Cannot write preview: {args.preview_path}")
                    preview_saved = True
                frames.append(
                    {
                        "timecode_sec": round(frame_index / fps, 3),
                        "cashier_present": any(
                            p["counted"] and p["role"] == "cashier" for p in people
                        ),
                        "customer_count": sum(
                            p["counted"] and p["role"] == "customer" for p in people
                        ),
                        "person_count": sum(p["counted"] for p in people),
                        "unknown_role_count": sum(
                            p["counted"] and p["role"] == "unknown" for p in people
                        ),
                        "reflection_candidate_count": sum(not p["counted"] for p in people),
                        "people": people,
                    }
                )
            frame_index += 1
        if not frames:
            parser.error("No frames could be decoded")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"video": args.video.name, "frames": frames}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Analyzed {len(frames)} frames; saved {args.output.resolve()}")
        return 0
    finally:
        capture.release()


if __name__ == "__main__":
    raise SystemExit(main())
