from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import Settings
from app.services.jobs import JobRepository
from app.services.uploads import UploadService


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config = settings if settings is not None else Settings()
        storage_dir = config.storage_dir.resolve()
        uploads_dir = storage_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        jobs = JobRepository(storage_dir / "jobs.sqlite3")
        jobs.initialize()
        application.state.settings = config
        application.state.jobs = jobs
        application.state.uploads = UploadService(uploads_dir, config.max_upload_bytes, jobs)
        yield

    application = FastAPI(
        title="Sana — ForteBank Video Analysis API",
        version="0.2.0",
        description="Local video uploads, persistent jobs and analysis results.",
        lifespan=lifespan,
    )
    application.include_router(router)

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
