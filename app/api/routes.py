import logging
import sqlite3
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect

from app.core.security import require_api_key
from app.rules.engine import evaluate
from app.rules.schemas import RuleReport, RuleRequest
from app.schemas import JobCreated, JobResponse
from app.services.jobs import QueueFull, UploadDeadlineExceeded
from app.services.multipart import parse_video_form
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
        400: {"description": "Invalid multipart, request headers, extension or empty file"},
        401: {"description": "Missing or invalid X-API-Key"},
        408: {"description": "Upload exhausted the total job time budget"},
        413: {"description": "File or total request body exceeds its configured limit"},
        422: {"description": "Required file field is missing"},
        503: {"description": "Queue is full or upload storage is unavailable"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": "One .mov video file",
                            }
                        },
                        "additionalProperties": False,
                    }
                }
            },
        }
    },
)
async def analyze(request: Request) -> JobCreated:
    form = None
    try:
        form = await parse_video_form(request, request.app.state.settings.max_upload_bytes)
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "type": "missing",
                        "loc": ["body", "file"],
                        "msg": "Field required",
                        "input": None,
                    }
                ],
            )
        job_id = await run_in_threadpool(
            request.app.state.uploads.save,
            file.filename,
            file.file,
            request.state.upload_started_at,
        )
    except ClientDisconnect as exc:
        raise HTTPException(status_code=400, detail="Upload connection was interrupted") from exc
    except QueueFull as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "QUEUE_FULL", "message": "Too many active jobs; try again later"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except UploadDeadlineExceeded as exc:
        raise HTTPException(
            status_code=408,
            detail={"code": "UPLOAD_TIMEOUT", "message": "Upload exhausted the job time budget"},
        ) from exc
    except InvalidUpload as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Unable to persist the uploaded video and job")
        raise HTTPException(status_code=503, detail="Upload storage is unavailable") from exc
    finally:
        if form is not None:
            await form.close()
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
