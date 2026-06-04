"""
ingestion/scheduler.py
----------------------
Daily APScheduler trigger for the ingestion pipeline.

Default schedule : 00:00 UTC every day (configurable via env vars).

Environment variables:
    INGEST_HOUR     Cron hour   (default: 0)
    INGEST_MINUTE   Cron minute (default: 0)
    INGEST_TIMEZONE Timezone    (default: UTC)

Usage:
    python -m ingestion.scheduler         # start blocking scheduler
    python -m ingestion.scheduler --once  # run pipeline immediately and exit
"""

import logging
import os
import sys
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _get_schedule() -> dict:
    return {
        "hour":     int(os.getenv("INGEST_HOUR",     "0")),
        "minute":   int(os.getenv("INGEST_MINUTE",   "0")),
        "timezone": os.getenv("INGEST_TIMEZONE", "UTC"),
    }


def run_once() -> None:
    """Trigger the pipeline immediately (used by --once flag and for testing)."""
    from ingestion.ingest import run_full_pipeline
    log.info("Running ingestion pipeline (one-shot) …")
    summary = run_full_pipeline()
    log.info("Pipeline summary: %s", summary)


def start_scheduler() -> None:
    """
    Start the blocking APScheduler.  Runs forever until interrupted (Ctrl-C).
    The first job fires at the next scheduled wall-clock time (not immediately).
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        log.critical(
            "APScheduler not installed. Run: pip install apscheduler --break-system-packages"
        )
        raise SystemExit(1)

    from ingestion.ingest import run_full_pipeline

    schedule = _get_schedule()
    scheduler = BlockingScheduler(timezone=schedule["timezone"])

    scheduler.add_job(
        run_full_pipeline,
        trigger="cron",
        hour=schedule["hour"],
        minute=schedule["minute"],
        id="daily_ingestion",
        name="Daily Fund Ingestion",
        misfire_grace_time=3600,   # allow up to 1-hour late start (e.g. after sleep/restart)
        replace_existing=True,
    )

    next_run = scheduler.get_jobs()[0].next_run_time
    log.info(
        "Scheduler started. Daily ingestion at %02d:%02d %s. Next run: %s",
        schedule["hour"], schedule["minute"], schedule["timezone"],
        next_run.strftime("%d %b %Y %H:%M %Z") if next_run else "unknown",
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if "--once" in sys.argv:
        run_once()
    else:
        start_scheduler()
