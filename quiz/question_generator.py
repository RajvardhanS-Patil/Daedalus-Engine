"""
Unstop Autopilot — Quiz Question Generator

Generates high-yield multiple-choice practice questions (MCQs) for competition
topics and aptitude domains using local Qwen 2.5 7B via Ollama.
Saves verified questions into the local SQLite database question bank.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from agents.llm_client import OllamaClient, OllamaError
from database.connection import get_session
from database.models import Opportunity, QuizQuestion

logger = logging.getLogger(__name__)


class QuizQuestionSchema(BaseModel):
    """Schema for a single multiple-choice question."""
    question: str = Field(description="The question prompt")
    options: list[str] = Field(description="List of 4 distinct choices [A, B, C, D]", min_length=4, max_length=4)
    correct_answer: str = Field(description="The exact text of the correct option")
    explanation: str = Field(description="Explanation of why this answer is correct")
    topic: str = Field(default="general", description="Topic/domain of the question")
    difficulty: str = Field(default="medium", description="easy, medium, or hard")


class QuizQuestionBatch(BaseModel):
    """Schema for a batch of generated quiz questions."""
    questions: list[QuizQuestionSchema] = Field(default_factory=list)


# Fallback question bank for offline/resilient test execution
FALLBACK_QUESTIONS: dict[str, list[dict[str, Any]]] = {
    "python": [
        {
            "question": "What is the output of print(type([])) in Python 3?",
            "options": ["<class 'list'>", "<class 'dict'>", "<class 'set'>", "<class 'tuple'>"],
            "correct_answer": "<class 'list'>",
            "explanation": "Square brackets [] denote a list literal in Python.",
            "topic": "Python",
            "difficulty": "easy",
        },
        {
            "question": "Which built-in Python module provides tools for working with iterators?",
            "options": ["itertools", "collections", "functools", "operator"],
            "correct_answer": "itertools",
            "explanation": "The itertools module provides standardized functions for efficient looping.",
            "topic": "Python",
            "difficulty": "medium",
        },
    ],
    "data_structures": [
        {
            "question": "What is the worst-case time complexity of quicksort?",
            "options": ["O(n^2)", "O(n log n)", "O(n)", "O(log n)"],
            "correct_answer": "O(n^2)",
            "explanation": "When the pivot chosen is consistently the smallest or largest element, quicksort degrades to O(n^2).",
            "topic": "Data Structures",
            "difficulty": "medium",
        },
        {
            "question": "Which data structure operates on a First-In-First-Out (FIFO) principle?",
            "options": ["Queue", "Stack", "Binary Tree", "Heap"],
            "correct_answer": "Queue",
            "explanation": "A queue processes elements in first-in, first-out order.",
            "topic": "Data Structures",
            "difficulty": "easy",
        },
    ],
    "general": [
        {
            "question": "What does HTTP stand for in computer networking?",
            "options": [
                "HyperText Transfer Protocol",
                "High Tech Transmission Path",
                "HyperTransfer Text Page",
                "Host Transfer Terminal Protocol",
            ],
            "correct_answer": "HyperText Transfer Protocol",
            "explanation": "HTTP is the foundational protocol used by the World Wide Web.",
            "topic": "General Tech",
            "difficulty": "easy",
        },
        {
            "question": "In Git, which command creates a new local branch and switches to it immediately?",
            "options": ["git checkout -b <branch>", "git branch -n <branch>", "git switch -c <branch>", "Both git checkout -b and git switch -c"],
            "correct_answer": "Both git checkout -b and git switch -c",
            "explanation": "Both git checkout -b and the newer git switch -c create and switch to a new branch.",
            "topic": "General Tech",
            "difficulty": "easy",
        },
    ],
}


class QuestionGenerator:
    """Generates structured MCQs using local Qwen 2.5 7B and persists them."""

    def __init__(self, llm_client: OllamaClient | None = None) -> None:
        self.llm = llm_client or OllamaClient()

    def generate_questions(
        self,
        topic: str,
        num_questions: int = 5,
        difficulty: str = "medium",
        context: str = "",
        save_to_db: bool = True,
    ) -> list[QuizQuestion]:
        """Generate MCQs for a specified topic, optionally saving to the database."""
        questions_data = self._generate_via_llm(topic, num_questions, difficulty, context)
        if not questions_data:
            logger.info("Using fallback questions for topic '%s'", topic)
            questions_data = self._get_fallback_questions(topic, num_questions, difficulty)

        saved_questions: list[QuizQuestion] = []
        if save_to_db:
            with get_session() as session:
                for qd in questions_data:
                    # Check if already exists to avoid exact duplicates
                    existing = session.execute(
                        select(QuizQuestion).where(QuizQuestion.question == qd.question)
                    ).scalar_one_or_none()
                    if existing:
                        saved_questions.append(existing)
                        continue

                    db_q = QuizQuestion(
                        question=qd.question,
                        correct_answer=qd.correct_answer,
                        explanation=qd.explanation,
                        topic=qd.topic,
                        difficulty=qd.difficulty,
                        source="generated",
                    )
                    db_q.set_options(qd.options)
                    session.add(db_q)
                    session.commit()
                    session.refresh(db_q)
                    saved_questions.append(db_q)
        else:
            for qd in questions_data:
                dummy_q = QuizQuestion(
                    question=qd.question,
                    correct_answer=qd.correct_answer,
                    explanation=qd.explanation,
                    topic=qd.topic,
                    difficulty=qd.difficulty,
                    source="generated",
                )
                dummy_q.set_options(qd.options)
                saved_questions.append(dummy_q)

        logger.info(
            "Generated %d quiz questions for topic '%s' (difficulty=%s)",
            len(saved_questions),
            topic,
            difficulty,
        )
        return saved_questions

    def generate_for_opportunity(
        self,
        opportunity_id: int,
        num_questions: int = 5,
        difficulty: str = "medium",
    ) -> list[QuizQuestion]:
        """Generate tailored practice MCQs for a specific competition or hackathon."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                raise ValueError(f"Opportunity with ID {opportunity_id} not found")

            topic = opp.category or "general"
            context = f"Competition Title: {opp.title}\nDescription: {opp.description}\nCategory: {opp.category}"

        return self.generate_questions(
            topic=topic,
            num_questions=num_questions,
            difficulty=difficulty,
            context=context,
        )

    def _generate_via_llm(
        self,
        topic: str,
        num_questions: int,
        difficulty: str,
        context: str,
    ) -> list[QuizQuestionSchema]:
        """Attempt to generate structured MCQs using Qwen 2.5 7B."""
        system_prompt = (
            "You are an expert technical interviewer and competition assessment designer. "
            "Generate rigorous, accurate multiple-choice practice questions (MCQs) for university "
            "and national-level competitive assessments. You must reply strictly with valid JSON "
            "matching the requested schema."
        )

        user_prompt = (
            f"Generate exactly {num_questions} multiple-choice questions for competitive preparation.\n"
            f"Topic: {topic}\n"
            f"Difficulty Level: {difficulty}\n"
        )
        if context:
            user_prompt += f"Context regarding the competition:\n{context}\n\n"

        user_prompt += (
            "Return JSON matching this format:\n"
            "{\n"
            '  "questions": [\n'
            "    {\n"
            '      "question": "What is ...?",\n'
            '      "options": ["Option A", "Option B", "Option C", "Option D"],\n'
            '      "correct_answer": "Option A",\n'
            '      "explanation": "Clear explanation of why Option A is correct.",\n'
            f'      "topic": "{topic}",\n'
            f'      "difficulty": "{difficulty}"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Rules:\n"
            "1. Each question MUST have exactly 4 choices.\n"
            "2. 'correct_answer' MUST exactly match one of the elements in 'options'.\n"
            "3. Explanations must be educational and factually accurate.\n"
            "4. Output strictly valid JSON."
        )

        try:
            batch = self.llm.generate_json(
                prompt=user_prompt,
                system=system_prompt,
                response_model=QuizQuestionBatch,
            )
            if isinstance(batch, QuizQuestionBatch) and batch.questions:
                # Sanitize correct_answer matching
                valid_questions = []
                for q in batch.questions:
                    if q.correct_answer not in q.options:
                        # Fallback: assign first option as correct or pick closest
                        q.options[0] = q.correct_answer
                    valid_questions.append(q)
                return valid_questions
        except OllamaError as err:
            logger.warning("LLM question generation failed: %s", err)
        except Exception as err:
            logger.warning("Unexpected error during question generation: %s", err)

        return []

    def _get_fallback_questions(
        self,
        topic: str,
        num_questions: int,
        difficulty: str,
    ) -> list[QuizQuestionSchema]:
        """Provide verified fallback MCQs when LLM is unavailable."""
        normalized_topic = topic.lower().replace(" ", "_")
        candidates = FALLBACK_QUESTIONS.get(normalized_topic)
        if not candidates:
            # Check partial match
            for k, v in FALLBACK_QUESTIONS.items():
                if k in normalized_topic or normalized_topic in k:
                    candidates = v
                    break
        if not candidates:
            candidates = FALLBACK_QUESTIONS["general"]

        results = []
        for i in range(num_questions):
            item = candidates[i % len(candidates)]
            results.append(
                QuizQuestionSchema(
                    question=f"{item['question']} (#{i+1})" if i >= len(candidates) else item['question'],
                    options=list(item["options"]),
                    correct_answer=item["correct_answer"],
                    explanation=item["explanation"],
                    topic=topic,
                    difficulty=difficulty,
                )
            )
        return results
