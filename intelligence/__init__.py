"""
Unstop Autopilot — Intelligence Engine Package

Provides suitability scoring, eligibility evaluation, and opportunity classification.
"""

from intelligence.classification import ClassificationCategory, classify_opportunity
from intelligence.eligibility_engine import EligibilityEngine, EligibilityEvaluation
from intelligence.opportunity_scorer import OpportunityScorer, ScoreBreakdown

__all__ = [
    "OpportunityScorer",
    "ScoreBreakdown",
    "EligibilityEngine",
    "EligibilityEvaluation",
    "ClassificationCategory",
    "classify_opportunity",
]
