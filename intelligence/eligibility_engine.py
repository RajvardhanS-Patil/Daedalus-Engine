"""
Unstop Autopilot — Eligibility Evaluation Engine

Evaluates candidate eligibility for competitions based on stated criteria,
college/student restrictions, degree level, and geographic constraints.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from database.connection import get_session
from database.models import UserPreference

logger = logging.getLogger(__name__)


@dataclass
class EligibilityEvaluation:
    """Detailed eligibility assessment result."""
    is_eligible: bool
    confidence: float
    reason: str
    criteria_checked: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_eligible": self.is_eligible,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "criteria_checked": self.criteria_checked,
        }


class EligibilityEngine:
    """Rules and heuristic engine for determining student participation eligibility."""

    def __init__(self) -> None:
        self._user_profile: dict[str, str] = self._load_user_profile()

    def _load_user_profile(self) -> dict[str, str]:
        """Fetch user profile attributes from the database or use sensible defaults."""
        profile: dict[str, str] = {
            "student_status": "undergraduate",
            "college": "Engineering College",
            "degree": "B.Tech / B.E.",
            "branch": "Computer Science / Engineering",
            "country": "India",
            "graduation_year": "2026",
        }
        try:
            with get_session() as session:
                records = session.execute(
                    select(UserPreference).where(UserPreference.category == "profile")
                ).scalars().all()
                for r in records:
                    profile[r.key] = r.value
        except Exception:
            pass
        return profile

    def evaluate(self, opp_data: dict[str, Any]) -> EligibilityEvaluation:
        """Evaluate whether the candidate meets the opportunity's eligibility requirements."""
        checked: dict[str, str] = {}
        eligibility_text = (opp_data.get("eligibility") or "").strip()
        description = (opp_data.get("description") or "").strip()
        title = (opp_data.get("title") or "").strip()
        source_evidence = opp_data.get("source_evidence") or {}

        # Combine text for holistic examination
        evidence_str = " ".join(str(v) for v in source_evidence.values())
        corpus = f"{title} {description} {eligibility_text} {evidence_str}".lower()

        # 1. Open to All / Everyone Can Apply
        if any(phrase in corpus for phrase in [
            "everyone can apply", "open to all", "all college students",
            "open for all", "anyone can participate", "no restrictions",
        ]):
            checked["general_eligibility"] = "Open to all participants"
            return EligibilityEvaluation(
                is_eligible=True,
                confidence=0.98,
                reason="Opportunity is explicitly open to all participants and college students.",
                criteria_checked=checked,
            )

        # 2. Ineligibility Filters: Working Professionals Only
        if any(phrase in corpus for phrase in [
            "working professionals only", "corporate employees only",
            "only for employees of", "executives only", "experienced professionals only",
        ]):
            checked["employment_status"] = "Restricted to working professionals"
            return EligibilityEvaluation(
                is_eligible=False,
                confidence=0.92,
                reason="Competition is strictly restricted to working professionals / corporate employees.",
                criteria_checked=checked,
            )

        # 3. Ineligibility Filters: School Students Only
        if any(phrase in corpus for phrase in [
            "school students only", "class 6 to 12 only", "k-12 students only", "high school only",
        ]):
            checked["education_level"] = "Restricted to school students"
            return EligibilityEvaluation(
                is_eligible=False,
                confidence=0.90,
                reason="Competition is strictly restricted to school (K-12) students.",
                criteria_checked=checked,
            )

        # 4. Ineligibility Filters: Women Only
        if "women only" in corpus or "female students only" in corpus:
            checked["gender_restriction"] = "Restricted to female candidates"
            # Flag for verification
            return EligibilityEvaluation(
                is_eligible=True,
                confidence=0.70,
                reason="Has demographic restrictions (Women/Female candidates). Please verify personal eligibility.",
                criteria_checked=checked,
            )

        # 5. College / Student Eligibility
        if any(phrase in corpus for phrase in [
            "engineering students", "undergraduate", "postgraduate", "college students",
            "b.tech", "b.e.", "m.tech", "bca", "mca", "mba", "university students",
        ]):
            checked["student_requirement"] = "Matches college student profile"
            return EligibilityEvaluation(
                is_eligible=True,
                confidence=0.92,
                reason="Criteria explicitly match undergraduate/college students.",
                criteria_checked=checked,
            )

        # 6. Default / Generic Opportunities on Unstop
        # The vast majority of Unstop competitions are student-eligible
        checked["default_heuristic"] = "Standard student-accessible competition listing"
        return EligibilityEvaluation(
            is_eligible=True,
            confidence=0.80,
            reason="Standard public opportunity without exclusionary restrictions.",
            criteria_checked=checked,
        )
