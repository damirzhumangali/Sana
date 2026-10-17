"""Local video inference and packaging of evidence for the temporal rule engine."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import fields
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.schemas import AnalysisResult, Violation
from app.services.analyzer import load_analyzer
from app.services.cashier_rules import FrameObservation, RuleEngine
from app.services.detector import classify_persons, create_evidence_frame

logger = logging.getLogger(__name__)

# Providers run locally. Missing/None values mean unknown, not a negative observation.
SignalProvider = Callable[[np.ndarray, float], Mapping[str, bool | None]]
SIGNAL_FIELDS = frozenset(field.name for field in fields(FrameObservation)) - {
    "timestamp_sec",
    "cashier_present",
    "client_count",
    "entering_count",
}


class VideoAnalysisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    video_model_path: Path = Path("yolov8n.pt")
    video_sample_fps: float = Field(default=2.0, ge=0.5, allow_inf_nan=False)
    video_signal_provider: str | None = None


@lru_cache(maxsize=2)
def _load_local_model(weights: str) -> Any:
    # Import only after checking the file. No implicit weight download is allowed.
    from ultralytics import YOLO

    logger.info("Loading local detection weights: %s", weights)
    return YOLO(weights, task="detect")


def get_yolo_model(model_path: Path | str = Path("yolov8n.pt")) -> Any:
    weights = Path(model_path).expanduser().resolve()
    if weights.suffix.lower() != ".pt":
        raise ValueError("VIDEO_MODEL_PATH must point to trained local .pt weights")
    if not weights.is_file() or weights.stat().st_size == 0:
        raise FileNotFoundError(f"Prepare nonempty local YOLO weights before analysis: {weights}")
    return _load_local_model(str(weights))


class VideoAnalyzer:
    def __init__(
        self,
        sample_fps: float = 2.0,
        *,
        model: Any | None = None,
        model_path: Path | str = Path("yolov8n.pt"),
        signal_provider: SignalProvider | None = None,
    ) -> None:
        if not math.isfinite(sample_fps) or sample_fps < 0.5:
            raise ValueError("sample_fps must be finite and at least 0.5")
        self.sample_fps = sample_fps
        self.model = model if model is not None else get_yolo_model(model_path)
        self.signal_provider = signal_provider

    def observe(self, frame: np.ndarray, timestamp_sec: float) -> FrameObservation:
        """Build an observation without promoting object candidates to verified actions.

        COCO laptop detections cannot establish an open/unlocked screen. A white
        rectangle or ID-card detection cannot establish verification by a cashier.
        """
        results = self.model(frame, verbose=False)[0]
        person_boxes: list[list[float]] = []
        person_confs: list[float] = []
        for box in results.boxes:
            cls_id = int(box.cls[0])
            confidence = float(box.conf[0])
            if self.model.names[cls_id] == "person" and confidence > 0.45:
                person_boxes.append(box.xyxy[0].tolist())
                person_confs.append(confidence)

        height, width = frame.shape[:2]
        classified = classify_persons(person_boxes, person_confs, frame_size=(width, height))
        signals: Mapping[str, bool | None] = {}
        if self.signal_provider is not None:
            signals = self.signal_provider(frame, timestamp_sec)
            if not isinstance(signals, Mapping):
                raise ValueError("VIDEO_SIGNAL_PROVIDER must return a mapping of semantic signals")
            if set(signals) - SIGNAL_FIELDS:
                raise ValueError("VIDEO_SIGNAL_PROVIDER returned unsupported signal names")
            if any(value is not None and type(value) is not bool for value in signals.values()):
                raise ValueError("Semantic signal values must be bool or None")

        return FrameObservation(
            timestamp_sec=timestamp_sec,
            cashier_present=bool(classified["cashier"]),
            client_count=len(classified["client"]),
            entering_count=len(classified["entering"]),
            **signals,
        )

    def analyze(self, video_path: Path) -> AnalysisResult:
        video_path = Path(video_path)
        if not video_path.is_file() or video_path.stat().st_size == 0:
            raise ValueError(f"Video file {video_path} does not exist or is empty")

        cap = cv2.VideoCapture(str(video_path))
        violations: list[Violation] = []
        decoded_frames = 0
        rule_engine = RuleEngine()
        try:
            if not cap.isOpened():
                raise RuntimeError(f"OpenCV could not open video: {video_path}")
            video_fps = cap.get(cv2.CAP_PROP_FPS)
            if not math.isfinite(video_fps) or video_fps <= 0:
                raise ValueError("Video must report a finite, positive frame rate")
            reported_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            total_frames = (
                int(reported_frames)
                if math.isfinite(reported_frames) and reported_frames > 0
                else 0
            )
            logger.info("Analyzing %s at %.2f samples/sec", video_path.name, self.sample_fps)
            next_sample_sec = 0.0
            previous_timestamp = -1.0
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                # Prefer the decoder clock for variable-frame-rate containers.
                # Some backends only return zero, so retain a CFR fallback.
                decoder_timestamp = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                timestamp_sec = decoded_frames / video_fps
                if (
                    math.isfinite(decoder_timestamp)
                    and decoder_timestamp >= 0
                    and decoder_timestamp > previous_timestamp
                ):
                    timestamp_sec = decoder_timestamp
                if timestamp_sec <= previous_timestamp:
                    raise RuntimeError("Video timestamps stopped increasing")
                previous_timestamp = timestamp_sec
                decoded_frames += 1
                if timestamp_sec + 1e-9 < next_sample_sec:
                    continue
                next_sample_sec = (math.floor(timestamp_sec * self.sample_fps + 1e-9) + 1) / (
                    self.sample_fps
                )
                observation = self.observe(frame, timestamp_sec)
                events = rule_engine.process(observation)
                if events:
                    # Use the actual analyzed frame: a second seek can select a neighbour.
                    evidence = create_evidence_frame(video_path, timestamp_sec, frame=frame)
                    violations.extend(
                        Violation(
                            type=event.type,
                            timecode_sec=evidence.timestamp_sec,
                            description=event.description,
                            evidence_frames=[evidence],
                        )
                        for event in events
                    )
            if decoded_frames == 0:
                raise RuntimeError("Video contains no decodable frames")
            # Tolerate one-frame metadata rounding, but never return a partial clean result.
            if total_frames and decoded_frames < total_frames - 1:
                raise RuntimeError("Video decoding stopped before the advertised end")
        finally:
            cap.release()

        verdict = "violation" if violations else "no_violation"
        logger.info("Analysis complete: %s, %d events", verdict, len(violations))
        return AnalysisResult(verdict=verdict, violations=violations)


def analyze_video(video_path: Path) -> dict:
    """Worker entry point: app.services.video_analyzer:analyze_video."""
    settings = VideoAnalysisSettings()
    provider = (
        load_analyzer(settings.video_signal_provider) if settings.video_signal_provider else None
    )
    analyzer = VideoAnalyzer(
        sample_fps=settings.video_sample_fps,
        model_path=settings.video_model_path,
        signal_provider=provider,
    )
    return analyzer.analyze(video_path).model_dump()
