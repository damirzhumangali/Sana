import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.core.security import require_api_key
from app.schemas import JobCreated
from app.services.uploads import InvalidUpload, UploadTooLarge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.post(
    "/analyze",
    response_model=JobCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["jobs"],
    summary="Upload a .mov video and register a job",
    description="Stage 1: stores the upload. Video analysis is not connected yet.",
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
