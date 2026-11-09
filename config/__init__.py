"""
Unstop Autopilot — Central Configuration

All application settings are defined here with sensible defaults.
Override via .env file or environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    """Read an environment variable with a fallback default."""
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    """Read an integer environment variable."""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    """Read a boolean environment variable."""
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


@dataclass
class OllamaSettings:
    """Ollama LLM runtime configuration."""
    base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5:7b"))
    context_size: int = field(default_factory=lambda: _env_int("OLLAMA_CONTEXT_SIZE", 8192))
    timeout: int = field(default_factory=lambda: _env_int("OLLAMA_TIMEOUT", 120))
    temperature: float = 0.3
    top_p: float = 0.9
    # VRAM safety: max tokens to generate
    max_tokens: int = 4096


@dataclass
class BrowserSettings:
    """Playwright browser configuration."""
    headless: bool = field(default_factory=lambda: _env_bool("BROWSER_HEADLESS", True))
    slow_mo: int = field(default_factory=lambda: _env_int("BROWSER_SLOW_MO", 0))
    timeout: int = field(default_factory=lambda: _env_int("BROWSER_TIMEOUT", 30000))
    user_data_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "data" / "browser_profile")
    # User-agent to identify ourselves
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )


@dataclass
class DiscoverySettings:
    """Opportunity discovery configuration."""
    delay_seconds: float = field(default_factory=lambda: float(_env_int("DISCOVERY_DELAY_SECONDS", 3)))
    max_pages: int = field(default_factory=lambda: _env_int("DISCOVERY_MAX_PAGES", 5))

    # Configurable target URLs for public listing pages
    target_urls: list[str] = field(default_factory=lambda: [
        "https://unstop.com/competitions?oppstatus=open",
        "https://unstop.com/hackathons?oppstatus=open",
        "https://unstop.com/quizzes?oppstatus=open",
    ])

    # Configurable CSS selectors — update these when Unstop changes its UI
    selectors: dict[str, str] = field(default_factory=lambda: {
        # Main listing container
        "opportunity_card": "a.item, a[href*='/competitions/'], a[href*='/hackathons/'], a[href*='/quiz/'], a[href*='/quizzes/']",
        # Fallback card selector
        "opportunity_card_alt": "a.single_profile, .opp-listing a[href*='/competition/'], .opp-listing a[href*='/hackathon/'], .opp-listing a[href*='/quiz/']",
        # Within each card
        "title": "h3[itemprop='name'], h3.double-wrap, h3, .double-wrap h2, .title, h3.opp-title",
        "description": "p.single-wrap, .caption, .desc, .opp-desc",
        "deadline": ".date, .deadline, .opp-deadline, [class*='deadline'], [class*='date']",
        "fee": ".fee, .price, [class*='fee'], [class*='price']",
        "mode": ".mode, [class*='online'], [class*='offline'], [class*='hybrid']",
        "category": ".type, .category, [class*='category']",
        "certificate": "[class*='certificate'], [class*='cert']",
        "participants": ".participants, [class*='registered'], [class*='participant']",
        # Pagination
        "next_page": "button.next, a.next, [aria-label='Next'], .pagination .next",
        "load_more": "button:has-text('Load More'), button:has-text('Show More')",
    })


@dataclass
class ScoringWeights:
    """Opportunity suitability scoring weights (must sum to 100)."""
    individual_participation: int = 20
    free_registration: int = 20
    certificate_available: int = 15
    online_participation: int = 15
    eligibility_match: int = 15
    reasonable_time_commitment: int = 10
    deadline_suitability: int = 5

    def validate(self) -> bool:
        """Check that weights sum to 100."""
        total = (
            self.individual_participation
            + self.free_registration
            + self.certificate_available
            + self.online_participation
            + self.eligibility_match
            + self.reasonable_time_commitment
            + self.deadline_suitability
        )
        return total == 100


@dataclass
class DatabaseSettings:
    """SQLite database configuration."""
    url: str = field(default_factory=lambda: _env(
        "DATABASE_URL",
        f"sqlite:///{_PROJECT_ROOT / 'data' / 'unstop_autopilot.db'}"
    ))
    echo: bool = False  # Set True for SQL debugging


@dataclass
class SchedulerSettings:
    """APScheduler configuration."""
    enabled: bool = field(default_factory=lambda: _env_bool("SCHEDULER_ENABLED", False))
    discovery_hour: int = field(default_factory=lambda: _env_int("DISCOVERY_SCHEDULE_HOUR", 9))
    discovery_minute: int = field(default_factory=lambda: _env_int("DISCOVERY_SCHEDULE_MINUTE", 0))


@dataclass
class LoggingSettings:
    """Logging configuration."""
    level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    log_file: Path = field(default_factory=lambda: _PROJECT_ROOT / "data" / "logs" / "autopilot.log")
    max_bytes: int = 10 * 1024 * 1024  # 10 MB
    backup_count: int = 5


@dataclass
class UserPreferences:
    """Default user preference filters for opportunity matching."""
    prefer_free: bool = True
    prefer_individual: bool = True
    prefer_online: bool = True
    prefer_certificate: bool = True
    prefer_student_eligible: bool = True
    min_days_before_deadline: int = 3


@dataclass
class QuizAutomationSettings:
    """Live quiz autonomous solving configuration."""
    autonomous_mode: bool = field(default_factory=lambda: _env_bool("QUIZ_AUTONOMOUS_MODE", True))
    human_like_delay_min: float = 0.5
    human_like_delay_max: float = 1.5
    max_solve_timeout: int = field(default_factory=lambda: _env_int("QUIZ_SOLVE_TIMEOUT", 8))
    abort_on_proctoring: bool = field(default_factory=lambda: _env_bool("QUIZ_ABORT_ON_PROCTORING", True))
    skip_low_confidence: bool = False


@dataclass
class Settings:
    """Master settings container."""
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    scoring: ScoringWeights = field(default_factory=ScoringWeights)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    user_preferences: UserPreferences = field(default_factory=UserPreferences)
    quiz_automation: QuizAutomationSettings = field(default_factory=QuizAutomationSettings)
    project_root: Path = _PROJECT_ROOT
    version: str = "0.1.0"
    app_name: str = "Unstop Autopilot"


# Singleton instance
settings = Settings()
