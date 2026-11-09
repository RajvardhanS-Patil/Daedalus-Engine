"""
Unstop Autopilot — Real-Time Browser Quiz Driver

Automates the live Chromium browser window during an Unstop quiz assessment.
Responsible for:
- Detecting proctoring constraints (webcam/screen-share) and aborting if found
- Scraping active questions, code blocks, option choices, and live timers
- Selecting answer options via clicks and advancing through questions
- Submitting the completed assessment
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import ElementHandle, Page, TimeoutError as PwTimeoutError

from browser.browser_manager import BrowserManager
from config import settings

logger = logging.getLogger(__name__)


@dataclass
class ScrapedQuestion:
    """Represents a live question parsed directly from the DOM."""

    question_number: int = 1
    total_questions: int = 1
    question_text: str = ""
    options: list[str] = field(default_factory=list)
    has_code_block: bool = False
    code_content: str = ""
    time_remaining_seconds: Optional[int] = None
    raw_timer_text: str = ""
    is_last_question: bool = False


class QuizDriver:
    """Drives the live browser through Unstop's assessment and quiz interface."""

    PROCTORING_INDICATORS = [
        "proctor",
        "webcam",
        "camera",
        "screen-share",
        "microphone",
        "audio-monitor",
        "face-detection",
        "tab-switch-warning",
    ]

    QUESTION_CONTAINER_SELECTORS = [
        ".question-container",
        ".question-text",
        ".ques-text",
        "[class*='question-content']",
        "[class*='questionText']",
        ".assessment-question",
        ".mat-card-title",
        ".test-question",
        "div[class*='Question_question']",
    ]

    OPTION_CONTAINER_SELECTORS = [
        ".options-list",
        ".option-container",
        "[class*='options']",
        "[class*='Options_options']",
        ".mat-radio-group",
        "ul.options",
    ]

    OPTION_ITEM_SELECTORS = [
        ".option-item",
        ".option-label",
        ".mat-radio-button",
        "label.option",
        "[class*='option-wrapper']",
        "[class*='answer-option']",
        "[class*='Option_option']",
        "li.option",
        "div[role='radio']",
        "label[for*='option']",
    ]

    NEXT_BUTTON_SELECTORS = [
        "button:has-text('Save & Next')",
        "button:has-text('Next Question')",
        "button:has-text('Save and Next')",
        "button:has-text('Next')",
        "button:has-text('Mark & Next')",
        "button[aria-label='Next']",
        "button:has-text('Skip & Next')",
    ]

    SUBMIT_BUTTON_SELECTORS = [
        "button:has-text('Submit Assessment')",
        "button:has-text('Submit Test')",
        "button:has-text('Submit Quiz')",
        "button:has-text('Finish Test')",
        "button:has-text('End Test')",
        "button:has-text('Submit')",
    ]

    START_BUTTON_SELECTORS = [
        "button:has-text('Start Assessment')",
        "button:has-text('Start Quiz')",
        "button:has-text('Begin Test')",
        "button:has-text('Start Test')",
        "button:has-text('Proceed to Test')",
        "button:has-text('Take Test')",
        "button:has-text('Start Now')",
        "a:has-text('Start Assessment')",
    ]

    TIMER_SELECTORS = [
        ".timer",
        ".countdown",
        "[class*='timer']",
        "[class*='time-left']",
        "[id*='timer']",
        "span[class*='countdown']",
        "div[class*='Timer_timer']",
    ]

    def __init__(self, browser_manager: Optional[BrowserManager] = None) -> None:
        self.browser_manager = browser_manager or BrowserManager()

    def navigate_to_quiz(self, url: str, page: Page) -> bool:
        """Navigate to the target quiz URL and wait for DOM stabilization."""
        logger.info("Navigating to quiz assessment URL: %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            return True
        except Exception as exc:
            logger.error("Failed to navigate to quiz URL: %s", exc)
            return False

    def detect_proctoring(self, page: Page) -> tuple[bool, str]:
        """Detect if the assessment requires webcam, mic, or proctoring software.

        Returns:
            (is_proctored: bool, reason: str)
        """
        page_content = page.content().lower()

        # Check explicit text indicators
        explicit_phrases = [
            ("webcam is required", "Webcam access required"),
            ("camera permissions", "Camera permissions required"),
            ("screen sharing is mandatory", "Screen sharing enforced"),
            ("proctored assessment", "Explicit proctored assessment"),
            ("face detection active", "Facial recognition active"),
            ("anti-cheat enabled", "Anti-cheat proctoring active"),
        ]

        for phrase, reason in explicit_phrases:
            if phrase in page_content:
                logger.warning("Proctoring detected on page: %s", reason)
                return True, reason

        # Check DOM element attributes
        for indicator in self.PROCTORING_INDICATORS:
            el = page.query_selector(f"[class*='{indicator}'], [id*='{indicator}']")
            if el and el.is_visible():
                reason = f"Visible proctoring element found ({indicator})"
                logger.warning(reason)
                return True, reason

        return False, ""

    def click_start_assessment(self, page: Page) -> bool:
        """Find and trigger the initial Start / Begin Assessment CTA."""
        page.wait_for_timeout(1000)

        # First, accept any declaration / terms checkbox if present
        try:
            checkboxes = page.query_selector_all("input[type='checkbox']")
            for cb in checkboxes:
                if cb.is_visible() and not cb.is_checked():
                    logger.info("Checking terms / instructions checkbox...")
                    cb.check()
                    page.wait_for_timeout(500)
        except Exception as exc:
            logger.debug("Terms checkbox check failed (non-fatal): %s", exc)

        for selector in self.START_BUTTON_SELECTORS:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and btn.is_enabled():
                    logger.info("Found start button: %s. Clicking...", selector)
                    btn.click()
                    page.wait_for_timeout(2000)
                    return True
            except Exception as exc:
                logger.debug("Error trying start button '%s': %s", selector, exc)

        logger.info("No explicit start button found; assessment may already be in progress.")
        return False

    def scrape_current_question(self, page: Page) -> ScrapedQuestion:
        """Scrape the currently active question, code block, options, and timer."""
        sq = ScrapedQuestion()

        # 1. Timer parsing
        timer_text = self._extract_timer_text(page)
        sq.raw_timer_text = timer_text
        sq.time_remaining_seconds = self._parse_timer_seconds(timer_text)

        # 2. Question number & counter
        sq.question_number, sq.total_questions = self._extract_question_counter(page)

        # 3. Code block extraction
        code_el = page.query_selector("pre code, pre, .code-snippet, [class*='code-block']")
        if code_el and code_el.is_visible():
            code_text = code_el.inner_text().strip()
            if code_text:
                sq.has_code_block = True
                sq.code_content = code_text

        # 4. Question text
        sq.question_text = self._extract_question_text(page)
        if sq.has_code_block and sq.code_content not in sq.question_text:
            sq.question_text += f"\n\n```\n{sq.code_content}\n```"

        # 5. Options extraction
        sq.options = self._extract_options(page)

        # 6. Check if last question
        sq.is_last_question = (
            sq.question_number >= sq.total_questions and sq.total_questions > 1
        ) or bool(page.query_selector("button:has-text('Finish Test'), button:has-text('Submit Test')"))

        return sq

    def select_option(self, page: Page, option_index: int) -> bool:
        """Click and select the chosen option index on the active question."""
        options_elements = self._get_option_elements(page)
        if not options_elements or option_index >= len(options_elements):
            logger.warning("Option index %d out of bounds (found %d options)", option_index, len(options_elements))
            return False

        target_el = options_elements[option_index]
        try:
            target_el.scroll_into_view_if_needed()
            page.wait_for_timeout(200)

            # Try clicking the inner input/radio if available
            inner_radio = target_el.query_selector("input[type='radio'], input[type='checkbox']")
            if inner_radio and inner_radio.is_visible():
                inner_radio.click()
            else:
                target_el.click()

            page.wait_for_timeout(300)
            logger.info("Successfully selected option index %d", option_index)
            return True
        except Exception as exc:
            logger.error("Failed to click option index %d: %s", option_index, exc)
            return False

    def click_next(self, page: Page) -> bool:
        """Click Next / Save & Next button to advance to subsequent question."""
        for selector in self.NEXT_BUTTON_SELECTORS:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and btn.is_enabled():
                    logger.info("Clicking next button: %s", selector)
                    btn.click()
                    page.wait_for_timeout(1000)
                    return True
            except Exception as exc:
                logger.debug("Next button click failed for '%s': %s", selector, exc)

        logger.warning("No enabled Next button found.")
        return False

    def click_submit_quiz(self, page: Page) -> bool:
        """Trigger final quiz submission and handle confirmation dialogs."""
        logger.info("Attempting final quiz submission...")
        for selector in self.SUBMIT_BUTTON_SELECTORS:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    page.wait_for_timeout(1500)

                    # Check for confirmation modal (e.g. 'Yes, Submit', 'Confirm')
                    confirm_selectors = [
                        "button:has-text('Yes, Submit')",
                        "button:has-text('Yes')",
                        "button:has-text('Confirm')",
                        "button:has-text('Submit Test')",
                        ".modal button:has-text('Submit')",
                        ".dialog button:has-text('Submit')",
                    ]
                    for c_sel in confirm_selectors:
                        confirm_btn = page.query_selector(c_sel)
                        if confirm_btn and confirm_btn.is_visible():
                            logger.info("Confirming submission via modal button: %s", c_sel)
                            confirm_btn.click()
                            page.wait_for_timeout(2000)
                            break

                    return True
            except Exception as exc:
                logger.debug("Submit button error '%s': %s", selector, exc)

        return False

    def is_quiz_completed(self, page: Page) -> bool:
        """Check if the assessment has finalized and reached the end/score screen."""
        page_content = page.content().lower()
        completion_markers = [
            "assessment submitted",
            "test submitted successfully",
            "quiz completed",
            "thank you for taking the test",
            "your responses have been recorded",
            "view results",
            "score card",
        ]
        return any(marker in page_content for marker in completion_markers)

    # ─── Internal Extraction Helpers ──────────────────────────────────────────

    def _extract_question_text(self, page: Page) -> str:
        """Extract clean text for the active question."""
        for selector in self.QUESTION_CONTAINER_SELECTORS:
            el = page.query_selector(selector)
            if el and el.is_visible():
                txt = el.inner_text().strip()
                if txt and len(txt) > 5:
                    return txt

        # Fallback: search main card or body
        card = page.query_selector(".mat-card, .assessment-body, main")
        if card:
            return card.inner_text().strip()[:500]

        return "Unknown Question"

    def _extract_options(self, page: Page) -> list[str]:
        """Extract list of option text strings."""
        elements = self._get_option_elements(page)
        options = []
        for el in elements:
            raw_text = el.inner_text().strip()
            # Clean leading prefixes like 'A.', 'B)', '1.', '(A)'
            clean_text = re.sub(r"^[A-Da-d0-9][\.\)\:\-]\s*", "", raw_text).strip()
            if clean_text:
                options.append(clean_text)

        return options

    def _get_option_elements(self, page: Page) -> list[ElementHandle]:
        """Retrieve list of option DOM element handles."""
        for selector in self.OPTION_ITEM_SELECTORS:
            elements = page.query_selector_all(selector)
            visible_elements = [el for el in elements if el.is_visible()]
            if len(visible_elements) >= 2:
                return visible_elements

        # Fallback to radio inputs
        radios = page.query_selector_all("input[type='radio']")
        visible_radios = [r for r in radios if r.is_visible()]
        if len(visible_radios) >= 2:
            return visible_radios

        return []

    def _extract_timer_text(self, page: Page) -> str:
        """Extract raw string representation of time remaining."""
        for selector in self.TIMER_SELECTORS:
            el = page.query_selector(selector)
            if el and el.is_visible():
                text = el.inner_text().strip()
                if text:
                    return text
        return ""

    def _parse_timer_seconds(self, timer_str: str) -> Optional[int]:
        """Convert a timer string (e.g. '12:45' or '01:15:30') into total seconds."""
        if not timer_str:
            return None

        # Look for HH:MM:SS or MM:SS pattern
        match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", timer_str)
        if match:
            part1 = int(match.group(1))
            part2 = int(match.group(2))
            part3 = match.group(3)
            if part3 is not None:
                # HH:MM:SS
                return part1 * 3600 + part2 * 60 + int(part3)
            # MM:SS
            return part1 * 60 + part2

        # Check for 'X mins' / 'X min'
        min_match = re.search(r"(\d+)\s*(?:min|minute)", timer_str, re.IGNORECASE)
        if min_match:
            return int(min_match.group(1)) * 60

        return None

    def _extract_question_counter(self, page: Page) -> tuple[int, int]:
        """Extract (current_question_number, total_questions) from DOM text."""
        counter_selectors = [
            ".question-count",
            ".q-counter",
            "[class*='counter']",
            "[class*='question-number']",
            "div:has-text('Question')",
        ]

        for sel in counter_selectors:
            el = page.query_selector(sel)
            if el and el.is_visible():
                text = el.inner_text().strip()
                match = re.search(r"(\d+)\s*(?:of|/)\s*(\d+)", text, re.IGNORECASE)
                if match:
                    return int(match.group(1)), int(match.group(2))

        return 1, 1
