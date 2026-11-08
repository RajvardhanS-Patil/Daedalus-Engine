"""
Tests for Discovery Data Structures and Scraping Logic
"""

import pytest
from browser.public_discovery import RawOpportunity, _CATEGORY_TOKENS, _MODE_TOKENS


def test_raw_opportunity_defaults():
    """Verify RawOpportunity default values."""
    opp = RawOpportunity(
        title="Sample Opportunity",
        url="https://unstop.com/competitions/sample",
    )
    assert opp.deadline == "unknown"
    assert opp.fee == "unknown"
    assert opp.mode == "unknown"
    assert opp.category == "unknown"
    assert opp.source_evidence == {}


def test_category_token_matching():
    """Verify regex patterns for category detection."""
    test_cases = [
        ("https://unstop.com/hackathons/ai-challenge", "hackathon"),
        ("https://unstop.com/competitions/national-case-study", "competition"),
        ("https://unstop.com/quizzes/tech-trivia", "quiz"),
        ("Grand Ideathon 2026", "ideathon"),
    ]

    for text, expected in test_cases:
        matched = False
        for pattern, cat in _CATEGORY_TOKENS:
            if pattern.search(text):
                assert cat == expected
                matched = True
                break
        assert matched, f"Failed to match {text} to {expected}"


def test_mode_token_matching():
    """Verify regex patterns for mode detection."""
    test_cases = [
        ("This is an online event", "online"),
        ("Hybrid presentation round", "hybrid"),
        ("Physical in-person attendance required", "offline"),
    ]

    for text, expected in test_cases:
        matched = False
        for pattern, mode in _MODE_TOKENS:
            if pattern.search(text):
                assert mode == expected
                matched = True
                break
        assert matched, f"Failed to match {text} to {expected}"
