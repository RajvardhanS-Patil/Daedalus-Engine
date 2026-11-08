"""
Unstop Autopilot — Linux Systemd User Service Manager

Generates and manages the Linux systemd user service unit (unstop-autopilot.service)
enabling background opportunity crawls, deadline alarms, and autonomous scheduling
without root permissions.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

SYSTEMD_UNIT_TEMPLATE = """[Unit]
Description=Unstop Autopilot — Autonomous Competition Management Daemon
Documentation=https://github.com/unstop-autopilot
After=network.target

[Service]
Type=simple
WorkingDirectory={workspace_dir}
ExecStart={python_bin} {main_script} --daemon
Restart=on-failure
RestartSec=15
Environment=PYTHONPATH={workspace_dir}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


class SystemdServiceManager:
    """Manages systemd user service installation, execution, and status."""

    SERVICE_NAME = "unstop-autopilot.service"

    def __init__(self) -> None:
        self.workspace_dir = Path(__file__).resolve().parent.parent
        self.python_bin = self._resolve_python_bin()
        self.main_script = self.workspace_dir / "main.py"
        self.user_systemd_dir = Path.home() / ".config" / "systemd" / "user"
        self.unit_file_path = self.user_systemd_dir / self.SERVICE_NAME

    def _resolve_python_bin(self) -> Path:
        """Locate the virtualenv python executable if available."""
        venv_py = self.workspace_dir / ".venv" / "bin" / "python"
        if venv_py.exists():
            return venv_py
        return Path(sys.executable)

    def generate_unit_file(self, target_path: Path | None = None) -> Path:
        """Write the systemd user service unit file."""
        out_path = target_path or self.unit_file_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        content = SYSTEMD_UNIT_TEMPLATE.format(
            workspace_dir=str(self.workspace_dir),
            python_bin=str(self.python_bin),
            main_script=str(self.main_script),
        )

        out_path.write_text(content, encoding="utf-8")
        logger.info("Generated systemd service unit at '%s'", out_path)
        return out_path

    def is_systemd_available(self) -> bool:
        """Check if systemctl command is present and user daemon responds."""
        if not shutil.which("systemctl"):
            return False
        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-system-running"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return res.returncode in (0, 1)  # 0 is running, 1 is degraded (both valid)
        except Exception:
            return False

    def reload_daemon(self) -> tuple[bool, str]:
        """Run systemctl --user daemon-reload."""
        return self._run_systemctl(["daemon-reload"])

    def install_and_enable(self) -> tuple[bool, str]:
        """Generate unit file, reload daemon, and enable service."""
        try:
            self.generate_unit_file()
            ok, out = self.reload_daemon()
            if not ok:
                return False, out
            return self._run_systemctl(["enable", self.SERVICE_NAME])
        except Exception as exc:
            return False, str(exc)

    def start(self) -> tuple[bool, str]:
        """Start the systemd user service."""
        return self._run_systemctl(["start", self.SERVICE_NAME])

    def stop(self) -> tuple[bool, str]:
        """Stop the systemd user service."""
        return self._run_systemctl(["stop", self.SERVICE_NAME])

    def restart(self) -> tuple[bool, str]:
        """Restart the systemd user service."""
        return self._run_systemctl(["restart", self.SERVICE_NAME])

    def get_status(self) -> dict[str, Any]:
        """Query systemd user service state and metadata."""
        installed = self.unit_file_path.exists()
        available = self.is_systemd_available()
        is_active = False
        is_enabled = False
        details = ""

        if available and installed:
            try:
                act_res = subprocess.run(
                    ["systemctl", "--user", "is-active", self.SERVICE_NAME],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                is_active = act_res.stdout.strip() == "active"

                en_res = subprocess.run(
                    ["systemctl", "--user", "is-enabled", self.SERVICE_NAME],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                is_enabled = en_res.stdout.strip() == "enabled"

                stat_res = subprocess.run(
                    ["systemctl", "--user", "status", self.SERVICE_NAME, "--lines=5"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                details = stat_res.stdout
            except Exception as exc:
                details = f"Query failed: {exc}"

        return {
            "systemd_available": available,
            "unit_installed": installed,
            "unit_file_path": str(self.unit_file_path),
            "is_active": is_active,
            "is_enabled": is_enabled,
            "details": details,
        }

    def _run_systemctl(self, args: list[str]) -> tuple[bool, str]:
        """Execute a systemctl --user command safely."""
        if not self.is_systemd_available():
            return False, "systemd user session is not available on this environment"

        cmd = ["systemctl", "--user"] + args
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return True, res.stdout.strip() or "Success"
            return False, res.stderr.strip() or f"Command exited with code {res.returncode}"
        except Exception as exc:
            return False, str(exc)
