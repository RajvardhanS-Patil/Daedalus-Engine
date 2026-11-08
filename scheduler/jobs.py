"""
Unstop Autopilot — Background Scheduler & Job Runner

Coordinates scheduled recurring background jobs using APScheduler.
Ensures single-instance execution via Linux fcntl file locking,
records job telemetry in the SQLite automation_jobs table, and handles
graceful shutdown and crash recovery.
"""

from __future__ import annotations

import fcntl
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from agents.discovery_agent import DiscoveryAgent
from agents.intelligence_agent import IntelligenceAgent
from config import settings
from database.connection import get_session
from database.models import ActivityLog, AutomationJob, Opportunity

logger = logging.getLogger(__name__)


class SchedulerLockError(Exception):
    """Raised when another scheduler daemon instance holds the file lock."""
    pass


class SchedulerManager:
    """Coordinates scheduled tasks, atomic locking, and database status logging."""

    DEFAULT_JOBS = [
        {
            "name": "daily_discovery",
            "type": "discovery",
            "schedule": "cron: 09:00 UTC",
            "description": "Daily automated crawl of Unstop competitions, hackathons, and quizzes",
        },
        {
            "name": "deadline_watch",
            "type": "deadline_check",
            "schedule": "interval: 4 hours",
            "description": "Scans open opportunities for upcoming deadlines and flags urgency",
        },
        {
            "name": "intelligence_batch",
            "type": "scoring",
            "schedule": "interval: 6 hours",
            "description": "Evaluates suitability scores and eligibility for newly discovered opportunities",
        },
    ]

    def __init__(self, lock_path: str | Path = "data/scheduler.lock") -> None:
        self.lock_path = Path(lock_path).resolve()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = None
        self._scheduler: BackgroundScheduler | None = None
        self.is_active: bool = False

    def acquire_lock(self) -> bool:
        """Acquire an exclusive non-blocking file lock to prevent duplicate runs."""
        try:
            self._lock_file = open(self.lock_path, "w")
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file.write(f"PID: {Path('/proc/self').resolve().name}\nStarted: {datetime.now(timezone.utc).isoformat()}\n")
            self._lock_file.flush()
            logger.info("Acquired scheduler lock at '%s'", self.lock_path)
            return True
        except (BlockingIOError, IOError):
            logger.warning("Another scheduler instance is already running (locked '%s')", self.lock_path)
            if self._lock_file:
                try:
                    self._lock_file.close()
                except Exception:
                    pass
                self._lock_file = None
            return False

    def release_lock(self) -> None:
        """Release the file lock."""
        if self._lock_file:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
            except Exception as exc:
                logger.debug("Error releasing lock: %s", exc)
            finally:
                self._lock_file = None
                if self.lock_path.exists():
                    try:
                        self.lock_path.unlink()
                    except Exception:
                        pass
                logger.info("Released scheduler lock.")

    def start(self) -> bool:
        """Start the background scheduler with registered recurring jobs."""
        if self.is_active and self._scheduler and self._scheduler.running:
            logger.info("Scheduler is already running.")
            return True

        if not self.acquire_lock():
            raise SchedulerLockError("Could not start scheduler: duplicate instance is already active.")

        self._init_jobs_in_db()

        self._scheduler = BackgroundScheduler(timezone="UTC")

        # 1. Daily Discovery Job
        hour = settings.scheduler.discovery_hour
        minute = settings.scheduler.discovery_minute
        self._scheduler.add_job(
            self.execute_job,
            trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
            args=["daily_discovery"],
            id="daily_discovery",
            replace_existing=True,
            name="Daily Opportunity Discovery",
        )

        # 2. Deadline Watch Job (every 4 hours)
        self._scheduler.add_job(
            self.execute_job,
            trigger=IntervalTrigger(hours=4),
            args=["deadline_watch"],
            id="deadline_watch",
            replace_existing=True,
            name="Deadline Watch & Alerts",
        )

        # 3. Intelligence Batch Job (every 6 hours)
        self._scheduler.add_job(
            self.execute_job,
            trigger=IntervalTrigger(hours=6),
            args=["intelligence_batch"],
            id="intelligence_batch",
            replace_existing=True,
            name="Batch AI Opportunity Intelligence",
        )

        self._scheduler.start()
        self.is_active = True
        logger.info("APScheduler started successfully with 3 background jobs.")
        return True

    def stop(self) -> None:
        """Gracefully shut down the scheduler and release locks."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped.")
        self.is_active = False
        self.release_lock()

    def execute_job(self, job_name: str) -> dict[str, Any]:
        """Execute a job by name, capture execution metrics, and update SQLite."""
        now = datetime.now(timezone.utc)
        logger.info("Executing scheduled job '%s'...", job_name)
        status = "running"
        result_payload: dict[str, Any] = {}
        error_msg = ""

        try:
            if job_name == "daily_discovery":
                agent = DiscoveryAgent()
                summary = agent.run_discovery()
                result_payload = summary
                status = "success"

            elif job_name == "deadline_watch":
                result_payload = self._check_deadlines()
                status = "success"

            elif job_name == "intelligence_batch":
                agent = IntelligenceAgent()
                res = agent.analyze_batch(limit=5, only_unanalyzed=True)
                result_payload = res
                status = "success"

            else:
                raise ValueError(f"Unknown job name: {job_name}")

        except Exception as exc:
            logger.error("Job '%s' encountered an error: %s", job_name, exc, exc_info=True)
            status = "error"
            error_msg = str(exc)
            result_payload = {"error": error_msg}

        # Update SQLite record
        with get_session() as session:
            job_record = session.execute(
                select(AutomationJob).where(AutomationJob.job_name == job_name)
            ).scalar_one_or_none()

            if job_record:
                job_record.last_run = now
                job_record.last_status = status
                job_record.last_result = json.dumps(result_payload)
                if status == "error":
                    job_record.error_count += 1

                # Calculate next run from scheduler if available
                if self._scheduler:
                    sched_job = self._scheduler.get_job(job_name)
                    if sched_job and sched_job.next_run_time:
                        job_record.next_run = sched_job.next_run_time

            # Log activity
            act_log = ActivityLog(
                module="SchedulerManager",
                action=f"job_{job_name}",
                status=status,
                details=f"Job {job_name} finished ({status}): {json.dumps(result_payload)[:200]}",
            )
            session.add(act_log)
            session.commit()

        logger.info("Job '%s' completed with status '%s'", job_name, status)
        return {
            "job_name": job_name,
            "status": status,
            "executed_at": now.isoformat(),
            "result": result_payload,
        }

    def get_jobs_status(self) -> list[dict[str, Any]]:
        """Fetch current telemetry and execution history for all automation jobs."""
        with get_session() as session:
            jobs = session.execute(
                select(AutomationJob).order_by(AutomationJob.id.asc())
            ).scalars().all()

            results = []
            for j in jobs:
                results.append({
                    "id": j.id,
                    "job_name": j.job_name,
                    "job_type": j.job_type,
                    "schedule": j.schedule,
                    "enabled": j.enabled,
                    "last_run": j.last_run.strftime("%Y-%m-%d %H:%M:%S") if j.last_run else "Never",
                    "next_run": j.next_run.strftime("%Y-%m-%d %H:%M:%S") if j.next_run else "Pending",
                    "last_status": j.last_status,
                    "error_count": j.error_count,
                    "last_result_summary": j.last_result[:80] if j.last_result else "",
                })
            return results

    def _init_jobs_in_db(self) -> None:
        """Seed automation_jobs table with standard recurring jobs."""
        with get_session() as session:
            for item in self.DEFAULT_JOBS:
                existing = session.execute(
                    select(AutomationJob).where(AutomationJob.job_name == item["name"])
                ).scalar_one_or_none()
                if not existing:
                    job = AutomationJob(
                        job_name=item["name"],
                        job_type=item["type"],
                        schedule=item["schedule"],
                        enabled=True,
                        last_status="ready",
                    )
                    session.add(job)
            session.commit()

    def _check_deadlines(self) -> dict[str, Any]:
        """Scan open opportunities and detect approaching deadlines."""
        with get_session() as session:
            opps = session.execute(
                select(Opportunity)
                .where(Opportunity.status == "open")
                .where(Opportunity.deadline != "unknown")
            ).scalars().all()

            urgent_count = 0
            flagged = []
            for o in opps:
                # Simple keyword urgency detection
                d_lower = (o.deadline or "").lower()
                if any(w in d_lower for w in ["today", "tomorrow", "hour", "hours", "1 day", "2 days"]):
                    urgent_count += 1
                    flagged.append({"id": o.id, "title": o.title[:40], "deadline": o.deadline})

            return {
                "checked_opportunities": len(opps),
                "urgent_deadlines_found": urgent_count,
                "flagged": flagged,
            }
