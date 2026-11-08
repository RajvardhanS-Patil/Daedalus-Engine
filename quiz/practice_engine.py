"""
Unstop Autopilot — Quiz Practice Engine

Executes interactive, timed or untimed practice assessments.
Tracks individual question accuracy, session scores, duration, and
identifies weak topic areas to maximize competitive performance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from database.connection import get_session
from database.models import QuizQuestion, QuizSession
from quiz.question_generator import QuestionGenerator

logger = logging.getLogger(__name__)


@dataclass
class PracticeSessionState:
    """Represents the in-memory state of an active practice session."""
    session_id: int
    mode: str
    topic: str
    total_questions: int
    questions: list[QuizQuestion]
    started_at: datetime


class PracticeEngine:
    """Coordinates practice test execution, evaluation, and analytics."""

    def __init__(self, generator: QuestionGenerator | None = None) -> None:
        self.generator = generator or QuestionGenerator()

    def start_session(
        self,
        mode: str = "practice",
        topic: str = "general",
        question_count: int = 5,
        difficulty: str = "medium",
    ) -> PracticeSessionState:
        """Initialize a practice session and load or generate target questions."""
        questions: list[QuizQuestion] = []
        with get_session() as session:
            # Query existing questions in DB matching topic or difficulty
            query = (
                select(QuizQuestion)
                .where(QuizQuestion.topic.ilike(f"%{topic}%"))
                .order_by(QuizQuestion.times_shown.asc(), func.random())
                .limit(question_count)
            )
            found = list(session.execute(query).scalars().all())

            # If not enough questions, generate new ones via QuestionGenerator
            needed = question_count - len(found)
            if needed > 0:
                new_questions = self.generator.generate_questions(
                    topic=topic,
                    num_questions=needed,
                    difficulty=difficulty,
                    save_to_db=True,
                )
                found.extend(new_questions)

            # Cap at question_count
            questions = found[:question_count]
            q_ids = [q.id for q in questions]

            quiz_session = QuizSession(
                mode=mode,
                topic=topic,
                total_questions=len(questions),
                correct_answers=0,
                score=0.0,
                duration_seconds=0,
                question_ids=json.dumps(q_ids),
                started_at=datetime.now(timezone.utc),
            )
            session.add(quiz_session)
            session.commit()
            session.refresh(quiz_session)
            session_id = quiz_session.id

        return PracticeSessionState(
            session_id=session_id,
            mode=mode,
            topic=topic,
            total_questions=len(questions),
            questions=questions,
            started_at=datetime.now(timezone.utc),
        )

    def submit_answer(
        self,
        session_id: int,
        question_id: int,
        selected_option: str,
    ) -> dict[str, Any]:
        """Evaluate a submitted answer, update question stats and session score."""
        with get_session() as session:
            q = session.get(QuizQuestion, question_id)
            if not q:
                raise ValueError(f"QuizQuestion with ID {question_id} not found")

            options = q.get_options()
            # Normalize user input (e.g. 'A', '1', or exact text)
            normalized_answer = self._resolve_option_text(selected_option, options)
            is_correct = (
                normalized_answer.strip().lower() == q.correct_answer.strip().lower()
            )

            # Update question performance metrics
            q.times_shown += 1
            if is_correct:
                q.times_correct += 1

            # Update session record
            quiz_session = session.get(QuizSession, session_id)
            if quiz_session:
                if is_correct:
                    quiz_session.correct_answers += 1
                if quiz_session.total_questions > 0:
                    quiz_session.score = (
                        quiz_session.correct_answers / quiz_session.total_questions
                    ) * 100.0

            session.commit()

            return {
                "question_id": question_id,
                "selected_option": normalized_answer,
                "correct_answer": q.correct_answer,
                "is_correct": is_correct,
                "explanation": q.explanation,
                "current_score": quiz_session.score if quiz_session else 0.0,
                "correct_count": quiz_session.correct_answers if quiz_session else 0,
            }

    def complete_session(self, session_id: int) -> dict[str, Any]:
        """Conclude an active practice session and persist final metrics."""
        now = datetime.now(timezone.utc)
        with get_session() as session:
            quiz_session = session.get(QuizSession, session_id)
            if not quiz_session:
                raise ValueError(f"QuizSession with ID {session_id} not found")

            quiz_session.completed_at = now
            if quiz_session.started_at:
                # Handle tz-aware or naive datetimes gracefully
                start_dt = quiz_session.started_at
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                quiz_session.duration_seconds = max(1, int((now - start_dt).total_seconds()))

            if quiz_session.total_questions > 0:
                quiz_session.score = round(
                    (quiz_session.correct_answers / quiz_session.total_questions) * 100.0, 2
                )

            session.commit()

            return {
                "session_id": quiz_session.id,
                "topic": quiz_session.topic,
                "mode": quiz_session.mode,
                "total_questions": quiz_session.total_questions,
                "correct_answers": quiz_session.correct_answers,
                "score_pct": quiz_session.score,
                "duration_seconds": quiz_session.duration_seconds,
            }

    def get_performance_analytics(self) -> dict[str, Any]:
        """Calculate overall accuracy, weak topic areas, and recent sessions."""
        with get_session() as session:
            total_shown = session.execute(
                select(func.sum(QuizQuestion.times_shown))
            ).scalar() or 0
            total_correct = session.execute(
                select(func.sum(QuizQuestion.times_correct))
            ).scalar() or 0

            overall_accuracy = (
                round((total_correct / total_shown) * 100.0, 2)
                if total_shown > 0
                else 0.0
            )

            # Topic breakdown
            topics_query = (
                select(
                    QuizQuestion.topic,
                    func.sum(QuizQuestion.times_shown).label("shown"),
                    func.sum(QuizQuestion.times_correct).label("correct"),
                )
                .group_by(QuizQuestion.topic)
                .having(func.sum(QuizQuestion.times_shown) > 0)
            )
            topic_rows = session.execute(topics_query).all()

            topic_stats = []
            weak_topics = []
            for row in topic_rows:
                topic_name, shown, correct = row[0], row[1], row[2]
                acc = round((correct / shown) * 100.0, 2) if shown > 0 else 0.0
                stat = {
                    "topic": topic_name,
                    "shown": shown,
                    "correct": correct,
                    "accuracy": acc,
                }
                topic_stats.append(stat)
                if acc < 60.0:
                    weak_topics.append(stat)

            # Recent sessions
            recent_sessions_query = (
                select(QuizSession)
                .order_by(QuizSession.started_at.desc())
                .limit(5)
            )
            recent_sessions = session.execute(recent_sessions_query).scalars().all()
            sessions_list = [
                {
                    "id": s.id,
                    "topic": s.topic,
                    "mode": s.mode,
                    "score": s.score,
                    "total": s.total_questions,
                    "correct": s.correct_answers,
                    "duration_sec": s.duration_seconds,
                    "started_at": s.started_at.strftime("%Y-%m-%d %H:%M") if s.started_at else "",
                }
                for s in recent_sessions
            ]

            return {
                "total_questions_attempted": total_shown,
                "total_correct": total_correct,
                "overall_accuracy_pct": overall_accuracy,
                "topic_breakdown": topic_stats,
                "weak_topics": weak_topics,
                "recent_sessions": sessions_list,
            }

    def _resolve_option_text(self, selection: str, options: list[str]) -> str:
        """Resolve selection like 'A', '1', or option string to the actual option text."""
        cleaned = selection.strip()
        # Check single letter A, B, C, D
        letter_map = {"A": 0, "B": 1, "C": 2, "D": 3, "a": 0, "b": 1, "c": 2, "d": 3}
        if cleaned in letter_map:
            idx = letter_map[cleaned]
            if idx < len(options):
                return options[idx]

        # Check digits 1, 2, 3, 4
        if cleaned.isdigit():
            idx = int(cleaned) - 1
            if 0 <= idx < len(options):
                return options[idx]

        # Exact match or prefix match
        for opt in options:
            if cleaned.lower() == opt.lower() or opt.lower().startswith(cleaned.lower()):
                return opt

        return selection
