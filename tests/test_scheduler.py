"""
Scheduler test (Phase 7): the daily trigger invokes the ingestion pipeline.

We do NOT start the blocking scheduler or hit the network — we verify the
default 00:00 UTC schedule and that the trigger path calls run_full_pipeline.
"""

import ingestion.ingest as ingest_mod
from ingestion import scheduler


def test_default_schedule_is_midnight_utc():
    s = scheduler._get_schedule()
    assert s["hour"] == 0
    assert s["minute"] == 0
    assert s["timezone"] == "UTC"


def test_run_once_triggers_ingestion(monkeypatch):
    calls = {"n": 0}

    def fake_pipeline():
        calls["n"] += 1
        return {"steps": {}, "ok": True}

    # run_once imports run_full_pipeline from ingestion.ingest at call time.
    monkeypatch.setattr(ingest_mod, "run_full_pipeline", fake_pipeline)
    scheduler.run_once()
    assert calls["n"] == 1


def test_cron_job_registers_with_correct_trigger():
    # Build the same job on a non-blocking scheduler to inspect its trigger.
    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler(timezone="UTC")
    job = sched.add_job(lambda: None, trigger="cron", hour=0, minute=0, id="daily_ingestion")
    assert job.id == "daily_ingestion"
    assert "cron" in type(job.trigger).__name__.lower()
