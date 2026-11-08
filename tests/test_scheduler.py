"""
Unit tests for APScheduler Manager and Linux Systemd Service Manager
"""

import os
from pathlib import Path

import pytest
from database.connection import get_session
from database.models import AutomationJob
from scheduler.jobs import SchedulerLockError, SchedulerManager
from scheduler.service import SystemdServiceManager


def test_scheduler_lock_acquisition_and_duplicate_prevention(tmp_path):
    """Verify exclusive lock acquisition prevents duplicate scheduler instances."""
    lock_file = tmp_path / "test_scheduler.lock"

    mgr1 = SchedulerManager(lock_path=lock_file)
    assert mgr1.acquire_lock() is True

    # Second manager attempting to lock same file must fail
    mgr2 = SchedulerManager(lock_path=lock_file)
    assert mgr2.acquire_lock() is False

    with pytest.raises(SchedulerLockError):
        mgr2.start()

    # Release lock on mgr1
    mgr1.release_lock()

    # Now mgr2 can acquire lock
    assert mgr2.acquire_lock() is True
    mgr2.release_lock()


def test_scheduler_jobs_lifecycle_and_execution(tmp_path):
    """Verify scheduler startup, database job tracking, and manual job triggering."""
    lock_file = tmp_path / "test_lifecycle.lock"
    mgr = SchedulerManager(lock_path=lock_file)

    assert mgr.start() is True
    assert mgr.is_active is True

    # Check database initialization of jobs
    jobs = mgr.get_jobs_status()
    assert len(jobs) >= 3
    job_names = [j["job_name"] for j in jobs]
    assert "daily_discovery" in job_names
    assert "deadline_watch" in job_names
    assert "intelligence_batch" in job_names

    # Manually execute deadline_watch
    exec_res = mgr.execute_job("deadline_watch")
    assert exec_res["status"] == "success"
    assert "checked_opportunities" in exec_res["result"]

    # Verify updated database record
    updated_jobs = mgr.get_jobs_status()
    dw_job = next(j for j in updated_jobs if j["job_name"] == "deadline_watch")
    assert dw_job["last_status"] == "success"
    assert dw_job["last_run"] != "Never"

    mgr.stop()
    assert mgr.is_active is False


def test_systemd_service_unit_generation(tmp_path):
    """Verify generation of valid Linux systemd user service unit file."""
    svc_mgr = SystemdServiceManager()
    custom_unit = tmp_path / "unstop-autopilot.service"

    generated_path = svc_mgr.generate_unit_file(target_path=custom_unit)
    assert generated_path.exists()

    content = generated_path.read_text(encoding="utf-8")
    assert "[Unit]" in content
    assert "Description=Unstop Autopilot" in content
    assert "[Service]" in content
    assert "WorkingDirectory=" in content
    assert "ExecStart=" in content
    assert "--daemon" in content
    assert "[Install]" in content

    # Query status dictionary
    status = svc_mgr.get_status()
    assert "systemd_available" in status
    assert "unit_file_path" in status
