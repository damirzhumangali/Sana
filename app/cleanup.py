"""One cleanup pass: python -m app.cleanup [--dry-run]."""

import argparse
import json
import logging
import sqlite3
from dataclasses import asdict

from app.core.config import Settings
from app.services.cleanup import StorageCleanup
from app.services.jobs import JobRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean owned Sana storage artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Report candidates without deleting")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    root = settings.storage_dir.resolve()
    database = root / "jobs.sqlite3"
    if not database.is_file():
        print("No jobs database in the configured storage; nothing to clean")
        return 0
    jobs = JobRepository(database)
    try:
        cleanup = StorageCleanup(
            jobs,
            root,
            settings.job_retention_seconds,
            settings.orphan_grace_seconds,
            settings.cleanup_batch_size,
        )
        stats = cleanup.run(dry_run=args.dry_run)
    except (OSError, ValueError, sqlite3.Error):
        logging.exception("Cleanup could not safely complete")
        return 1
    print(json.dumps({"dry_run": args.dry_run, **asdict(stats)}))
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
