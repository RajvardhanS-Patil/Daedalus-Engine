"""
Tests for LiveQuizAgent and QuizDriver.
"""

from unittest.mock import MagicMock, patch

import pytest

from agents.quiz_agent import LiveQuizAgent, QuizRunResult
from browser.quiz_driver import QuizDriver, ScrapedQuestion
from database.connection import get_session, init_database
from database.models import LiveQuizAttempt, QuizSession
from quiz.solver import LiveAnswerDecision


@pytest.fixture(autouse=True)
def setup_db():
    init_database()


def test_driver_parse_timer_seconds():
    """Verify timer string parsing to seconds."""
    driver = QuizDriver(browser_manager=MagicMock())

    assert driver._parse_timer_seconds("10:00") == 600
    assert driver._parse_timer_seconds("01:30") == 90
    assert driver._parse_timer_seconds("01:05:20") == 3920
    assert driver._parse_timer_seconds("15 mins") == 900
    assert driver._parse_timer_seconds("") is None


def test_driver_proctoring_detection_positive():
    """Test that proctored pages trigger abort condition."""
    mock_page = MagicMock()
    mock_page.content.return_value = "<html><body>Please note: Webcam is required for this proctored assessment.</body></html>"
    mock_page.query_selector.return_value = None

    driver = QuizDriver(browser_manager=MagicMock())
    is_proctored, reason = driver.detect_proctoring(mock_page)

    assert is_proctored is True
    assert "Webcam" in reason


def test_driver_proctoring_detection_negative():
    """Test that regular unproctored quiz pages pass proctoring detection."""
    mock_page = MagicMock()
    mock_page.content.return_value = "<html><body>Welcome to the Python Online Quiz. You have 15 minutes.</body></html>"
    mock_page.query_selector.return_value = None

    driver = QuizDriver(browser_manager=MagicMock())
    is_proctored, reason = driver.detect_proctoring(mock_page)

    assert is_proctored is False
    assert reason == ""


def test_live_quiz_agent_login_required():
    """Test that agent halts gracefully if user is not logged in."""
    mock_bm = MagicMock()
    mock_bm.check_login_status.return_value = False
    mock_bm.new_page.return_value = MagicMock()

    agent = LiveQuizAgent(browser_manager=mock_bm)
    result = agent.run_quiz("https://unstop.com/assessment/test123")

    assert result.status == "login_required"
    assert "No active Unstop login session found" in result.error_message


def test_live_quiz_agent_proctoring_abort():
    """Test that agent aborts if proctoring is detected on assessment page."""
    mock_bm = MagicMock()
    mock_bm.check_login_status.return_value = True
    mock_page = MagicMock()
    mock_bm.new_page.return_value = mock_page

    mock_driver = MagicMock()
    mock_driver.navigate_to_quiz.return_value = True
    mock_driver.detect_proctoring.return_value = (True, "Webcam required")

    agent = LiveQuizAgent(browser_manager=mock_bm, driver=mock_driver)
    result = agent.run_quiz("https://unstop.com/assessment/proctored-test")

    assert result.status == "aborted_proctoring"
    assert "Proctoring restriction detected" in result.error_message


def test_live_quiz_agent_successful_run():
    """Test full autonomous quiz loop with mocked driver and solver."""
    mock_bm = MagicMock()
    mock_bm.check_login_status.return_value = True
    mock_page = MagicMock()
    mock_bm.new_page.return_value = mock_page

    mock_driver = MagicMock()
    mock_driver.navigate_to_quiz.return_value = True
    mock_driver.detect_proctoring.return_value = (False, "")
    mock_driver.click_start_assessment.return_value = True
    mock_driver.is_quiz_completed.side_effect = [False, False, True]

    # Two questions
    q1 = ScrapedQuestion(
        question_number=1,
        total_questions=2,
        question_text="What is 2 + 2?",
        options=["3", "4", "5", "6"],
        is_last_question=False,
    )
    q2 = ScrapedQuestion(
        question_number=2,
        total_questions=2,
        question_text="What is 3 * 3?",
        options=["6", "8", "9", "12"],
        is_last_question=True,
    )

    mock_driver.scrape_current_question.side_effect = [q1, q2, ScrapedQuestion()]
    mock_driver.select_option.return_value = True
    mock_driver.click_next.return_value = True
    mock_driver.click_submit_quiz.return_value = True

    mock_solver = MagicMock()
    mock_solver.solve_with_fallback.side_effect = [
        LiveAnswerDecision(chosen_option="4", chosen_index=1, confidence=0.99, rationale="2+2=4"),
        LiveAnswerDecision(chosen_option="9", chosen_index=2, confidence=0.99, rationale="3*3=9"),
    ]

    agent = LiveQuizAgent(
        browser_manager=mock_bm,
        driver=mock_driver,
        solver=mock_solver,
    )

    result = agent.run_quiz("https://unstop.com/assessment/mock-math-quiz")

    assert result.status == "completed"
    assert result.answers_submitted == 2
    assert result.average_confidence >= 0.95
    assert len(result.attempts) == 2

    # Verify database persistence
    with get_session() as session:
        db_sess = session.get(QuizSession, result.session_id)
        assert db_sess is not None
        assert db_sess.mode == "live"
        assert db_sess.total_questions == 2

        attempts = session.query(LiveQuizAttempt).filter_by(session_id=result.session_id).all()
        assert len(attempts) == 2
        assert attempts[0].chosen_option == "4"
        assert attempts[1].chosen_option == "9"
