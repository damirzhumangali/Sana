"""Run with: python -m app.worker. Requires a real local ANALYZER_CALLABLE."""

import json
import logging
import multiprocessing
import time
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.schemas import AnalysisResult, JobError
from app.services.analyzer import analyze_in_subprocess
from app.services.jobs import ClaimedJob, JobRepository

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        jobs: JobRepository,
        analyzer_callable: str,
        work_dir: Path,
        timeout_seconds: int = 240,
    ) -> None:
        self.jobs = jobs
        self.analyzer_callable = analyzer_callable
        self.work_dir = work_dir
        self.timeout_seconds = timeout_seconds
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> bool:
        self.jobs.expire_due()
        job = self.jobs.claim_next(self.timeout_seconds)
        if job is None:
            return False
        logger.info("Processing job %s", job.job_id)
        self._execute(job)
        return True

    def _execute(self, job: ClaimedJob) -> None:
        output_path = self.work_dir / f"{job.job_id}-{job.run_token}.json"
        process = multiprocessing.get_context("spawn").Process(
            target=analyze_in_subprocess,
            args=(self.analyzer_callable, job.video_path, output_path),
        )
        try:
            process.start()
            remaining = max(0, (job.deadline_at - datetime.now(UTC)).total_seconds())
            process.join(remaining)
            if process.is_alive():
                self._stop(process)
                self.jobs.fail(
                    job,
                    JobError(code="JOB_TIMEOUT", message="Video analysis exceeded its time limit"),
                )
                return
            if process.exitcode != 0 or not output_path.is_file():
                self.jobs.fail(
                    job,
                    JobError(code="WORKER_ERROR", message="Analysis process stopped unexpectedly"),
                )
                return
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            if "result" in payload:
                self.jobs.complete(job, AnalysisResult.model_validate(payload["result"]))
            else:
                self.jobs.fail(job, JobError.model_validate(payload["error"]))
        except KeyboardInterrupt:
            self.jobs.fail(
                job, JobError(code="WORKER_INTERRUPTED", message="Analysis worker was stopped")
            )
            raise
        except Exception:
            logger.exception("Worker failed for job %s", job.job_id)
            self.jobs.fail(
                job,
                JobError(code="WORKER_ERROR", message="Analysis worker could not complete the job"),
            )
        finally:
            if process.pid is not None:
                self._stop(process)
                process.close()
            try:
                output_path.unlink(missing_ok=True)
                output_path.with_suffix(".tmp").unlink(missing_ok=True)
            finally:
                self.jobs.release_execution(job)

    @staticmethod
    def _stop(process) -> None:
        if process.is_alive():
            process.terminate()
            process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    if not settings.analyzer_callable:
        logger.error(
            "Worker not started: set ANALYZER_CALLABLE=package.module:function "
            "to your team's local analysis function. No jobs were claimed."
        )
        return 2
    storage = settings.storage_dir.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    jobs = JobRepository(
        storage / "jobs.sqlite3",
        settings.total_job_timeout_seconds,
        settings.max_active_jobs,
        settings.max_running_jobs,
    )
    jobs.initialize()
    worker = Worker(
        jobs,
        settings.analyzer_callable,
        storage / "work",
        settings.job_timeout_seconds,
    )
    logger.info("Worker started; analysis timeout: %s seconds", settings.job_timeout_seconds)
    try:
        while True:
            if not worker.run_once():
                time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
