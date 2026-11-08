"""
Unstop Autopilot — Opportunity Classification Engine

Maps suitability scores, eligibility evaluations, and deadlines into standard
action priority classifications.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ClassificationCategory(str, Enum):
    """Priority classifications for discovered opportunities."""
    HIGH_PRIORITY = "HIGH PRIORITY"
    GOOD_OPPORTUNITY = "GOOD OPPORTUNITY"
    LOW_PRIORITY = "LOW PRIORITY"
    INELIGIBLE = "INELIGIBLE"
    EXPIRED = "EXPIRED"
    NEEDS_VERIFICATION = "NEEDS VERIFICATION"


def classify_opportunity(
    suitability_score: int,
    is_eligible: bool,
    eligibility_confidence: float = 1.0,
    confidence_factor: float = 1.0,
    status: str = "open",
) -> ClassificationCategory:
    """Determine the final action priority category for an opportunity."""
    if status.lower() in ("expired", "closed"):
        return ClassificationCategory.EXPIRED

    if not is_eligible:
        return ClassificationCategory.INELIGIBLE

    # If confidence is low or information is deficient, request verification
    if confidence_factor < 0.60 or eligibility_confidence < 0.65:
        if suitability_score >= 50:
            return ClassificationCategory.NEEDS_VERIFICATION

    # Score thresholds
    if suitability_score >= 70:
        return ClassificationCategory.HIGH_PRIORITY
    elif suitability_score >= 48:
        return ClassificationCategory.GOOD_OPPORTUNITY
    else:
        return ClassificationCategory.LOW_PRIORITY
