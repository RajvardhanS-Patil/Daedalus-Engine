"""Public discovery engine for Unstop opportunities.

Crawls the configured Unstop listing pages, extracts opportunity cards,
and returns structured :class:`RawOpportunity` dataclass instances.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

from playwright.sync_api import ElementHandle, Page, TimeoutError as PwTimeoutError

from browser.browser_manager import BrowserManager
from config import settings

logger = logging.getLogger(__name__)

_UNSTOP_BASE = "https://unstop.com"

# Known category tokens that may appear in a URL path or card text.
_CATEGORY_TOKENS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhackathon", re.IGNORECASE), "hackathon"),
    (re.compile(r"\bcompetition", re.IGNORECASE), "competition"),
    (re.compile(r"\bquiz", re.IGNORECASE), "quiz"),
    (re.compile(r"\bscholarship", re.IGNORECASE), "scholarship"),
    (re.compile(r"\binternship", re.IGNORECASE), "internship"),
    (re.compile(r"\bjob", re.IGNORECASE), "job"),
    (re.compile(r"\bworkshop", re.IGNORECASE), "workshop"),
    (re.compile(r"\bconference", re.IGNORECASE), "conference"),
    (re.compile(r"\bchallenge", re.IGNORECASE), "challenge"),
    (re.compile(r"\bidea", re.IGNORECASE), "ideathon"),
    (re.compile(r"\bcase.?study", re.IGNORECASE), "case-study"),
    (re.compile(r"\bfest", re.IGNORECASE), "fest"),
]

# Tokens for online/offline/hybrid mode detection.
_MODE_TOKENS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhybrid\b", re.IGNORECASE), "hybrid"),
    (re.compile(r"\bonline\b", re.IGNORECASE), "online"),
    (re.compile(r"\boffline\b", re.IGNORECASE), "offline"),
    (re.compile(r"\bvirtual\b", re.IGNORECASE), "online"),
    (re.compile(r"\bin.?person\b", re.IGNORECASE), "offline"),
]


@dataclass
class RawOpportunity:
    """Lightly-structured representation of a single Unstop opportunity.

    All text fields default to ``'unknown'`` so that downstream consumers
    always have a string to work with.
    """

    title: str
    url: str
    description: str = ""
    deadline: str = "unknown"
    fee: str = "unknown"
    mode: str = "unknown"  # online / offline / hybrid
    category: str = "unknown"  # competition / hackathon / quiz …
    certificate_info: str = "unknown"
    participants_count: str = "unknown"
    source_evidence: dict[str, str] = field(default_factory=dict)
    discovered_at: datetime = field(default_factory=datetime.now)
    source_url: str = ""  # the listing page this card was scraped from


class UnstopDiscovery:
    """Discover opportunities across all configured Unstop listing URLs.

    Parameters
    ----------
    browser_manager:
        An already-launched :class:`BrowserManager` instance.
    """

    def __init__(self, browser_manager: BrowserManager) -> None:
        self.browser: BrowserManager = browser_manager
        self.selectors: dict[str, str] = settings.discovery.selectors

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_all(self) -> list[RawOpportunity]:
        """Run discovery across every target URL in settings.

        Returns a deduplicated list of :class:`RawOpportunity` instances.
        """
        all_opportunities: list[RawOpportunity] = []
        seen_urls: set[str] = set()
        target_urls: list[str] = settings.discovery.target_urls

        if not target_urls:
            logger.warning("No target URLs configured — nothing to discover.")
            return all_opportunities

        page: Page = self.browser.new_page()

        try:
            for target_url in target_urls:
                logger.info("Starting discovery on %s", target_url)
                page_number = 0
                max_pages: int = settings.discovery.max_pages

                try:
                    page.goto(target_url, wait_until="domcontentloaded")
                    # Best-effort wait for the main content to materialise.
                    self._wait_for_content(page)
                except PwTimeoutError:
                    logger.warning(
                        "Timed out loading %s — skipping.", target_url
                    )
                    continue
                except Exception:
                    logger.warning(
                        "Failed to load %s — skipping.",
                        target_url,
                        exc_info=True,
                    )
                    continue

                while page_number < max_pages:
                    page_number += 1
                    logger.info(
                        "Processing page %d / %d for %s",
                        page_number,
                        max_pages,
                        target_url,
                    )

                    raw = self.discover_from_page(page, url=target_url)
                    for opp in raw:
                        if opp.url not in seen_urls:
                            seen_urls.add(opp.url)
                            all_opportunities.append(opp)
                        else:
                            logger.debug("Duplicate URL skipped: %s", opp.url)

                    # Attempt pagination; break if there is nothing more.
                    if page_number < max_pages:
                        has_more = self._handle_pagination(page)
                        if not has_more:
                            logger.info(
                                "No more pages available for %s after page %d.",
                                target_url,
                                page_number,
                            )
                            break
                        # Respect rate limit between page loads.
                        delay = settings.discovery.delay_seconds
                        if delay > 0:
                            logger.debug("Sleeping %.1f s between pages.", delay)
                            time.sleep(delay)

        finally:
            try:
                page.close()
            except Exception:
                logger.debug("Error closing discovery page.", exc_info=True)

        logger.info(
            "Discovery complete — %d unique opportunities found.",
            len(all_opportunities),
        )
        return all_opportunities

    def discover_from_page(
        self, page: Page, url: str
    ) -> list[RawOpportunity]:
        """Extract all opportunity cards visible on the current page.

        Parameters
        ----------
        page:
            A Playwright :class:`Page` already navigated to a listing URL.
        url:
            The listing URL (used for ``source_url`` and URL normalisation).

        Returns
        -------
        list[RawOpportunity]
            One entry per card found; fields that cannot be extracted
            default to ``'unknown'``.
        """
        opportunities: list[RawOpportunity] = []

        # Try the primary selector, then fall back to the alt selector.
        primary_sel: str = self.selectors.get("opportunity_card", "")
        alt_sel: str = self.selectors.get("opportunity_card_alt", "")

        cards: list[ElementHandle] = []
        if primary_sel:
            cards = page.query_selector_all(primary_sel)
        if not cards and alt_sel:
            logger.debug(
                "Primary selector matched 0 cards — trying alt selector."
            )
            cards = page.query_selector_all(alt_sel)

        if not cards:
            logger.warning(
                "No opportunity cards found on %s with selectors '%s' / '%s'.",
                url,
                primary_sel,
                alt_sel,
            )
            return opportunities

        logger.info("Found %d opportunity card(s) on %s.", len(cards), url)

        for card in cards:
            try:
                opp = self._parse_card(card, source_url=url)
                if opp is not None:
                    opportunities.append(opp)
            except Exception:
                logger.warning(
                    "Failed to parse an opportunity card on %s.",
                    url,
                    exc_info=True,
                )

        return opportunities

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_card(
        self, card: ElementHandle, source_url: str
    ) -> RawOpportunity | None:
        """Parse a single card element into a :class:`RawOpportunity`.

        Returns *None* if neither a title nor a URL can be determined
        (the card is likely not a real opportunity).
        """
        evidence: dict[str, str] = {}

        # --- URL ----------------------------------------------------------
        raw_href = self._extract_href(card)
        card_url = self._normalize_url(raw_href) if raw_href else ""
        if not card_url:
            logger.debug("Skipping card — no URL found.")
            return None
        evidence["url"] = raw_href or ""

        # Get full card text as fallback / evidence
        try:
            card_inner_text = (card.inner_text() or "").strip()
        except Exception:
            card_inner_text = ""
        evidence["raw_card_text"] = card_inner_text

        # --- Title --------------------------------------------------------
        title = self._extract_text(card, self.selectors.get("title", ""))
        if title == "unknown" or not title:
            # Fallback: check h3, h2, or first line of card_inner_text
            for fallback_sel in ("h3", "h2", "h4", "[class*='title']", "a"):
                title = self._extract_text(card, fallback_sel)
                if title != "unknown" and title:
                    break
            if (title == "unknown" or not title) and card_inner_text:
                lines = [line.strip() for line in card_inner_text.splitlines() if line.strip()]
                if lines:
                    title = lines[0]

        evidence["title"] = title

        if (title == "unknown" or not title) and not card_url:
            return None

        # --- Remaining fields ---------------------------------------------
        description = self._extract_text(
            card, self.selectors.get("description", "")
        )
        if description == "unknown" and card_inner_text:
            lines = [line.strip() for line in card_inner_text.splitlines() if line.strip()]
            if len(lines) > 1 and lines[1] != title:
                description = lines[1]
        evidence["description"] = description

        deadline_raw = self._extract_text(
            card, self.selectors.get("deadline", "")
        )
        if deadline_raw == "unknown" and card_inner_text:
            for line in card_inner_text.splitlines():
                line_str = line.strip()
                if re.search(r"\b(left|deadline|ends? on|apply by)\b", line_str, re.IGNORECASE):
                    deadline_raw = line_str
                    break
        evidence["deadline"] = deadline_raw

        fee_raw = self._extract_text(card, self.selectors.get("fee", ""))
        if fee_raw == "unknown" and card_inner_text:
            if re.search(r"\b(free|₹\s*0|nil)\b", card_inner_text, re.IGNORECASE):
                fee_raw = "Free"
            elif match := re.search(r"(₹\s*\d[\d,]*)", card_inner_text):
                fee_raw = match.group(1)
        evidence["fee"] = fee_raw

        mode_raw = self._extract_text(card, self.selectors.get("mode", ""))
        evidence["mode_raw"] = mode_raw

        certificate_raw = self._extract_text(
            card, self.selectors.get("certificate", "")
        )
        if certificate_raw == "unknown" and card_inner_text:
            if re.search(r"\b(certificate|cert)\b", card_inner_text, re.IGNORECASE):
                certificate_raw = "Certificate provided"
        evidence["certificate"] = certificate_raw

        participants_raw = self._extract_text(
            card, self.selectors.get("participants", "")
        )
        if participants_raw == "unknown" and card_inner_text:
            for line in card_inner_text.splitlines():
                line_str = line.strip()
                if re.search(r"\b(members?|participants?|registered|teams?)\b", line_str, re.IGNORECASE):
                    participants_raw = line_str
                    break
        evidence["participants"] = participants_raw

        # --- Derived fields -----------------------------------------------
        full_text = " ".join([
            *(v for v in evidence.values() if v and v != "unknown"),
            card_inner_text,
        ])

        mode = self._detect_mode(mode_raw, full_text)
        category = self._detect_category(card_url, full_text)

        return RawOpportunity(
            title=title,
            url=card_url,
            description=description if description != "unknown" else "",
            deadline=deadline_raw,
            fee=fee_raw,
            mode=mode,
            category=category,
            certificate_info=certificate_raw,
            participants_count=participants_raw,
            source_evidence=evidence,
            source_url=source_url,
        )

    def _extract_text(self, element: ElementHandle, selector: str) -> str:
        """Safely extract inner text from a child matching *selector*.

        Parameters
        ----------
        element:
            Parent Playwright :class:`ElementHandle`.
        selector:
            CSS selector to locate within *element*.

        Returns
        -------
        str
            Stripped text content, or ``'unknown'`` when the selector is
            empty, nothing matches, or the text is blank.
        """
        if not selector:
            return "unknown"

        try:
            child = element.query_selector(selector)
            if child is None:
                return "unknown"
            raw_text: str | None = child.inner_text()
            if raw_text is None:
                return "unknown"
            text = raw_text.strip()
            return text if text else "unknown"
        except Exception:
            logger.debug(
                "Could not extract text with selector '%s'.",
                selector,
                exc_info=True,
            )
            return "unknown"

    def _extract_href(self, card: ElementHandle) -> str:
        """Return the first ``href`` attribute found within *card*.

        Checks the card itself first (if it is an ``<a>``), then looks
        for any child ``<a>`` element.
        """
        try:
            # If the card element itself is an <a>.
            href = card.get_attribute("href")
            if href:
                return href.strip()

            # Otherwise look for a child anchor.
            link = card.query_selector("a[href]")
            if link is not None:
                href = link.get_attribute("href")
                if href:
                    return href.strip()
        except Exception:
            logger.debug("Failed to extract href from card.", exc_info=True)
        return ""

    def _normalize_url(self, url: str) -> str:
        """Ensure *url* is an absolute URL rooted at Unstop.

        Relative paths such as ``/competitions/123`` are expanded to
        ``https://unstop.com/competitions/123``.
        """
        if not url:
            return ""
        url = url.strip()
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return urljoin(_UNSTOP_BASE, url)
        if url.startswith("http://") or url.startswith("https://"):
            return url
        # Bare relative path (e.g. "competitions/123").
        return urljoin(_UNSTOP_BASE + "/", url)

    def _detect_category(self, url: str, text: str) -> str:
        """Infer the opportunity category from its URL and visible text.

        Checks the URL path first (higher confidence), then falls back
        to token matching in the combined card text.

        Returns ``'unknown'`` if no category can be determined.
        """
        haystack = f"{url} {text}"
        for pattern, category in _CATEGORY_TOKENS:
            if pattern.search(haystack):
                return category
        return "unknown"

    @staticmethod
    def _detect_mode(mode_text: str, full_text: str) -> str:
        """Detect online/offline/hybrid from raw mode text or card text."""
        haystack = f"{mode_text} {full_text}"
        for pattern, mode in _MODE_TOKENS:
            if pattern.search(haystack):
                return mode
        return "unknown"

    def _handle_pagination(self, page: Page) -> bool:
        """Attempt to advance to the next page of results.

        Tries, in order:

        1. A *next_page* button/link (classic pagination).
        2. A *load_more* button (infinite-scroll style).

        Returns
        -------
        bool
            *True* if new content was (likely) loaded; *False* otherwise.
        """
        next_sel: str = self.selectors.get("next_page", "")
        load_more_sel: str = self.selectors.get("load_more", "")

        # --- Strategy 1: next page button --------------------------------
        if next_sel:
            try:
                btn = page.query_selector(next_sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    # Snapshot the current page URL to detect navigation.
                    old_url = page.url
                    btn.click()
                    # Wait for either a navigation or new network activity.
                    try:
                        page.wait_for_load_state(
                            "domcontentloaded", timeout=10_000
                        )
                    except PwTimeoutError:
                        pass
                    self._wait_for_content(page)
                    if page.url != old_url:
                        logger.debug(
                            "Navigated to next page: %s", page.url
                        )
                    return True
            except PwTimeoutError:
                logger.debug(
                    "Timeout clicking next-page button.", exc_info=True
                )
            except Exception:
                logger.debug(
                    "Error with next-page selector '%s'.",
                    next_sel,
                    exc_info=True,
                )

        # --- Strategy 2: load more button --------------------------------
        if load_more_sel:
            try:
                btn = page.query_selector(load_more_sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    # Count cards before click to detect new content.
                    primary_sel = self.selectors.get("opportunity_card", "")
                    alt_sel = self.selectors.get("opportunity_card_alt", "")
                    count_sel = primary_sel or alt_sel
                    old_count = (
                        len(page.query_selector_all(count_sel))
                        if count_sel
                        else 0
                    )

                    btn.scroll_into_view_if_needed()
                    btn.click()

                    # Give the page a moment to render new cards.
                    try:
                        page.wait_for_load_state(
                            "networkidle", timeout=10_000
                        )
                    except PwTimeoutError:
                        pass

                    if count_sel:
                        new_count = len(page.query_selector_all(count_sel))
                        if new_count > old_count:
                            logger.debug(
                                "Load-more added %d new cards.",
                                new_count - old_count,
                            )
                            return True
                    else:
                        # Cannot verify — assume success.
                        return True
            except PwTimeoutError:
                logger.debug(
                    "Timeout clicking load-more button.", exc_info=True
                )
            except Exception:
                logger.debug(
                    "Error with load-more selector '%s'.",
                    load_more_sel,
                    exc_info=True,
                )

        return False

    def _wait_for_content(self, page: Page) -> None:
        """Best-effort wait for opportunity cards to appear on *page*.

        Tries network-idle first, then falls back to a short sleep so
        that JS-rendered content has time to mount.
        """
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PwTimeoutError:
            logger.debug("Network-idle timeout — continuing anyway.")

        # Additionally wait for the card selector itself.
        primary_sel: str = self.selectors.get("opportunity_card", "")
        alt_sel: str = self.selectors.get("opportunity_card_alt", "")
        target_sel = primary_sel or alt_sel
        if target_sel:
            try:
                page.wait_for_selector(target_sel, timeout=8_000)
            except PwTimeoutError:
                logger.debug(
                    "Card selector '%s' not found within timeout.", target_sel
                )
