"""
Unit tests for Quiz Laboratory — QuestionGenerator and PracticeEngine
"""

import uuid
import pytest
from database.connection import get_session
from database.models import Opportunity, QuizQuestion, QuizSession
from quiz.practice_engine import PracticeEngine
from quiz.question_generator import QuestionGenerator, QuizQuestionSchema


def test_question_generator_fallback():
    """Verify fallback question generation creates valid 4-option MCQs."""
    gen = QuestionGenerator()
    questions = gen.generate_questions(
        topic="python",
        num_questions=2,
        difficulty="easy",
        save_to_db=False,
    )
    assert len(questions) == 2
    for q in questions:
        opts = q.get_options()
        assert len(opts) == 4
        assert q.correct_answer in opts
        assert q.explanation != ""


def test_question_generator_db_persistence():
    """Verify generated questions are saved and retrieved in SQLite."""
    gen = QuestionGenerator()
    unique_topic = f"test_topic_{uuid.uuid4().hex[:6]}"
    questions = gen.generate_questions(
        topic=unique_topic,
        num_questions=2,
        difficulty="medium",
        save_to_db=True,
    )
    assert len(questions) == 2
    assert questions[0].id is not None

    with get_session() as session:
        db_q = session.get(QuizQuestion, questions[0].id)
        assert db_q is not None
        assert db_q.topic == unique_topic
        assert len(db_q.get_options()) == 4


def test_practice_engine_lifecycle():
    """Verify complete practice session lifecycle: start, submit, complete, analytics."""
    engine = PracticeEngine()
    state = engine.start_session(
        mode="practice",
        topic="python",
        question_count=2,
        difficulty="easy",
    )
    assert state.session_id is not None
    assert len(state.questions) == 2

    q1 = state.questions[0]
    q2 = state.questions[1]

    # Submit correct answer for Q1
    res1 = engine.submit_answer(
        session_id=state.session_id,
        question_id=q1.id,
        selected_option=q1.correct_answer,
    )
    assert res1["is_correct"] is True
    assert res1["correct_count"] == 1

    # Submit deliberately wrong answer for Q2
    wrong_opt = [opt for opt in q2.get_options() if opt != q2.correct_answer][0]
    res2 = engine.submit_answer(
        session_id=state.session_id,
        question_id=q2.id,
        selected_option=wrong_opt,
    )
    assert res2["is_correct"] is False

    # Complete session
    summary = engine.complete_session(state.session_id)
    assert summary["session_id"] == state.session_id
    assert summary["total_questions"] == 2
    assert summary["correct_answers"] == 1
    assert summary["score_pct"] == 50.0

    # Analytics
    analytics = engine.get_performance_analytics()
    assert analytics["total_questions_attempted"] >= 2
    assert analytics["total_correct"] >= 1
    assert "topic_breakdown" in analytics


def test_opportunity_quiz_generation():
    """Verify question generation tailored to an Opportunity."""
    with get_session() as session:
        opp = Opportunity(
            title="Algorithm Masters 2026",
            url=f"https://unstop.com/quiz/algo-{uuid.uuid4().hex[:6]}",
            category="quiz",
            description="Competitive programming and graph theory algorithms challenge",
            status="open",
        )
        session.add(opp)
        session.commit()
        opp_id = opp.id

    gen = QuestionGenerator()
    questions = gen.generate_for_opportunity(opp_id, num_questions=2)
    assert len(questions) == 2
    for q in questions:
        assert len(q.get_options()) == 4
        assert q.correct_answer != ""
