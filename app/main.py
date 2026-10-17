import asyncio
import logging
from pathlib import Path
import sqlite3
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.api.routes import router
from app.core.config import Settings
from app.middleware import UploadProtectionMiddleware
from app.services.cleanup import StorageCleanup
from app.services.jobs import JobRepository
from app.services.uploads import UploadService

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"
logger = logging.getLogger(__name__)


async def sweep_deadlines(jobs: JobRepository) -> None:
    while True:
        try:
            await asyncio.to_thread(jobs.expire_due)
        except (sqlite3.Error, OSError):
            logger.exception("Unable to expire overdue jobs")
        await asyncio.sleep(1)


async def sweep_storage(cleanup: StorageCleanup, interval: float) -> None:
    while True:
        operation = asyncio.create_task(asyncio.to_thread(cleanup.run))
        try:
            try:
                stats = await asyncio.shield(operation)
            except asyncio.CancelledError:
                # Finish an in-flight deletion pass before closing the application.
                await asyncio.gather(operation, return_exceptions=True)
                raise
            if stats.deleted_jobs or stats.deleted_uploads or stats.deleted_work_files:
                logger.info("Storage cleanup: %s", stats)
        except (OSError, ValueError, sqlite3.Error):
            logger.exception("Storage cleanup failed; it will be retried")
        await asyncio.sleep(interval)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config = settings if settings is not None else Settings()
        storage_dir = config.storage_dir.resolve()
        uploads_dir = storage_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        jobs = JobRepository(
            storage_dir / "jobs.sqlite3",
            config.total_job_timeout_seconds,
            config.max_active_jobs,
            config.max_running_jobs,
        )
        jobs.initialize()
        application.state.settings = config
        application.state.jobs = jobs
        application.state.uploads = UploadService(uploads_dir, config.max_upload_bytes, jobs)
        cleanup = StorageCleanup(
            jobs,
            storage_dir,
            config.job_retention_seconds,
            config.orphan_grace_seconds,
            config.cleanup_batch_size,
        )
        application.state.cleanup = cleanup
        sweepers = [asyncio.create_task(sweep_deadlines(jobs))]
        if config.cleanup_enabled:
            sweepers.append(
                asyncio.create_task(sweep_storage(cleanup, config.cleanup_interval_seconds))
            )
        try:
            yield
        finally:
            for sweeper in sweepers:
                sweeper.cancel()
            for sweeper in sweepers:
                with suppress(asyncio.CancelledError):
                    await sweeper

    application = FastAPI(
        title="Sana — ForteBank Video Analysis API",
        version="0.5.0",
        description="Local video uploads, persistent jobs and analysis results.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)
    application.add_middleware(UploadProtectionMiddleware)

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get(
        "/",
        response_class=HTMLResponse,
        tags=["ui"],
        summary="Web User Interface for Video Analysis",
    )
    def index() -> HTMLResponse:
        if STATIC_INDEX.is_file():
            return HTMLResponse(content=STATIC_INDEX.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Sana Video Analysis</h1>")

    return application


app = create_app()
