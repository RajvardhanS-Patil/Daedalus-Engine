"""
Tests for Opportunity Classification Categories
"""

import pytest
from intelligence.classification import ClassificationCategory, classify_opportunity


def test_classify_high_priority():
    """Verify score >= 70 yields HIGH PRIORITY."""
    cat = classify_opportunity(suitability_score=85, is_eligible=True, confidence_factor=1.0)
    assert cat == ClassificationCategory.HIGH_PRIORITY


def test_classify_good_opportunity():
    """Verify score 50-69 yields GOOD OPPORTUNITY."""
    cat = classify_opportunity(suitability_score=62, is_eligible=True, confidence_factor=1.0)
    assert cat == ClassificationCategory.GOOD_OPPORTUNITY


def test_classify_ineligible():
    """Verify is_eligible=False yields INELIGIBLE regardless of score."""
    cat = classify_opportunity(suitability_score=90, is_eligible=False)
    assert cat == ClassificationCategory.INELIGIBLE


def test_classify_expired():
    """Verify status=expired yields EXPIRED."""
    cat = classify_opportunity(suitability_score=95, is_eligible=True, status="expired")
    assert cat == ClassificationCategory.EXPIRED


def test_classify_needs_verification():
    """Verify low confidence with decent score triggers NEEDS VERIFICATION."""
    cat = classify_opportunity(
        suitability_score=75,
        is_eligible=True,
        confidence_factor=0.5,  # heavily missing data
    )
    assert cat == ClassificationCategory.NEEDS_VERIFICATION
