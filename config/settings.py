"""
Unstop Autopilot — Central Configuration

Re-exports from config package for convenience.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import (
    Settings,
    OllamaSettings,
    BrowserSettings,
    DiscoverySettings,
    ScoringWeights,
    DatabaseSettings,
    SchedulerSettings,
    LoggingSettings,
    UserPreferences,
    settings,
)

__all__ = [
    "Settings",
    "OllamaSettings",
    "BrowserSettings",
    "DiscoverySettings",
    "ScoringWeights",
    "DatabaseSettings",
    "SchedulerSettings",
    "LoggingSettings",
    "UserPreferences",
    "settings",
]
