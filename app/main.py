import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import Settings
from app.middleware import UploadProtectionMiddleware
from app.services.jobs import JobRepository
from app.services.uploads import UploadService

logger = logging.getLogger(__name__)


async def sweep_deadlines(jobs: JobRepository) -> None:
    while True:
        try:
            await asyncio.to_thread(jobs.expire_due)
        except (sqlite3.Error, OSError):
            logger.exception("Unable to expire overdue jobs")
        await asyncio.sleep(1)


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
        sweeper = asyncio.create_task(sweep_deadlines(jobs))
        try:
            yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper

    application = FastAPI(
        title="Sana — ForteBank Video Analysis API",
        version="0.4.0",
        description="Local video uploads, persistent jobs and analysis results.",
        lifespan=lifespan,
    )
    application.include_router(router)
    application.add_middleware(UploadProtectionMiddleware)

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
