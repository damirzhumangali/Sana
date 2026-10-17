import logging
import sqlite3
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.core.security import require_api_key
from app.rules.engine import evaluate
from app.rules.schemas import RuleReport, RuleRequest
from app.schemas import JobCreated, JobResponse
from app.services.uploads import InvalidUpload, UploadTooLarge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.post(
    "/rules/evaluate",
    response_model=RuleReport,
    tags=["rules"],
    summary="Evaluate R01-R04 on a confirmed semantic event timeline",
    description=(
        "Accepts temporally confirmed events and reliable observation intervals. "
        "This endpoint does not detect actions in video. Missing coverage prevents "
        "a no_violation verdict. Same X-API-Key as video endpoints."
    ),
)
def evaluate_rules(timeline: RuleRequest) -> RuleReport:
    return evaluate(timeline)


@router.post(
    "/analyze",
    response_model=JobCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["jobs"],
    summary="Upload a .mov video and register a job",
    description="Stores the video and queues it for a separately configured analysis worker.",
    responses={
        400: {"description": "Unsupported extension or empty file"},
        401: {"description": "Missing or invalid X-API-Key"},
        413: {"description": "File exceeds the configured upload limit"},
        503: {"description": "Upload storage is unavailable"},
    },
)
def analyze(
    request: Request,
    file: Annotated[UploadFile, File(description="Video file with a .mov extension")],
) -> JobCreated:
    try:
        job_id = request.app.state.uploads.save(file.filename, file.file)
    except InvalidUpload as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Unable to persist the uploaded video and job")
        raise HTTPException(status_code=503, detail="Upload storage is unavailable") from exc
    finally:
        file.file.close()
    return JobCreated(job_id=job_id)


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    tags=["jobs"],
    summary="Get job status and the completed analysis result",
    responses={
        401: {"description": "Missing or invalid X-API-Key"},
        404: {"description": "Job not found"},
        503: {"description": "Job storage is unavailable"},
    },
)
def get_job(request: Request, job_id: UUID) -> JobResponse:
    try:
        job = request.app.state.jobs.get(job_id)
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.exception("Unable to read job %s", job_id)
        raise HTTPException(status_code=503, detail="Job storage is unavailable") from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
