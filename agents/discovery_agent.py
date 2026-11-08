"""
Unstop Autopilot — Discovery Agent

Orchestrates the opportunity discovery pipeline: launches a headless
browser, scrapes Unstop listing pages, deduplicates against the database,
and persists new/updated opportunities.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select

from config import settings
from database.connection import get_session
from database.models import ActivityLog, Opportunity

logger = logging.getLogger(__name__)


class DiscoveryAgent:
    """Orchestrates the opportunity discovery pipeline.

    Coordinates browser-based scraping with database persistence.
    All browser and discovery imports are lazy so the module can be
    imported without Playwright being installed (useful for testing
    the DB-only paths).
    """

    # Fields that should be updated when a duplicate is found and the
    # incoming data has a non-empty value.
    _UPDATABLE_FIELDS: list[str] = [
        "title",
        "description",
        "deadline",
        "fee",
        "mode",
        "category",
        "certificate_info",
        "participants_count",
        "source_evidence",
        "source_url",
    ]

    def __init__(self) -> None:
        # Lazy — actual instances are created in run_discovery()
        self._browser_manager: Any = None
        self._discovery: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_discovery(
        self,
        callback: Callable[[str], None] | None = None,
    ) -> dict:
        """Execute the full discovery pipeline.

        Args:
            callback: Optional callable that receives progress status
                messages (e.g. for UI updates).

        Returns:
            A summary dict with keys:
                - ``total_found``  – raw opportunities returned by the scraper
                - ``new_saved``    – newly inserted into the database
                - ``updated``      – existing records updated with fresh data
                - ``duplicates_skipped`` – duplicates with no changes
                - ``expired_skipped``    – skipped because deadline has passed
                - ``errors``       – list of error message strings
        """
        summary: dict[str, Any] = {
            "total_found": 0,
            "new_saved": 0,
            "updated": 0,
            "duplicates_skipped": 0,
            "expired_skipped": 0,
            "errors": [],
        }

        def _notify(msg: str) -> None:
            logger.info(msg)
            if callback is not None:
                try:
                    callback(msg)
                except Exception:
                    pass  # never let a callback crash the pipeline

        _notify("Discovery pipeline starting...")

        # ----------------------------------------------------------
        # Step 1 — Launch browser
        # ----------------------------------------------------------
        try:
            from browser.browser_manager import BrowserManager

            _notify("Launching headless browser...")
            self._browser_manager = BrowserManager()
            self._browser_manager.launch()
        except ImportError as exc:
            error = f"Browser module not available: {exc}"
            logger.error(error)
            summary["errors"].append(error)
            self._log_activity("discovery_run", error, status="error")
            return summary
        except Exception as exc:
            error = f"Failed to launch browser: {exc}"
            logger.error(error, exc_info=True)
            summary["errors"].append(error)
            self._log_activity("discovery_run", error, status="error")
            return summary

        # ----------------------------------------------------------
        # Step 2 — Scrape opportunities
        # ----------------------------------------------------------
        try:
            from browser.public_discovery import UnstopDiscovery

            _notify("Scraping Unstop listing pages...")
            self._discovery = UnstopDiscovery(self._browser_manager)
            raw_opportunities = self._discovery.discover_all()
            summary["total_found"] = len(raw_opportunities)
            _notify(f"Found {len(raw_opportunities)} raw opportunities")
        except ImportError as exc:
            error = f"Discovery module not available: {exc}"
            logger.error(error)
            summary["errors"].append(error)
            self._close_browser()
            self._log_activity("discovery_run", error, status="error")
            return summary
        except Exception as exc:
            error = f"Discovery scraping failed: {exc}"
            logger.error(error, exc_info=True)
            summary["errors"].append(error)
            self._close_browser()
            self._log_activity("discovery_run", error, status="error")
            return summary

        # ----------------------------------------------------------
        # Step 3 — Persist to database
        # ----------------------------------------------------------
        if not raw_opportunities:
            _notify("No opportunities found — nothing to persist")
            self._close_browser()
            self._log_activity(
                "discovery_run",
                "Completed with 0 opportunities found",
                status="success",
            )
            return summary

        _notify("Persisting opportunities to database...")
        for raw in raw_opportunities:
            try:
                opp_data = self._raw_to_opportunity(raw)
                url = opp_data.get("url", "")

                if not url:
                    logger.warning("Skipping opportunity with empty URL")
                    continue

                # Check expiry before persisting
                deadline_text = opp_data.get("deadline", "unknown")
                if self._detect_expired(deadline_text):
                    summary["expired_skipped"] += 1
                    logger.debug("Skipping expired opportunity: %s", url)
                    continue

                with get_session() as session:
                    existing = session.execute(
                        select(Opportunity).where(Opportunity.url == url)
                    ).scalar_one_or_none()

                    if existing is None:
                        # New opportunity — insert
                        opportunity = Opportunity(**opp_data)
                        session.add(opportunity)
                        summary["new_saved"] += 1
                        logger.debug("New opportunity saved: %s", url)
                    else:
                        # Existing — update changed fields
                        changed = self._update_existing(existing, opp_data)
                        if changed:
                            summary["updated"] += 1
                            logger.debug("Updated opportunity: %s", url)
                        else:
                            summary["duplicates_skipped"] += 1
                            logger.debug("Duplicate (no changes): %s", url)

            except Exception as exc:
                error = f"Error persisting opportunity: {exc}"
                logger.error(error, exc_info=True)
                summary["errors"].append(error)

        # ----------------------------------------------------------
        # Step 4 — Cleanup & log
        # ----------------------------------------------------------
        self._close_browser()

        details = (
            f"Found {summary['total_found']}, "
            f"new {summary['new_saved']}, "
            f"updated {summary['updated']}, "
            f"duplicates {summary['duplicates_skipped']}, "
            f"expired {summary['expired_skipped']}, "
            f"errors {len(summary['errors'])}"
        )
        status = "success" if not summary["errors"] else "warning"
        self._log_activity("discovery_run", details, status=status)
        _notify(f"Discovery complete — {details}")

        return summary

    # ------------------------------------------------------------------
    # Data conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_to_opportunity(raw: Any) -> dict:
        """Convert a ``RawOpportunity`` dataclass to a dict for DB insertion.

        Handles missing attributes gracefully by falling back to defaults.

        Args:
            raw: A ``RawOpportunity`` instance (or any object with the
                expected attributes).

        Returns:
            A dictionary whose keys match ``Opportunity`` column names.
        """
        # Build source_evidence from all available raw fields
        evidence: dict[str, str] = {}
        for field_name in (
            "title", "description", "deadline", "fee", "mode",
            "category", "certificate_info", "participants_count",
        ):
            value = getattr(raw, field_name, None)
            if value:
                evidence[field_name] = str(value)

        return {
            "title": getattr(raw, "title", "Untitled") or "Untitled",
            "url": getattr(raw, "url", "") or "",
            "description": getattr(raw, "description", "") or "",
            "deadline": getattr(raw, "deadline", "unknown") or "unknown",
            "fee": getattr(raw, "fee", "unknown") or "unknown",
            "mode": getattr(raw, "mode", "unknown") or "unknown",
            "category": getattr(raw, "category", "unknown") or "unknown",
            "certificate_info": getattr(raw, "certificate_info", "unknown") or "unknown",
            "participants_count": getattr(raw, "participants_count", "unknown") or "unknown",
            "source_url": getattr(raw, "source_url", "") or "",
            "source_evidence": json.dumps(evidence),
            "status": "open",
            "is_verified": False,
        }

    @staticmethod
    def _detect_expired(deadline_text: str) -> bool:
        """Try to parse a deadline string and check if it has passed.

        Supports common formats like:
        - ``2026-11-15``
        - ``15 Nov 2026``
        - ``Nov 15, 2026``
        - ``15/11/2026``
        - ``15-11-2026``

        Args:
            deadline_text: The raw deadline string from the scraper.

        Returns:
            ``True`` if the deadline is clearly in the past,
            ``False`` if it cannot be parsed or is in the future.
        """
        if not deadline_text or deadline_text.lower() in ("unknown", "n/a", "tbd", ""):
            return False

        # Try standard date formats
        formats = [
            "%Y-%m-%d",
            "%d %b %Y",
            "%b %d, %Y",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%Y-%m-%dT%H:%M:%S",
            "%d %B %Y",
            "%B %d, %Y",
        ]

        # Strip common prefixes like "Deadline: ", "Ends on ", etc.
        cleaned = re.sub(
            r"^(deadline\s*:?\s*|ends?\s*(on)?\s*:?\s*|closes?\s*(on)?\s*:?\s*)",
            "",
            deadline_text.strip(),
            flags=re.IGNORECASE,
        ).strip()

        now = datetime.now(timezone.utc)

        for fmt in formats:
            try:
                parsed = datetime.strptime(cleaned, fmt)
                # Assume UTC if no timezone info
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed < now
            except ValueError:
                continue

        # Could not parse — don't assume expired
        logger.debug("Could not parse deadline '%s', treating as not expired", deadline_text)
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_existing(self, existing: Opportunity, incoming: dict) -> bool:
        """Update an existing ``Opportunity`` with incoming data.

        Only updates fields where the incoming value is non-empty and
        differs from the current value.

        Args:
            existing: The existing SQLAlchemy model instance.
            incoming: The new data dictionary.

        Returns:
            ``True`` if any field was changed, ``False`` otherwise.
        """
        changed = False
        for field_name in self._UPDATABLE_FIELDS:
            new_value = incoming.get(field_name)
            if not new_value or new_value in ("unknown", ""):
                continue
            current_value = getattr(existing, field_name, None)
            if current_value != new_value:
                setattr(existing, field_name, new_value)
                changed = True
                logger.debug(
                    "Field '%s' updated for %s: '%s' -> '%s'",
                    field_name,
                    existing.url,
                    str(current_value)[:80],
                    str(new_value)[:80],
                )

        if changed:
            existing.updated_at = datetime.now(timezone.utc)

        return changed

    def _close_browser(self) -> None:
        """Safely close the browser manager."""
        if self._browser_manager is not None:
            try:
                self._browser_manager.close()
                logger.debug("Browser closed")
            except Exception as exc:
                logger.warning("Error closing browser: %s", exc)
            finally:
                self._browser_manager = None
                self._discovery = None

    @staticmethod
    def _log_activity(
        action: str,
        details: str,
        status: str = "success",
    ) -> None:
        """Write an entry to the ``activity_logs`` table.

        Args:
            action: Short action identifier (e.g. ``discovery_run``).
            details: Free-form description of what happened.
            status: One of ``success``, ``error``, ``warning``.
        """
        try:
            with get_session() as session:
                log_entry = ActivityLog(
                    action=action,
                    details=details,
                    status=status,
                    module="discovery_agent",
                )
                session.add(log_entry)
            logger.debug("Activity logged: [%s] %s — %s", status, action, details)
        except Exception as exc:
            # Logging failures must never crash the pipeline
            logger.error("Failed to write activity log: %s", exc)
