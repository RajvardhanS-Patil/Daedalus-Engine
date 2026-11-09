"""
Tests for QuizSolver (LLM answer engine and fallback heuristic).
"""

from unittest.mock import MagicMock

import pytest

from agents.llm_client import OllamaError
from quiz.solver import LiveAnswerDecision, QuizSolver


def test_live_answer_decision_validation():
    """Verify LiveAnswerDecision Pydantic schema validation."""
    decision = LiveAnswerDecision(
        chosen_option="O(log n)",
        chosen_index=1,
        confidence=0.92,
        rationale="Binary search divides the search interval in half each iteration.",
    )
    assert decision.chosen_index == 1
    assert decision.confidence == 0.92
    assert "Binary search" in decision.rationale


def test_quiz_solver_with_mocked_llm():
    """Test standard quiz question solving using mocked Ollama response."""
    mock_llm = MagicMock()
    mock_llm.generate_json.return_value = LiveAnswerDecision(
        chosen_option="O(1)",
        chosen_index=0,
        confidence=0.95,
        rationale="Hash table lookup has O(1) average time complexity.",
    )

    solver = QuizSolver(llm_client=mock_llm)
    options = ["O(1)", "O(n)", "O(log n)", "O(n^2)"]
    result = solver.solve(
        question="What is the average time complexity of hash table lookup?",
        options=options,
    )

    assert result.chosen_index == 0
    assert result.chosen_option == "O(1)"
    assert result.confidence == 0.95
    assert mock_llm.generate_json.called


def test_quiz_solver_index_out_of_bounds_recovery():
    """Test that an out-of-bounds index from LLM is safely reconciled with option text."""
    mock_llm = MagicMock()
    mock_llm.generate_json.return_value = LiveAnswerDecision(
        chosen_option="Python",
        chosen_index=9,  # out of bounds for 3 options
        confidence=0.88,
        rationale="Python is an interpreted language.",
    )

    solver = QuizSolver(llm_client=mock_llm)
    options = ["C++", "Java", "Python"]
    result = solver.solve(
        question="Which language uses indentation for blocks?",
        options=options,
    )

    # Should match option 'Python' at index 2
    assert result.chosen_index == 2
    assert result.chosen_option == "Python"


def test_keyword_heuristic():
    """Test heuristic answer selection when LLM is unavailable."""
    solver = QuizSolver()
    question = "Which of the following data structures is non-linear?"
    options = ["Array", "Linked List", "Stack", "Binary Tree"]

    idx = solver._keyword_heuristic(question, options)
    assert 0 <= idx < len(options)


def test_keyword_heuristic_all_of_the_above():
    """Test heuristic gives priority to 'All of the above' options."""
    solver = QuizSolver()
    question = "Which protocols operate at the transport layer?"
    options = ["TCP", "UDP", "All of the above", "None of the above"]

    idx = solver._keyword_heuristic(question, options)
    assert idx == 2  # 'All of the above'


def test_solve_with_fallback_on_llm_failure():
    """Verify that solve_with_fallback safely returns a decision when Ollama fails."""
    mock_llm = MagicMock()
    mock_llm.generate_json.side_effect = OllamaError("Connection refused")

    solver = QuizSolver(llm_client=mock_llm)
    options = ["Alpha", "Beta", "Gamma", "Delta"]

    result = solver.solve_with_fallback(
        question="What is the first letter of the Greek alphabet?",
        options=options,
    )

    assert isinstance(result, LiveAnswerDecision)
    assert 0 <= result.chosen_index < len(options)
    assert result.chosen_option in options
    assert "Fallback heuristic" in result.rationale
