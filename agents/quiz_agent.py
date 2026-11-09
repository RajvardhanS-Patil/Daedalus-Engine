"""
Unstop Autopilot — Autonomous Live Quiz Agent

Coordinates real-time browser control, screen observation, question evaluation
via Qwen 2.5 7B, option selection, human-like timing, and final submission.
Maintains full database persistence and audit trails.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

from browser.browser_manager import BrowserManager
from browser.quiz_driver import QuizDriver, ScrapedQuestion
from config import settings
from database.connection import get_session
from database.models import ActivityLog, LiveQuizAttempt, Opportunity, QuizQuestion, QuizSession
from quiz.solver import LiveAnswerDecision, QuizSolver

logger = logging.getLogger(__name__)


@dataclass
class QuizRunResult:
    """Outcome of an autonomous live quiz execution."""

    status: str = "completed"  # completed, aborted_proctoring, login_required, error
    opportunity_url: str = ""
    session_id: Optional[int] = None
    total_questions_encountered: int = 0
    answers_submitted: int = 0
    average_confidence: float = 0.0
    duration_seconds: float = 0.0
    error_message: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)


class LiveQuizAgent:
    """Autonomous agent executing live Unstop assessments without manual intervention."""

    def __init__(
        self,
        solver: Optional[QuizSolver] = None,
        driver: Optional[QuizDriver] = None,
        browser_manager: Optional[BrowserManager] = None,
    ) -> None:
        self.browser_manager = browser_manager or BrowserManager()
        self.solver = solver or QuizSolver()
        self.driver = driver or QuizDriver(browser_manager=self.browser_manager)

    def run_quiz_by_id(
        self,
        opportunity_id: int,
        headless: bool = False,
        update_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> QuizRunResult:
        """Execute a live quiz for an opportunity identified by its local database ID."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                return QuizRunResult(
                    status="error",
                    error_message=f"Opportunity #{opportunity_id} not found in database.",
                )
            target_url = opp.url

        return self.run_quiz(
            opportunity_url=target_url,
            headless=headless,
            update_callback=update_callback,
        )

    def run_agentx(
        self,
        opportunity_url: str,
        headless: bool = False,
        update_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> QuizRunResult:
        """Universal AgentX Autonomous Screen Control entry point.

        Maintains persistent browser control on the user's desktop:
        - Auto-registers for competitions (zero approvals)
        - Detects stages & enters active assessments
        - Solves questions via Qwen 2.5 7B & submits
        - Keeps browser open on desktop with continuous screen monitoring & co-pilot prompt
        """
        return self.run_quiz(
            opportunity_url=opportunity_url,
            headless=headless,
            keep_alive=True,
            update_callback=update_callback,
        )

    def run_quiz(
        self,
        opportunity_url: str,
        headless: bool = False,
        max_questions: int = 100,
        keep_alive: bool = False,
        update_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> QuizRunResult:
        """Run the end-to-end autonomous quiz-solving workflow.

        Args:
            opportunity_url: Direct URL to the Unstop assessment or competition.
            headless: Whether to run Chromium headlessly (default: False for live screen control).
            max_questions: Safety upper bound on questions to prevent infinite loops.
            keep_alive: Whether to keep browser open and enter persistent interactive co-pilot mode.
            update_callback: Optional hook for real-time progress events.

        Returns:
            QuizRunResult summarizing the test run.
        """
        start_time = time.time()
        result = QuizRunResult(opportunity_url=opportunity_url)

        self._notify(update_callback, "status", "Launching browser...")
        try:
            self.browser_manager.launch(headless=headless)
            page = self.browser_manager.new_page()

            # 1. Verify Authentication
            self._notify(update_callback, "status", "Verifying Unstop login session...")
            if not self.browser_manager.check_login_status(page):
                logger.warning("User is not logged into Unstop.")
                result.status = "login_required"
                result.error_message = (
                    "No active Unstop login session found. "
                    "Run 'agentx --login' to authenticate your account first."
                )
                self._notify(update_callback, "error", result.error_message)
                return result

            # 2. Navigate to Assessment or Competition
            self._notify(update_callback, "status", f"Navigating to {opportunity_url}...")
            if not self.driver.navigate_to_quiz(opportunity_url, page):
                result.status = "error"
                result.error_message = "Failed to load quiz assessment page."
                return result

            # 3. Inject glowing visual AI cursor
            self.driver.inject_visual_cursor(page)

            # 4. Check & Perform Autonomous Registration if needed (Zero Approvals)
            self._notify(update_callback, "status", "Checking competition registration status...")
            reg_done = self.driver.auto_register_if_needed(page)
            if reg_done:
                self._notify(update_callback, "status", "Registration verified/completed autonomously.")

            # 5. Enter Assessment (handle competition pages, tabs, stages)
            self._notify(update_callback, "status", "Locating assessment round and start CTA...")
            page = self.driver.find_and_enter_assessment(page)
            self.driver.inject_visual_cursor(page)

            # 6. Proctoring Safety Check
            self._notify(update_callback, "status", "Checking for proctoring restrictions...")
            is_proctored, proctor_reason = self.driver.detect_proctoring(page)
            if is_proctored and settings.quiz_automation.abort_on_proctoring:
                logger.warning("Proctored quiz detected: %s. Aborting for account safety.", proctor_reason)
                result.status = "aborted_proctoring"
                result.error_message = (
                    f"Proctoring restriction detected ({proctor_reason}). "
                    "AgentX aborted to protect your account integrity."
                )
                self._notify(update_callback, "proctor_abort", result.error_message)
                return result

            # 7. Live Screen Observation Loop (wait for quiz questions to be active)
            page = self._wait_for_live_questions(
                page=page,
                timeout_seconds=30 if not keep_alive else 8,
                update_callback=update_callback,
            )

            # Check if questions were found
            if not self.driver._has_visible_questions(page):
                overview = self.driver.detect_competition_overview(page)
                page_status = overview.get("status_message") or self.driver.detect_page_status(page)
                if page_status:
                    result.status = "not_active"
                    result.error_message = f"Assessment is not live right now. {page_status}"
                    self._notify(update_callback, "status", f"Notice: {page_status}")
                else:
                    result.status = "no_questions_found"
                    result.error_message = (
                        "No active quiz questions detected on screen. "
                        "The assessment may not be open yet or requires manual registration."
                    )
                self._notify(update_callback, "status" if keep_alive else "error", result.error_message)

                if keep_alive and not headless:
                    self._persistent_copilot_loop(page, update_callback)
                    result.status = "active_session"
                    return result

                return result

            # 8. Initialize Database Session
            session_id = self._create_db_session(opportunity_url)
            result.session_id = session_id
            self._notify(update_callback, "status", "Questions detected! Beginning autonomous test solving...")

            # 9. Autonomous Solving Loop
            attempts = self._solve_assessment_loop(
                page=page,
                session_id=session_id,
                max_questions=max_questions,
                update_callback=update_callback,
            )

            # 10. Finalize Results
            duration = time.time() - start_time
            confidences = [a.get("confidence", 0.0) for a in attempts]
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

            result.total_questions_encountered = len(attempts)
            result.answers_submitted = len(attempts)
            result.average_confidence = round(avg_conf, 2)
            result.duration_seconds = round(duration, 1)
            result.attempts = attempts
            result.status = "completed"

            self._finalize_db_session(session_id, len(attempts), duration)

            self._notify(update_callback, "completed", {
                "total_answered": len(attempts),
                "duration": result.duration_seconds,
                "avg_confidence": result.average_confidence,
            })

            if keep_alive and not headless:
                self._persistent_copilot_loop(page, update_callback)

            return result

        except Exception as exc:
            logger.critical("Fatal error during live quiz execution: %s", exc, exc_info=True)
            result.status = "error"
            result.error_message = str(exc)
            result.duration_seconds = round(time.time() - start_time, 1)
            self._notify(update_callback, "error", str(exc))
            return result

        finally:
            if not headless and not keep_alive:
                time.sleep(4)
                self.browser_manager.close()
            elif headless:
                self.browser_manager.close()

    def _solve_assessment_loop(
        self,
        page: Any,
        session_id: int,
        max_questions: int = 100,
        update_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> list[dict[str, Any]]:
        """Core loop scraping and answering active test questions on screen."""
        seen_questions: set[str] = set()
        attempts: list[dict[str, Any]] = []

        for q_idx in range(1, max_questions + 1):
            page.wait_for_timeout(1000)

            # Keep page reference updated to the active tab
            page = self._get_active_quiz_page(page)

            # Check if test completed
            if self.driver.is_quiz_completed(page):
                logger.info("Quiz completion detected on page.")
                break

            # Scrape current active question
            scraped = self.driver.scrape_current_question(page)

            # Termination check: empty options or repeated question with no progress
            if not scraped.options:
                page.wait_for_timeout(2500)
                scraped = self.driver.scrape_current_question(page)
                if not scraped.options:
                    if self.driver.is_quiz_completed(page):
                        logger.info("Quiz reached completion screen.")
                    else:
                        logger.warning("Options remain unavailable. Finishing assessment loop.")
                    break

            q_key = scraped.question_text[:80].strip()
            if q_key in seen_questions:
                logger.warning("Encountered repeated question '%s'. Attempting to click Next...", q_key)
                if not self.driver.click_next(page):
                    break
                continue
            seen_questions.add(q_key)

            # Notify UI of question arrival
            self._notify(
                update_callback,
                "question_scraped",
                {
                    "number": scraped.question_number,
                    "total": scraped.total_questions,
                    "text": scraped.question_text,
                    "options": scraped.options,
                    "timer": scraped.raw_timer_text,
                },
            )

            # Solve question via Qwen 2.5 7B
            self._notify(update_callback, "status", f"Evaluating question #{scraped.question_number} with Qwen 2.5 7B...")
            decision = self.solver.solve_with_fallback(
                question=scraped.question_text,
                options=scraped.options,
            )

            # Notify UI of decision
            self._notify(
                update_callback,
                "answer_decided",
                {
                    "number": scraped.question_number,
                    "chosen_index": decision.chosen_index,
                    "chosen_option": decision.chosen_option,
                    "confidence": decision.confidence,
                    "rationale": decision.rationale,
                },
            )

            # Click selected option on screen using visual AI cursor
            self._notify(update_callback, "status", f"Selecting option [{decision.chosen_index}] on screen...")
            self.driver.select_option(page, decision.chosen_index)

            # Human-like delay
            self._human_delay()

            # Record attempt in database
            self._record_attempt(
                session_id=session_id,
                q_num=scraped.question_number,
                q_text=scraped.question_text,
                options=scraped.options,
                decision=decision,
                timer_secs=scraped.time_remaining_seconds,
            )

            attempts.append({
                "question_number": scraped.question_number,
                "question_text": scraped.question_text,
                "chosen_option": decision.chosen_option,
                "chosen_index": decision.chosen_index,
                "confidence": decision.confidence,
                "rationale": decision.rationale,
            })

            # Advance or Submit
            if scraped.is_last_question or q_idx >= scraped.total_questions:
                self._notify(update_callback, "status", "Final question reached. Submitting assessment...")
                self.driver.click_submit_quiz(page)
                page.wait_for_timeout(2000)
                break
            else:
                self._notify(update_callback, "status", "Moving to next question...")
                self.driver.click_next(page)

        return attempts

    def _persistent_copilot_loop(
        self,
        page: Any,
        update_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        """Hold browser open on screen with continuous live monitoring & user control prompt."""
        self._notify(
            update_callback,
            "status",
            "AgentX is keeping the browser open on your desktop. Full autonomous screen control active.",
        )
        print("\n" + "=" * 70)
        print("  AGENTX PERSISTENT SCREEN CONTROL ACTIVE")
        print("  • Browser is open on your desktop with glowing AI visual cursor.")
        print("  • If you navigate or open an assessment, AgentX will solve it autonomously.")
        print("  • Commands:")
        print("      [Enter]      : Re-scan active screen & check for live questions/rounds")
        print("      u <url>      : Navigate browser to a new Unstop competition/quiz URL")
        print("      r            : Re-run auto-registration on the current page")
        print("      q            : Close browser and exit AgentX")
        print("=" * 70 + "\n")

        while True:
            try:
                cmd = input("AgentX Screen Control [Enter to scan / q to exit] > ").strip()
                if cmd.lower() in ("q", "quit", "exit"):
                    print("[*] Exiting AgentX. Closing browser...")
                    self.browser_manager.close()
                    break

                if cmd.lower().startswith("u "):
                    new_url = cmd[2:].strip()
                    if new_url.startswith("http"):
                        print(f"[*] Navigating AgentX to: {new_url}...")
                        active_p = self._get_active_quiz_page(page)
                        active_p.goto(new_url, wait_until="domcontentloaded")
                        self.driver.inject_visual_cursor(active_p)
                        self.driver.auto_register_if_needed(active_p)
                        active_p = self.driver.find_and_enter_assessment(active_p)
                        page = active_p
                    else:
                        print("[!] Please provide a valid URL starting with http:// or https://")
                    continue

                if cmd.lower() == "r":
                    active_p = self._get_active_quiz_page(page)
                    print("[*] Re-checking autonomous registration on active page...")
                    done = self.driver.auto_register_if_needed(active_p)
                    print(f"[*] Registration status: {'Completed/Already Registered' if done else 'No registration form found'}")
                    continue

                # Default Enter: scan current page
                active_p = self._get_active_quiz_page(page)
                self.driver.inject_visual_cursor(active_p)
                if self.driver._has_visible_questions(active_p):
                    print("[*] Active quiz questions detected on screen! Starting autonomous test solver...")
                    sess_id = self._create_db_session(active_p.url)
                    self._solve_assessment_loop(active_p, session_id=sess_id, max_questions=100, update_callback=update_callback)
                else:
                    overview = self.driver.detect_competition_overview(active_p)
                    print(f"[*] Current Page: {overview.get('title') or active_p.title()}")
                    print(f"[*] Registered: {overview.get('is_registered')}")
                    print(f"[*] Status: {overview.get('status_message') or 'Monitoring page for changes'}")
            except (KeyboardInterrupt, EOFError):
                print("\n[*] Received exit signal. Closing browser...")
                self.browser_manager.close()
                break
            except Exception as exc:
                logger.error("Error in screen control loop: %s", exc)

    # ─── Internal Helpers ─────────────────────────────────────────────────────

    def _human_delay(self) -> None:
        """Inject randomized micro-delay to simulate natural human deliberation."""
        d_min = settings.quiz_automation.human_like_delay_min
        d_max = settings.quiz_automation.human_like_delay_max
        delay = random.uniform(d_min, d_max)
        time.sleep(delay)

    def _wait_for_live_questions(
        self,
        page: Any,
        timeout_seconds: int = 30,
        update_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> Any:
        """Observe all open browser tabs and frames until quiz questions appear on screen."""
        self._notify(
            update_callback,
            "status",
            "AgentX is observing screen for live quiz questions... (You can click into your test if needed)",
        )
        end_time = time.time() + timeout_seconds

        while time.time() < end_time:
            context_pages = (
                self.browser_manager._context.pages
                if self.browser_manager._context
                else [page]
            )
            for candidate_page in reversed(context_pages):
                try:
                    self.driver.inject_visual_cursor(candidate_page)
                    if "/register" in getattr(candidate_page, "url", ""):
                        self.driver.auto_register_if_needed(candidate_page)
                    self.driver.click_start_assessment(candidate_page)

                    if self.driver._has_visible_questions(candidate_page):
                        logger.info("Live questions detected on tab: %s", getattr(candidate_page, "url", "unknown"))
                        if hasattr(candidate_page, "bring_to_front"):
                            candidate_page.bring_to_front()
                        return candidate_page
                except Exception:
                    pass

            time.sleep(1.2)

        return self._get_active_quiz_page(page)

    def _get_active_quiz_page(self, fallback_page: Any) -> Any:
        """Locate the currently active browser tab."""
        if not self.browser_manager._context:
            return fallback_page
        pages = self.browser_manager._context.pages
        if not pages:
            return fallback_page
        for p in reversed(pages):
            try:
                if "/assessment/" in getattr(p, "url", "") or self.driver._has_visible_questions(p):
                    return p
            except Exception:
                pass
        return pages[-1]

    def _notify(
        self,
        callback: Optional[Callable[[dict[str, Any]], None]],
        event_type: str,
        payload: Any,
    ) -> None:
        """Dispatch progress notifications to caller UI or logger."""
        if callback:
            try:
                callback({"event": event_type, "data": payload})
            except Exception as exc:
                logger.debug("Callback notification error: %s", exc)

    def _create_db_session(self, opportunity_url: str) -> int:
        """Create a new live QuizSession in the SQLite database."""
        with get_session() as session:
            db_sess = QuizSession(
                mode="live",
                topic=opportunity_url[:200],
                total_questions=0,
                correct_answers=0,
                score=0.0,
                duration_seconds=0,
            )
            session.add(db_sess)
            session.commit()
            session.refresh(db_sess)

            # Audit log entry
            log = ActivityLog(
                module="quiz_agent",
                action="start_live_assessment",
                status="success",
                details=f"Initiated live quiz session #{db_sess.id} for {opportunity_url}",
            )
            session.add(log)
            session.commit()
            return db_sess.id

    def _record_attempt(
        self,
        session_id: int,
        q_num: int,
        q_text: str,
        options: list[str],
        decision: LiveAnswerDecision,
        timer_secs: Optional[int],
    ) -> None:
        """Persist question attempt and update question bank."""
        with get_session() as session:
            attempt = LiveQuizAttempt(
                session_id=session_id,
                question_number=q_num,
                question_text=q_text,
                chosen_option=decision.chosen_option,
                chosen_index=decision.chosen_index,
                confidence=decision.confidence,
                rationale=decision.rationale,
                time_remaining_seconds=timer_secs,
            )
            attempt.set_options(options)
            session.add(attempt)

            # Check if we should archive this question into the general question bank
            existing = session.scalar(
                select(QuizQuestion).where(QuizQuestion.question == q_text).limit(1)
            )
            if not existing and len(options) >= 2:
                q_bank = QuizQuestion(
                    question=q_text,
                    correct_answer=decision.chosen_option,
                    explanation=decision.rationale,
                    topic="live_unstop",
                    difficulty="medium",
                    source="live_unstop",
                    times_shown=1,
                    times_correct=1,
                )
                q_bank.set_options(options)
                session.add(q_bank)

            session.commit()

    def _finalize_db_session(self, session_id: int, total_questions: int, duration: float) -> None:
        """Update QuizSession with final counts and duration."""
        with get_session() as session:
            db_sess = session.get(QuizSession, session_id)
            if db_sess:
                db_sess.total_questions = total_questions
                db_sess.duration_seconds = int(duration)
                db_sess.completed_at = datetime.now(timezone.utc)

                log = ActivityLog(
                    module="quiz_agent",
                    action="complete_live_assessment",
                    status="success",
                    details=f"Completed live quiz session #{session_id} ({total_questions} questions answered in {int(duration)}s)",
                )
                session.add(log)
                session.commit()
