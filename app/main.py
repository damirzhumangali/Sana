from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.api.routes import router
from app.core.config import Settings
from app.services.jobs import JobRepository
from app.services.uploads import UploadService

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)

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
