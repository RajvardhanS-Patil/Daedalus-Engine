"""
Tests for Opportunity Suitability Scorer
"""

import pytest
from intelligence.opportunity_scorer import OpportunityScorer, ScoreBreakdown


def test_scorer_ideal_opportunity():
    """Verify high score for ideal student opportunity (free, solo, online, certificate)."""
    scorer = OpportunityScorer()
    opp_data = {
        "participation_type": "individual",
        "participants_count": "Solo / 1 member",
        "fee": "Free",
        "certificate_info": "Certificate of participation provided",
        "mode": "online",
        "category": "quiz",
        "deadline": "25 days left",
        "num_rounds": "1",
    }

    result = scorer.score(opp_data, is_eligible=True, eligibility_confidence=1.0)

    assert result.total_score >= 80
    assert result.individual_participation == 20
    assert result.free_registration == 20
    assert result.certificate_available == 15
    assert result.online_participation == 15
    assert result.eligibility_match == 15


def test_scorer_ineligible_penalty():
    """Verify ineligible opportunities receive a heavy score penalty."""
    scorer = OpportunityScorer()
    opp_data = {
        "participation_type": "individual",
        "fee": "Free",
        "certificate_info": "Certificate provided",
        "mode": "online",
        "category": "competition",
        "deadline": "10 days left",
    }

    result = scorer.score(opp_data, is_eligible=False, eligibility_confidence=1.0)
    # Even if other criteria are great, ineligibility caps score to <= 30
    assert result.total_score <= 30
    assert result.eligibility_match == 0


def test_scorer_unknown_fields_cap():
    """Verify that multiple unknown fields prevent a false high score."""
    scorer = OpportunityScorer()
    opp_data = {
        "participation_type": "unknown",
        "fee": "unknown",
        "certificate_info": "unknown",
        "mode": "unknown",
        "category": "unknown",
        "deadline": "unknown",
    }

    result = scorer.score(opp_data, is_eligible=None)
    # When many fields are unknown, confidence factor drops and score is capped at 65
    assert result.total_score <= 65
    assert result.confidence_factor < 0.8
