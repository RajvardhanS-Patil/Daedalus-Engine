"""
Unstop Autopilot — Opportunity Suitability Scorer

Evaluates opportunities on a 0-100 scale using multi-factor weighted scoring.
Configurable weights prioritize free, individual, online opportunities with
verified certificates and clear eligibility matches.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class ScoreBreakdown:
    """Detailed score report showing points awarded per criterion."""
    individual_participation: int = 0
    free_registration: int = 0
    certificate_available: int = 0
    online_participation: int = 0
    eligibility_match: int = 0
    reasonable_time_commitment: int = 0
    deadline_suitability: int = 0

    total_score: int = 0
    max_possible: int = 100
    confidence_factor: float = 1.0
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": self.total_score,
            "max_possible": self.max_possible,
            "confidence_factor": round(self.confidence_factor, 2),
            "breakdown": {
                "individual_participation": self.individual_participation,
                "free_registration": self.free_registration,
                "certificate_available": self.certificate_available,
                "online_participation": self.online_participation,
                "eligibility_match": self.eligibility_match,
                "reasonable_time_commitment": self.reasonable_time_commitment,
                "deadline_suitability": self.deadline_suitability,
            },
            "reasoning": self.reasoning,
        }


class OpportunityScorer:
    """Weighted suitability scoring engine for competition opportunities."""

    def __init__(self) -> None:
        self.weights = settings.scoring

    def score(
        self,
        opp_data: dict[str, Any],
        is_eligible: bool | None = None,
        eligibility_confidence: float = 1.0,
    ) -> ScoreBreakdown:
        """Compute suitability score (0-100) for an opportunity."""
        report = ScoreBreakdown()
        unknown_fields_count = 0

        # 1. Individual vs Team Participation (Weight: 20)
        max_p = self.weights.individual_participation
        part_type = (opp_data.get("participation_type") or "").lower()
        part_count = (opp_data.get("participants_count") or "").lower()
        full_text = f"{part_type} {part_count}"

        if "individual" in full_text or "solo" in full_text or "1 member" in full_text:
            report.individual_participation = max_p
            report.reasoning.append("Solo/Individual participation (+20)")
        elif "2 member" in full_text or "duo" in full_text:
            report.individual_participation = int(max_p * 0.6)
            report.reasoning.append("Small team (2 members) (+12)")
        elif any(w in full_text for w in ["team", "members", "group"]):
            report.individual_participation = int(max_p * 0.3)
            report.reasoning.append("Team participation required (+6)")
        else:
            report.individual_participation = int(max_p * 0.35)
            unknown_fields_count += 1
            report.reasoning.append("Participation type unknown (+7 default)")

        # 2. Free Registration (Weight: 20)
        max_f = self.weights.free_registration
        fee_str = (opp_data.get("fee") or "unknown").lower()
        if any(w in fee_str for w in ["free", "0", "nil"]):
            report.free_registration = max_f
            report.reasoning.append("Free registration verified (+20)")
        elif any(char.isdigit() for char in fee_str) and not any(w in fee_str for w in ["free", "0"]):
            report.free_registration = 0
            report.reasoning.append("Paid registration (+0)")
        else:
            report.free_registration = int(max_f * 0.4)
            unknown_fields_count += 1
            report.reasoning.append("Registration fee unverified (+8 default)")

        # 3. Certificate Availability (Weight: 15)
        max_c = self.weights.certificate_available
        cert_info = (opp_data.get("certificate_info") or "").lower()
        if any(w in cert_info for w in ["yes", "provided", "certificate", "participation certificate"]):
            report.certificate_available = max_c
            report.reasoning.append("Certificate explicitly provided (+15)")
        elif "no" in cert_info:
            report.certificate_available = 0
            report.reasoning.append("No certificate provided (+0)")
        else:
            report.certificate_available = int(max_c * 0.4)
            unknown_fields_count += 1
            report.reasoning.append("Certificate availability unknown (+6 default)")

        # 4. Online Participation (Weight: 15)
        max_m = self.weights.online_participation
        mode_str = (opp_data.get("mode") or "").lower()
        if "online" in mode_str or "virtual" in mode_str:
            report.online_participation = max_m
            report.reasoning.append("100% Online participation (+15)")
        elif "hybrid" in mode_str:
            report.online_participation = int(max_m * 0.55)
            report.reasoning.append("Hybrid mode (+8)")
        elif "offline" in mode_str or "in-person" in mode_str:
            report.online_participation = int(max_m * 0.15)
            report.reasoning.append("Offline physical attendance (+2)")
        else:
            report.online_participation = int(max_m * 0.4)
            unknown_fields_count += 1
            report.reasoning.append("Mode unverified (+6 default)")

        # 5. Eligibility Match (Weight: 15)
        max_e = self.weights.eligibility_match
        if is_eligible is True:
            report.eligibility_match = int(max_e * eligibility_confidence)
            report.reasoning.append(f"Eligibility match confirmed (+{report.eligibility_match})")
        elif is_eligible is False:
            report.eligibility_match = 0
            report.reasoning.append("Ineligible based on profile restrictions (+0)")
        else:
            report.eligibility_match = int(max_e * 0.45)
            unknown_fields_count += 1
            report.reasoning.append("Eligibility unknown / unverified (+7 default)")

        # 6. Reasonable Time Commitment (Weight: 10)
        max_t = self.weights.reasonable_time_commitment
        cat_str = (opp_data.get("category") or "").lower()
        rounds = (opp_data.get("num_rounds") or "").lower()
        if "quiz" in cat_str:
            report.reasonable_time_commitment = max_t
            report.reasoning.append("Quick quiz format (low time commitment) (+10)")
        elif "competition" in cat_str or "hackathon" in cat_str:
            report.reasonable_time_commitment = int(max_t * 0.8)
            report.reasoning.append("Standard competition format (+8)")
        else:
            report.reasonable_time_commitment = int(max_t * 0.6)
            report.reasoning.append("Moderate commitment (+6)")

        # 7. Deadline Suitability (Weight: 5)
        max_d = self.weights.deadline_suitability
        deadline_str = (opp_data.get("deadline") or "").lower()
        days_match = re.search(r"(\d+)\s*days?\s*left", deadline_str)
        month_match = re.search(r"(\d+)\s*months?\s*left", deadline_str)
        hours_match = re.search(r"(\d+)\s*hours?\s*left", deadline_str)

        if month_match or (days_match and int(days_match.group(1)) >= 7):
            report.deadline_suitability = max_d
            report.reasoning.append("Ample registration window (> 7 days) (+5)")
        elif days_match and 3 <= int(days_match.group(1)) < 7:
            report.deadline_suitability = int(max_d * 0.6)
            report.reasoning.append("Moderate registration window (3-7 days) (+3)")
        elif (days_match and int(days_match.group(1)) < 3) or hours_match:
            report.deadline_suitability = int(max_d * 0.2)
            report.reasoning.append("Urgent deadline (< 3 days) (+1)")
        else:
            report.deadline_suitability = int(max_d * 0.5)
            report.reasoning.append("Deadline unverified (+2)")

        # Calculate Raw Sum
        raw_total = (
            report.individual_participation
            + report.free_registration
            + report.certificate_available
            + report.online_participation
            + report.eligibility_match
            + report.reasonable_time_commitment
            + report.deadline_suitability
        )

        # Apply confidence discount if key fields are missing
        # If 3 or more fields are unknown, cap score to prevent false HIGH PRIORITY
        confidence = max(0.4, 1.0 - (unknown_fields_count * 0.12))
        report.confidence_factor = confidence

        if unknown_fields_count >= 3:
            final_score = min(raw_total, 65)
            report.reasoning.append(f"Score capped at 65 due to {unknown_fields_count} unknown fields")
        else:
            final_score = raw_total

        # Ineligible opportunities cannot score above 40
        if is_eligible is False:
            final_score = min(final_score, 30)

        report.total_score = max(0, min(100, int(final_score)))
        return report
