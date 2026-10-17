"""Run local video -> detections -> histories -> events -> the existing Rule Engine."""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from app.cv.inspect_cash import DEFAULT_MODEL, CashDetector
from app.cv.inspect_roles import draw_zones
from app.cv.train_cash import local_yolo
from app.events.config import EventConfig
from app.events.detector import EventDetector
from app.services.person_roles import is_reflection_candidate
from app.services.role_tracking import GeometricRoleTracker


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def suppress_nested_people(people, overlap_threshold):
    """Do not turn nearly contained duplicate boxes into new person identities.

    Raw detections remain in the cache. A suppression also invalidates people
    coverage: two real people can overlap, so this is not proof of their count.
    """
    kept, suppressed = [], []
    for person in sorted(people, key=lambda p: p["confidence"], reverse=True):
        a = person["box_xyxy"]
        duplicate = False
        for other in kept:
            b = other["box_xyxy"]
            intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                0, min(a[3], b[3]) - max(a[1], b[1])
            )
            minimum_area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
            if minimum_area > 0 and intersection / minimum_area >= overlap_threshold:
                duplicate = True
                break
        (suppressed if duplicate else kept).append(person)
    return kept, suppressed


def draw_debug(frame, state, config):
    import cv2

    canvas = frame.copy()
    height, width = canvas.shape[:2]
    draw_zones(canvas, config.zones, cv2)
    for person in state["people"]:
        x1, y1, x2, y2 = [
            round(v * (width if i % 2 == 0 else height)) for i, v in enumerate(person["bbox"])
        ]
        color = (80, 220, 80) if person["role"] == "cashier" else (255, 160, 60)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            canvas,
            f"{person['track_id']} {person['role']}",
            (x1, max(15, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
            cv2.LINE_AA,
        )
    for obj in state["objects"]:
        x1, y1, x2, y2 = [
            round(v * (width if i % 2 == 0 else height)) for i, v in enumerate(obj["bbox"])
        ]
        color = (0, 255, 255) if obj["label"] == "cash" else (255, 0, 255)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            canvas,
            f"{obj['track_id']} / {obj['logical_object_id']}",
            (max(0, min(x1, width - 180)), max(15, y1 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            color,
            1,
            cv2.LINE_AA,
        )
    cv2.rectangle(canvas, (0, 0), (width, 136), (25, 25, 25), -1)
    lines = [
        f"{state['timestamp_sec']:.2f}s SERVICE: {state['active_session'] or 'INACTIVE'}",
        f"CUSTOMER: {state['current_customer'] or '-'} "
        f"{'visible' if state['current_customer_visible'] else 'not tracked'} | ID: unavailable",
    ]
    lines += [
        f"{e['timestamp_sec']:.2f} {e['type']}" + (" ?" if not e.get("accepted", True) else "")
        for e in state["recent_events"]
    ]
    for row, text in enumerate(lines):
        cv2.putText(
            canvas,
            text,
            (6, 19 + 21 * row),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.37,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
    return canvas


def analyze_video(
    video: Path,
    output: Path,
    config_path: Path,
    person_weights=Path("yolo11n.pt"),
    cash_weights=DEFAULT_MODEL,
    replay=None,
):
    import cv2

    cfg = EventConfig.model_validate_json(config_path.read_text(encoding="utf-8-sig"))
    if cfg.id_detector_validated:
        raise ValueError(
            "No validated ID model is installed in the video adapter; keep ID disabled"
        )
    if not video.is_file():
        raise FileNotFoundError(video)
    outputs = {
        "debug.mp4",
        "detections.json",
        "track_history.json",
        "events.json",
        "rule_request.json",
        "rule_result.json",
        "timeline.txt",
        "preview.jpg",
    }
    if any(
        (output / name).resolve()
        in {p.resolve() for p in (video, config_path, person_weights, cash_weights)}
        for name in outputs
    ):
        raise ValueError("An output path would overwrite an input")
    started = time.perf_counter()
    digest = sha256(video)
    cached = json.loads(replay.read_text(encoding="utf-8")) if replay else None
    if replay and (not cached or not cached.get("frames")):
        raise ValueError("Replay cache must contain detections and video metadata")
    if cached and cached["video_sha256"] != digest:
        raise ValueError("Replay detections belong to a different video")
    raw_by_index = {row["frame_index"]: row for row in cached["frames"]} if cached else {}
    if not cached:
        if not person_weights.is_file():
            raise FileNotFoundError(person_weights)
        person_model = local_yolo()(str(person_weights))
        if person_model.names.get(0) != "person":
            raise ValueError("Person weights must have person at class 0")
        cash_model = CashDetector(cash_weights, cfg.minimum_detection_confidence)
    detector = EventDetector(cfg)
    roles = GeometricRoleTracker(max_gap_sec=cfg.person_track_gap_sec)
    cap = cv2.VideoCapture(str(video))
    writer = None
    raw_frames, states = [], []
    preview_score = -1
    index = 0
    output.mkdir(parents=True, exist_ok=True)
    try:
        if not cap.isOpened():
            raise ValueError("Cannot open video")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Video FPS unavailable")
        stride = cached["stride"] if cached else max(1, round(fps / cfg.sample_fps))
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if (index in raw_by_index) if cached else (index % stride == 0):
                t = index / fps
                height, width = frame.shape[:2]
                if cached:
                    raw = raw_by_index[index]
                    if abs(raw["timestamp_sec"] - t) > 0.001:
                        raise ValueError("Replay frame timing mismatch")
                else:
                    prediction = person_model.predict(
                        frame,
                        classes=[0],
                        conf=cfg.minimum_detection_confidence,
                        device="cpu",
                        verbose=False,
                    )[0]
                    raw_people = [
                        {"box_xyxy": box, "confidence": score}
                        for box, score in zip(
                            prediction.boxes.xyxy.cpu().tolist(),
                            prediction.boxes.conf.cpu().tolist(),
                            strict=True,
                        )
                    ]
                    raw_objects = [
                        {
                            "bbox": [
                                v / (width if i % 2 == 0 else height)
                                for i, v in enumerate(d["box_xyxy"])
                            ],
                            "confidence": d["confidence"],
                            "label": "cash",
                        }
                        for d in cash_model.detect(frame)
                    ]
                    raw = {
                        "frame_index": index,
                        "timestamp_sec": t,
                        "width": width,
                        "height": height,
                        "people": raw_people,
                        "objects": raw_objects,
                    }
                raw_frames.append(raw)
                candidates = [
                    {
                        **p,
                        "counted": not is_reflection_candidate(
                            p["box_xyxy"], width, height, cfg.reflection_regions
                        ),
                    }
                    for p in raw["people"]
                ]
                people, suppressed = suppress_nested_people(
                    [p for p in candidates if p["counted"]], cfg.person_duplicate_overlap
                )
                if suppressed:
                    detector.warnings.add("overlapping_person_detections_suppressed")
                roles.update(people, t, width, height, cfg.zones, cfg.cashier_anchor)
                normalized = [
                    {
                        "track_id": p["track_id"],
                        "role": p["role"],
                        "confidence": p["confidence"],
                        "identity_reliable": not p.get("tracking_ambiguous", False),
                        "bbox": [
                            v / (width if i % 2 == 0 else height)
                            for i, v in enumerate(p["box_xyxy"])
                        ],
                    }
                    for p in people
                    if p["counted"]
                ]
                visible = {p["track_id"] for p in normalized if p["identity_reliable"]}
                missing_interior = any(
                    s.inside and key not in visible and not detector.in_zone(s.last_point, "exit")
                    for key, s in detector.people.items()
                )
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                clean_people = all(
                    p["identity_reliable"]
                    and p["role"] != "unknown"
                    and p["confidence"] >= cfg.people_coverage_confidence
                    for p in normalized
                )
                observable = bool(
                    gray.std() > cfg.image_min_std
                    and clean_people
                    and not missing_interior
                    and not suppressed
                )
                state = detector.update(
                    {
                        "timestamp_sec": t,
                        "people": normalized,
                        "objects": raw["objects"],
                        "people_observable": observable,
                        "money_observable": False,
                        "id_observable": False,
                    }
                )
                state["suppressed_person_detections"] = suppressed
                state["people_observable"] = observable
                states.append(state)
                annotated = draw_debug(frame, state, cfg)
                if writer is None:
                    writer = cv2.VideoWriter(
                        str(output / "debug.mp4"),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        fps / stride,
                        (width, height),
                    )
                    if not writer.isOpened():
                        raise OSError("Cannot create debug video")
                writer.write(annotated)
                score = len(detector.events) + (1 if state["objects"] else 0)
                if score > preview_score:
                    if not cv2.imwrite(str(output / "preview.jpg"), annotated):
                        raise OSError("Cannot write preview")
                    preview_score = score
                if len(states) % 30 == 0:
                    print(f"{t:.1f}s: {len(detector.events)} event candidates", flush=True)
            index += 1
        if not states:
            raise ValueError("No frames analyzed")
        final = detector.finish(index / fps)
        final["video"] = video.name
        final["video_sha256"] = digest
        final["config"] = cfg.model_dump()
        final["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        final["processing_mode"] = "replay_cached_detections" if cached else "video_inference"
        final["sampled_frames"] = len(states)
        history = final.pop("track_history")
        metadata = {
            "video_sha256": digest,
            "stride": stride,
            "fps": fps,
            "person_weights_sha256": cached["person_weights_sha256"]
            if cached
            else sha256(person_weights),
            "cash_weights_sha256": cached["cash_weights_sha256"]
            if cached
            else cash_model.model_sha256,
            "frames": raw_frames,
        }
        for name, value in {
            "detections": metadata,
            "track_history": {"observations": history, "frames": states},
            "events": final,
            "rule_request": final["rule_request"],
            "rule_result": final["rule_result"],
        }.items():
            (output / f"{name}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        lines = [
            f"{int(e['timestamp_sec'] // 60):02}:{e['timestamp_sec'] % 60:05.2f} {e['type']} "
            f"confidence={e['confidence']:.3f} "
            f"{'accepted' if e.get('accepted') else 'uncertain'}"
            for e in final["events"]
        ]
        (output / "timeline.txt").write_text(
            "\n".join(lines) or "No confirmed temporal events", encoding="utf-8"
        )
        return {
            "verdict": final["rule_result"]["verdict"],
            "sampled_frames": len(states),
            "event_candidates": len(detector.events),
            "warnings": final["warnings"],
            "elapsed_seconds": final["elapsed_seconds"],
            "processing_mode": final["processing_mode"],
            "output": str(output.resolve()),
        }
    finally:
        cap.release()
        if writer:
            writer.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/event_camera.json"))
    parser.add_argument("--person-model", type=Path, default=Path("yolo11n.pt"))
    parser.add_argument("--cash-model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--replay", type=Path, help="Replay cached detections for this exact video")
    args = parser.parse_args()
    result = analyze_video(
        args.video,
        args.output_dir or Path("data/events") / args.video.stem,
        args.config,
        args.person_model,
        args.cash_model,
        args.replay,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
