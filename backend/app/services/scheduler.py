"""In-process scheduler for periodic ingestion.

APScheduler rather than Celery: the MVP has exactly one recurring job and no
need for a broker, a result backend or a second container. The job body is a
plain function, so moving to a distributed worker later is a change of trigger,
not a rewrite of the work.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import session_scope
from app.services.ingestion import run_ingestion
from app.services.seed import get_default_organization

logger = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None

INGEST_JOB_ID = "ingest-competitors"


def ingest_job() -> None:
    """Refresh every competitor feed. Swallows errors: a scheduler that dies on
    one bad poll stops monitoring everything else."""
    try:
        with session_scope() as session:
            org = get_default_organization(session)
            report = run_ingestion(session, org.id)
        logger.info("Scheduled ingestion finished: %s", report.as_dict())
    except Exception:
        logger.exception("Scheduled ingestion failed")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (set DSS_SCHEDULER_ENABLED=true to enable).")
        return None
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        ingest_job,
        trigger=IntervalTrigger(minutes=settings.crawl_interval_minutes),
        id=INGEST_JOB_ID,
        name="Ingest competitor feeds",
        # A slow poll must not queue up behind itself.
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started: ingestion every %d minute(s)", settings.crawl_interval_minutes)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
