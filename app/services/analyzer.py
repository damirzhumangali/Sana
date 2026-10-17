"""A local callable accepts Path and returns AnalysisResult or a matching dict."""

import importlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from app.schemas import AnalysisResult, JobError

logger = logging.getLogger(__name__)


def load_analyzer(reference: str) -> Callable:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("ANALYZER_CALLABLE must have the form package.module:function")
    analyzer = getattr(importlib.import_module(module_name), attribute)
    if not callable(analyzer):
        raise ValueError("ANALYZER_CALLABLE must point to a callable")
    return analyzer


def analyze_video(video_path: Path) -> dict:
    """Baseline reference analyzer: returns no_violation until real model is integrated."""
    return {"verdict": "no_violation", "violations": []}


def analyze_in_subprocess(reference: str, video_path: Path, output_path: Path) -> None:
    """Run in a disposable process so a timeout can actually stop inference."""
    try:
        analyzer = load_analyzer(reference)
        if not video_path.is_file():
            payload = {
                "error": JobError(
                    code="VIDEO_NOT_FOUND", message="Uploaded video is unavailable"
                ).model_dump()
            }
        else:
            result = AnalysisResult.model_validate(analyzer(video_path))
            payload = {"result": result.model_dump()}
    except ValidationError:
        logger.exception("Analyzer returned an invalid result")
        payload = {
            "error": JobError(
                code="INVALID_RESULT", message="Analyzer returned an invalid result"
            ).model_dump()
        }
    except Exception:
        logger.exception("Local video analysis failed")
        payload = {
            "error": JobError(
                code="ANALYZER_ERROR", message="Local video analysis failed"
            ).model_dump()
        }

    temporary_path = output_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(output_path)
