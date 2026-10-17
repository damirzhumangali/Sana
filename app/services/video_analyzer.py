from __future__ import annotations

import logging
from pathlib import Path

import cv2
from ultralytics import YOLO

from app.schemas import AnalysisResult, Violation
from app.services.detector import (
    build_violation,
    classify_persons,
    detect_id_card_on_desk,
)

logger = logging.getLogger(__name__)

_YOLO_MODEL: YOLO | None = None


def get_yolo_model() -> YOLO:
    global _YOLO_MODEL
    if _YOLO_MODEL is None:
        logger.info("Loading YOLOv8n model...")
        _YOLO_MODEL = YOLO("yolov8n.pt")
    return _YOLO_MODEL


class VideoAnalyzer:
    def __init__(self, sample_fps: float = 1.0) -> None:
        self.sample_fps = sample_fps
        self.model = get_yolo_model()

    def analyze(self, video_path: Path) -> AnalysisResult:
        if not video_path.is_file() or video_path.stat().st_size == 0:
            raise ValueError(f"Video file {video_path} does not exist or is empty")

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV could not open video: {video_path}")

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / video_fps if video_fps > 0 else 0.0

        logger.info(
            "Analyzing %s: %.1f sec, %d frames @ %.1f FPS (sampling at %.1f FPS)",
            video_path.name,
            duration,
            total_frames,
            video_fps,
            self.sample_fps,
        )

        step_frames = max(1, int(video_fps / self.sample_fps))
        frame_idx = 0

        cashier_timeline: list[tuple[float, bool]] = []
        client_counts: list[tuple[float, int]] = []
        entering_counts: list[tuple[float, int]] = []
        id_card_timeline: list[tuple[float, bool]] = []
        laptop_timeline: list[tuple[float, bool]] = []

        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            if frame_idx % step_frames == 0:
                current_sec = round(frame_idx / video_fps, 2)
                results = self.model(frame, verbose=False)[0]

                person_boxes: list[list[float]] = []
                person_confs: list[float] = []
                has_laptop = False

                for box in results.boxes:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    cls_name = self.model.names[cls_id]

                    if cls_name == "person" and conf > 0.45:
                        person_boxes.append(box.xyxy[0].tolist())
                        person_confs.append(conf)
                    elif cls_name == "laptop" and conf > 0.35:
                        has_laptop = True

                # Classify roles with glass reflection suppression
                classified = classify_persons(person_boxes, person_confs)
                has_cashier = len(classified["cashier"]) > 0
                n_clients = len(classified["client"])
                n_entering = len(classified["entering"])
                has_id_card = detect_id_card_on_desk(frame)

                cashier_timeline.append((current_sec, has_cashier))
                client_counts.append((current_sec, n_clients))
                entering_counts.append((current_sec, n_entering))
                id_card_timeline.append((current_sec, has_id_card))
                laptop_timeline.append((current_sec, has_laptop))

            frame_idx += 1

        cap.release()

        violations: list[Violation] = []

        # Rule 1: Multiple clients / unauthorized persons in booth (NB RK #231, p. 8, 10)
        multi_person_seconds = [
            sec for (sec, c_cnt), (_, e_cnt) in zip(client_counts, entering_counts, strict=False)
            if (c_cnt >= 2) or (c_cnt >= 1 and e_cnt >= 1)
        ]
        if len(multi_person_seconds) >= 2:
            first_ts = multi_person_seconds[0]
            violations.append(
                build_violation(
                    video_path=video_path,
                    violation_type="Постороннее лицо / нарушение очередности",
                    timecode_sec=first_ts,
                    description=(
                        "Обнаружено одновременное присутствие нескольких лиц в зоне обслуживания "
                        "кассового узла (Постановление Правления НБРК № 231, п. 8, п. 10)."
                    ),
                )
            )

        # Rule 2: Cashier left workspace (NB RK #231, p. 14, 35)
        ever_present = any(present for _, present in cashier_timeline)
        if ever_present:
            consecutive_absence = 0
            absence_start_sec = 0.0
            for sec, present in cashier_timeline:
                if not present:
                    if consecutive_absence == 0:
                        absence_start_sec = sec
                    consecutive_absence += 1
                    if consecutive_absence >= 4:
                        violations.append(
                            build_violation(
                                video_path=video_path,
                                violation_type="Оставление рабочего места",
                                timecode_sec=absence_start_sec,
                                description=(
                                    "Кассир покинул рабочее место при наличии оборудования "
                                    "и ценностей в кассе (Постановление Правления НБРК № 231, "
                                    "п. 14, п. 35)."
                                ),
                            )
                        )
                        break
                else:
                    consecutive_absence = 0

        # Rule 3: Client serviced without ID verification (Law on AML/CFT art. 5, NB RK #231, p. 27)
        # If client sat for > 8 seconds, but ID card was NEVER placed on the counter
        client_seconds = [sec for sec, c_cnt in client_counts if c_cnt >= 1]
        id_ever_presented = any(has_card for _, has_card in id_card_timeline)
        if len(client_seconds) >= 8 and not id_ever_presented:
            first_client_sec = client_seconds[0]
            violations.append(
                build_violation(
                    video_path=video_path,
                    violation_type="Прием клиента без проверки личности (ID)",
                    timecode_sec=first_client_sec,
                    description=(
                        "Кассир начал обслуживание клиента без предварительной проверки "
                        "документа, удостоверяющего личность (Закон РК «О ПОД/ФТ» ст. 5, "
                        "Постановление Правления НБРК № 231, п. 27)."
                    ),
                )
            )

        verdict = "violation" if violations else "no_violation"
        logger.info(
            "Analysis complete for %s: verdict=%s, %d violations",
            video_path.name,
            verdict,
            len(violations),
        )
        return AnalysisResult(verdict=verdict, violations=violations)


def analyze_video(video_path: Path) -> dict:
    """Entry point matching ANALYZER_CALLABLE=app.services.video_analyzer:analyze_video."""
    analyzer = VideoAnalyzer(sample_fps=1.0)
    result = analyzer.analyze(video_path)
    return result.model_dump()
