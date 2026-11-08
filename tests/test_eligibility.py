"""
Tests for Eligibility Evaluation Engine
"""

import pytest
from intelligence.eligibility_engine import EligibilityEngine


def test_eligibility_everyone_can_apply():
    """Verify 'Everyone can apply' evaluates to eligible with high confidence."""
    engine = EligibilityEngine()
    opp_data = {
        "title": "Open Coding Challenge",
        "description": "Everyone can apply. All branches welcome.",
        "eligibility": "Open to all students",
    }

    eval_result = engine.evaluate(opp_data)
    assert eval_result.is_eligible is True
    assert eval_result.confidence >= 0.95


def test_eligibility_corporate_only():
    """Verify working professionals only evaluates to ineligible."""
    engine = EligibilityEngine()
    opp_data = {
        "title": "Corporate Leadership Summit",
        "description": "Exclusively for working professionals with 3+ years experience.",
        "eligibility": "Corporate employees only",
    }

    eval_result = engine.evaluate(opp_data)
    assert eval_result.is_eligible is False
    assert eval_result.confidence >= 0.90


def test_eligibility_school_only():
    """Verify school students only evaluates to ineligible for college candidate."""
    engine = EligibilityEngine()
    opp_data = {
        "title": "Junior Science Olympiad",
        "description": "For class 6 to 12 school students only.",
        "eligibility": "School students only",
    }

    eval_result = engine.evaluate(opp_data)
    assert eval_result.is_eligible is False
    assert eval_result.confidence >= 0.85
