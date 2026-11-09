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
        "button:has-text('Continue')",
        "button:has-text('Proceed')",
        "button:has-text('Agree & Continue')",
        "button:has-text('I Agree')",
        "button:has-text('Start')",
        "button:has-text('Play')",
    ]

    ROUND_BUTTON_SELECTORS = [
        "a[href*='/assessment/']",
        "a[href*='/quiz/']",
        "a[href*='/round/']",
        "button:has-text('Take Assessment')",
        "a:has-text('Take Assessment')",
        "button:has-text('Start Assessment')",
        "a:has-text('Start Assessment')",
        "button:has-text('Play')",
        "a:has-text('Play')",
        "button:has-text('Enter Round')",
        "a:has-text('Enter Round')",
        "button:has-text('Take Test')",
        "a:has-text('Take Test')",
        "button:has-text('Attempt')",
        "a:has-text('Attempt')",
        "button:has-text('Enter')",
        "a:has-text('Enter')",
        "button:has-text('Join Round')",
        "button:has-text('Go to Assessment')",
        "a:has-text('Go to Assessment')",
        "button:has-text('View Assessment')",
        "a:has-text('View Assessment')",
    ]

    STAGES_TAB_SELECTORS = [
        "button:has-text('Stages & Timelines')",
        "a:has-text('Stages & Timelines')",
        "span:has-text('Stages & Timelines')",
        "li:has-text('Stages & Timelines')",
        "[id*='stages']",
        "button:has-text('Rounds')",
        "a:has-text('Rounds')",
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

    def find_and_enter_assessment(self, page: Page) -> Page:
        """Navigate through competition overview pages to locate and enter the live assessment.

        Handles clicking round tabs, round action buttons, and switching to popup tabs.
        Returns the active assessment Page object.
        """
        # If questions already visible, we are ready
        if self._has_visible_questions(page):
            return page

        initial_pages_count = len(self.browser_manager._context.pages) if self.browser_manager._context else 1

        # Check for direct round buttons
        clicked = False
        for selector in self.ROUND_BUTTON_SELECTORS:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    logger.info("Found round entry CTA: %s. Clicking...", selector)
                    el.scroll_into_view_if_needed()
                    el.click()
                    page.wait_for_timeout(2500)
                    clicked = True
                    break
            except Exception as exc:
                logger.debug("Round button click error '%s': %s", selector, exc)

        # If direct round button wasn't found, try clicking 'Stages & Timelines' tab
        if not clicked:
            for s_sel in self.STAGES_TAB_SELECTORS:
                try:
                    tab = page.query_selector(s_sel)
                    if tab and tab.is_visible():
                        logger.info("Clicking Stages & Timelines tab: %s", s_sel)
                        tab.click()
                        page.wait_for_timeout(1500)
                        for r_sel in self.ROUND_BUTTON_SELECTORS:
                            el = page.query_selector(r_sel)
                            if el and el.is_visible():
                                logger.info("Found round CTA under Stages tab: %s", r_sel)
                                el.scroll_into_view_if_needed()
                                el.click()
                                page.wait_for_timeout(2500)
                                clicked = True
                                break
                        if clicked:
                            break
                except Exception as exc:
                    logger.debug("Stages tab navigation error '%s': %s", s_sel, exc)

        # Check if assessment opened in a new browser tab / window
        if self.browser_manager._context and len(self.browser_manager._context.pages) > initial_pages_count:
            new_page = self.browser_manager._context.pages[-1]
            logger.info("Assessment loaded in new browser tab: %s", new_page.url)
            new_page.bring_to_front()
            page = new_page
            page.wait_for_timeout(2000)

        # Attempt to click start / instructions button
        self.click_start_assessment(page)
        return page

    def _has_visible_questions(self, page: Page) -> bool:
        """Check if any question choices/options are currently visible in DOM or frames."""
        opts = self._get_option_elements(page)
        return len(opts) >= 2

    def detect_page_status(self, page: Page) -> str:
        """Analyze page text to identify why assessment might not be active."""
        try:
            body_text = page.inner_text("body").lower()
        except Exception:
            return ""

        # Scheduled timing
        start_patterns = [
            r"(starts\s+(?:on|at|in)\s+[^.\n\r]+)",
            r"(live\s+(?:on|from|in)\s+[^.\n\r]+)",
            r"(round\s+starts\s+[^.\n\r]+)",
            r"(assessment\s+starts\s+[^.\n\r]+)",
            r"(starts\s+in\s+\d+\s+(?:days?|hours?|mins?))",
        ]
        for pattern in start_patterns:
            m = re.search(pattern, body_text)
            if m:
                clean_msg = re.sub(r"\s+", " ", m.group(1)).strip()
                return f"Scheduled: {clean_msg}"

        if "registration closed" in body_text:
            return "Registration is closed for this opportunity."
        if "register now" in body_text or "registration deadline" in body_text:
            reg_btn = page.query_selector("button:has-text('Register'), a:has-text('Register')")
            if reg_btn and reg_btn.is_visible():
                return "Registration required before entering this assessment."

        if "round has expired" in body_text or "assessment ended" in body_text:
            return "Assessment period has already ended."
        if "results declared" in body_text or "view results" in body_text:
            return "Assessment is concluded; results declared."

        return ""

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

    # ─── Visual Cursor & Screen Control ──────────────────────────────────────

    def inject_visual_cursor(self, page: Page) -> None:
        """Inject an animated, glowing neon AI cursor overlay into the page."""
        try:
            page.evaluate("""() => {
                if (document.getElementById('agentx-cursor')) return;
                
                const style = document.createElement('style');
                style.textContent = `
                    #agentx-cursor {
                        position: fixed;
                        width: 18px;
                        height: 18px;
                        border-radius: 50%;
                        background: radial-gradient(circle, #ff0055 35%, rgba(255, 0, 85, 0.4) 70%, transparent 100%);
                        box-shadow: 0 0 10px #ff0055, 0 0 20px rgba(255, 0, 85, 0.8);
                        pointer-events: none;
                        z-index: 2147483647;
                        transform: translate(-50%, -50%);
                        transition: left 0.12s ease-out, top 0.12s ease-out;
                    }
                    #agentx-ring {
                        position: fixed;
                        width: 36px;
                        height: 36px;
                        border-radius: 50%;
                        border: 2px solid #00f0ff;
                        box-shadow: 0 0 12px #00f0ff;
                        pointer-events: none;
                        z-index: 2147483646;
                        transform: translate(-50%, -50%);
                        transition: left 0.18s ease-out, top 0.18s ease-out, transform 0.15s ease;
                    }
                    #agentx-label {
                        position: fixed;
                        background: rgba(10, 10, 18, 0.9);
                        color: #00f0ff;
                        font-family: monospace;
                        font-size: 11px;
                        font-weight: bold;
                        padding: 2px 7px;
                        border-radius: 4px;
                        border: 1px solid #00f0ff;
                        box-shadow: 0 0 8px rgba(0, 240, 255, 0.4);
                        pointer-events: none;
                        z-index: 2147483647;
                        transform: translate(16px, -16px);
                        transition: left 0.18s ease-out, top 0.18s ease-out;
                        letter-spacing: 0.5px;
                    }
                `;
                document.head.appendChild(style);

                const cursor = document.createElement('div');
                cursor.id = 'agentx-cursor';
                const ring = document.createElement('div');
                ring.id = 'agentx-ring';
                const label = document.createElement('div');
                label.id = 'agentx-label';
                label.textContent = 'AgentX AI';

                document.body.appendChild(cursor);
                document.body.appendChild(ring);
                document.body.appendChild(label);
            }""")
        except Exception as exc:
            logger.debug("Visual cursor injection error (non-fatal): %s", exc)

    def cursor_move_to(self, page: Page, element: ElementHandle, steps: int = 15) -> tuple[float, float]:
        """Visibly and smoothly move the AI cursor to an element's center."""
        self.inject_visual_cursor(page)
        try:
            element.scroll_into_view_if_needed()
            box = element.bounding_box()
            if not box:
                return 0.0, 0.0
            x = box["x"] + box["width"] / 2.0
            y = box["y"] + box["height"] / 2.0

            # Move visual cursor elements on screen
            page.evaluate("""({x, y}) => {
                const c = document.getElementById('agentx-cursor');
                const r = document.getElementById('agentx-ring');
                const l = document.getElementById('agentx-label');
                if (c) { c.style.left = x + 'px'; c.style.top = y + 'px'; }
                if (r) { r.style.left = x + 'px'; r.style.top = y + 'px'; }
                if (l) { l.style.left = x + 'px'; l.style.top = y + 'px'; }
            }""", {"x": x, "y": y})

            # Physical Playwright mouse movement
            page.mouse.move(x, y, steps=steps)
            page.wait_for_timeout(100)
            return x, y
        except Exception as exc:
            logger.debug("Cursor move error: %s", exc)
            return 0.0, 0.0

    def cursor_click(self, page: Page, element: ElementHandle) -> bool:
        """Move cursor to element and perform an animated physical mouse click."""
        try:
            x, y = self.cursor_move_to(page, element, steps=12)
            if x > 0 and y > 0:
                # Click pulse animation
                page.evaluate("""() => {
                    const r = document.getElementById('agentx-ring');
                    if (r) {
                        r.style.transform = 'translate(-50%, -50%) scale(1.6)';
                        r.style.borderColor = '#ff0055';
                        setTimeout(() => {
                            r.style.transform = 'translate(-50%, -50%) scale(1.0)';
                            r.style.borderColor = '#00f0ff';
                        }, 220);
                    }
                }""")
                page.mouse.click(x, y)
                page.wait_for_timeout(350)
                return True
            else:
                element.click()
                return True
        except Exception as exc:
            logger.debug("Cursor click fallback: %s", exc)
            try:
                element.click()
                return True
            except Exception:
                return False

    def cursor_type(self, page: Page, element: ElementHandle, text: str) -> None:
        """Move cursor to input field, click, and type text character-by-character."""
        try:
            self.cursor_click(page, element)
            element.fill("")
            page.wait_for_timeout(150)
            element.type(text, delay=30)
            page.wait_for_timeout(200)
        except Exception as exc:
            logger.debug("Cursor type error: %s", exc)
            try:
                element.fill(text)
            except Exception:
                pass

    def auto_register_if_needed(self, page: Page) -> bool:
        """Autonomously fill registration details and submit without requiring user approvals."""
        is_register_page = "/register" in page.url or "/apply" in page.url
        reg_btn = page.query_selector("button:has-text('Register'), a:has-text('Register'), button:has-text('Apply Now'), a:has-text('Apply Now')")

        already_reg = page.query_selector("button:has-text('Registered'), button:has-text('View Registration')")
        if already_reg and already_reg.is_visible():
            logger.info("Candidate is already registered for this competition.")
            return True

        if not (is_register_page or (reg_btn and reg_btn.is_visible())):
            return False

        logger.info("Autonomous Registration required. Taking screen control...")
        self.inject_visual_cursor(page)

        # Click Register CTA if on landing page
        if reg_btn and reg_btn.is_visible() and not is_register_page:
            self.cursor_click(page, reg_btn)
            page.wait_for_timeout(2500)

        # Get candidate profile data
        from browser.registration_copilot import RegistrationCopilot
        copilot = RegistrationCopilot(browser_manager=self.browser_manager)
        candidate = copilot.get_candidate_profile()

        # Fill visible text inputs
        field_mappings = [
            (["name", "full_name", "first_name"], candidate.get("name", "Rajvardhan Patil")),
            (["college", "university", "institute", "organization"], candidate.get("college", "National Institute of Technology")),
            (["degree", "qualification", "course"], candidate.get("degree", "B.Tech")),
            (["year", "graduation", "batch"], candidate.get("graduation_year", "2027")),
            (["phone", "mobile", "contact"], candidate.get("phone", "")),
            (["city", "location"], candidate.get("city", "Mumbai")),
        ]

        inputs = page.query_selector_all("input[type='text'], input[type='email'], input[type='tel'], input:not([type])")
        for inp in inputs:
            try:
                if not inp.is_visible() or not inp.is_enabled():
                    continue
                val = inp.input_value()
                if val and len(val) > 1:
                    continue

                html_tag = (inp.get_attribute("name") or "") + " " + (inp.get_attribute("placeholder") or "") + " " + (inp.get_attribute("id") or "")
                html_tag = html_tag.lower()

                for keywords, value in field_mappings:
                    if value and any(kw in html_tag for kw in keywords):
                        logger.info("Autonomous typing into '%s' -> '%s'", html_tag[:25], value)
                        self.cursor_type(page, inp, value)
                        break
            except Exception as exc:
                logger.debug("Input auto-fill error: %s", exc)

        # Accept all declaration / terms checkboxes
        checkboxes = page.query_selector_all("input[type='checkbox']")
        for cb in checkboxes:
            try:
                if cb.is_visible() and not cb.is_checked():
                    logger.info("Autonomous check on declaration checkbox...")
                    self.cursor_click(page, cb)
            except Exception as exc:
                logger.debug("Checkbox check error: %s", exc)

        # Select individual / student radio if unselected
        radios = page.query_selector_all("input[type='radio']")
        for r in radios:
            try:
                if r.is_visible() and not r.is_checked():
                    r_text = (r.get_attribute("value") or "") + " " + (r.get_attribute("id") or "")
                    if any(w in r_text.lower() for w in ["student", "individual", "solo", "college"]):
                        self.cursor_click(page, r)
            except Exception:
                pass

        # Submit registration form
        submit_selectors = [
            "button:has-text('Complete Registration')",
            "button:has-text('Submit Registration')",
            "button:has-text('Confirm Registration')",
            "button:has-text('Register Now')",
            "button:has-text('Submit')",
            "button:has-text('Next')",
        ]
        for sel in submit_selectors:
            sub_btn = page.query_selector(sel)
            if sub_btn and sub_btn.is_visible() and sub_btn.is_enabled():
                logger.info("Autonomous registration submit: %s", sel)
                self.cursor_click(page, sub_btn)
                page.wait_for_timeout(3000)
                break

        return True

    def select_option(self, page: Page, option_index: int) -> bool:
        """Click and select the chosen option index on the active question using smooth cursor movement."""
        options_elements = self._get_option_elements(page)
        if not options_elements or option_index >= len(options_elements):
            logger.warning("Option index %d out of bounds (found %d options)", option_index, len(options_elements))
            return False

        target_el = options_elements[option_index]
        logger.info("Autonomous cursor selecting option index %d...", option_index)
        return self.cursor_click(page, target_el)

    def click_next(self, page: Page) -> bool:
        """Click Next / Save & Next button to advance using smooth cursor movement."""
        for selector in self.NEXT_BUTTON_SELECTORS:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and btn.is_enabled():
                    logger.info("Autonomous cursor clicking next: %s", selector)
                    return self.cursor_click(page, btn)
            except Exception as exc:
                logger.debug("Next button click failed for '%s': %s", selector, exc)

        logger.warning("No enabled Next button found.")
        return False

    def click_submit_quiz(self, page: Page) -> bool:
        """Trigger final quiz submission using smooth cursor movement and handle confirmation modals."""
        logger.info("Autonomous cursor submitting assessment...")
        for selector in self.SUBMIT_BUTTON_SELECTORS:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible() and btn.is_enabled():
                    self.cursor_click(page, btn)
                    page.wait_for_timeout(1500)

                    # Confirmation dialogs
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
                            logger.info("Confirming submission via modal: %s", c_sel)
                            self.cursor_click(page, confirm_btn)
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
        """Extract clean text for the active question across main page and child frames."""
        scopes = [page]
        for f in getattr(page, "frames", []):
            if f != page.main_frame:
                scopes.append(f)

        for scope in scopes:
            for selector in self.QUESTION_CONTAINER_SELECTORS:
                try:
                    el = scope.query_selector(selector)
                    if el and el.is_visible():
                        txt = el.inner_text().strip()
                        if txt and len(txt) > 5:
                            return txt
                except Exception:
                    pass

            try:
                card = scope.query_selector(".mat-card, .assessment-body, main")
                if card and card.is_visible():
                    return card.inner_text().strip()[:500]
            except Exception:
                pass

        return "Unknown Question"

    def _extract_options(self, page: Page) -> list[str]:
        """Extract list of option text strings."""
        elements = self._get_option_elements(page)
        options = []
        for el in elements:
            try:
                raw_text = el.inner_text().strip()
                # Clean leading prefixes like 'A.', 'B)', '1.', '(A)'
                clean_text = re.sub(r"^[A-Da-d0-9][\.\)\:\-]\s*", "", raw_text).strip()
                if clean_text:
                    options.append(clean_text)
            except Exception:
                pass

        return options

    def _get_option_elements(self, page: Page) -> list[ElementHandle]:
        """Retrieve list of option DOM element handles across page and child frames."""
        scopes = [page]
        for f in getattr(page, "frames", []):
            if f != page.main_frame:
                scopes.append(f)

        for scope in scopes:
            for selector in self.OPTION_ITEM_SELECTORS:
                try:
                    elements = scope.query_selector_all(selector)
                    visible_elements = [el for el in elements if el.is_visible()]
                    if len(visible_elements) >= 2:
                        return visible_elements
                except Exception:
                    pass

            # Fallback to radio inputs
            try:
                radios = scope.query_selector_all("input[type='radio']")
                visible_radios = [r for r in radios if r.is_visible()]
                if len(visible_radios) >= 2:
                    return visible_radios
            except Exception:
                pass

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
