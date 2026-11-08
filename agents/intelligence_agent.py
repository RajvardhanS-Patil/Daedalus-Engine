"""
Unstop Autopilot — Opportunity Intelligence Agent

Uses local Qwen 2.5 7B via Ollama to perform deep semantic analysis,
extract structured parameters, evaluate candidate eligibility, score
suitability, and assign action classifications.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select

from agents.llm_client import OllamaClient
from config import settings
from database.connection import get_session
from database.models import ActivityLog, EligibilityResult, Opportunity
from intelligence.classification import classify_opportunity
from intelligence.eligibility_engine import EligibilityEngine
from intelligence.opportunity_scorer import OpportunityScorer

logger = logging.getLogger(__name__)


class LLMOpportunityExtraction(BaseModel):
    """Structured extraction format returned by Qwen 2.5 7B."""
    opportunity_name: str = Field(
        default="",
        validation_alias=AliasChoices("opportunity_name", "opportunity_title", "title", "name"),
        description="Official title of the opportunity",
    )
    category: str = Field(
        default="competition",
        validation_alias=AliasChoices("category", "type", "opportunity_type"),
        description="Category: competition, hackathon, quiz, case study, hiring, or workshop",
    )
    participation_type: str = Field(
        default="individual",
        validation_alias=AliasChoices("participation_type", "participation", "format", "team_or_individual"),
        description="individual or team",
    )
    team_size_min: int = Field(default=1, validation_alias=AliasChoices("team_size_min", "min_team_size", "min_members"))
    team_size_max: int = Field(default=1, validation_alias=AliasChoices("team_size_max", "max_team_size", "max_members"))
    registration_fee: str = Field(
        default="Free",
        validation_alias=AliasChoices("registration_fee", "fee", "price", "entry_fee"),
        description="Registration fee or 'Free'",
    )
    is_free: bool = Field(default=True, validation_alias=AliasChoices("is_free", "free"))
    certificate_available: bool = Field(
        default=True,
        validation_alias=AliasChoices("certificate_available", "has_certificate", "certificate", "certificate_provided"),
        description="True if certificate of participation or merit is mentioned",
    )
    certificate_details: str = Field(
        default="Participation Certificate",
        validation_alias=AliasChoices("certificate_details", "certificate_info", "certificate_type"),
    )
    format_type: str = Field(
        default="online",
        validation_alias=AliasChoices("format_type", "mode", "event_mode"),
        description="online, offline, or hybrid",
    )
    eligibility_summary: str = Field(
        default="Open to undergraduate students",
        validation_alias=AliasChoices("eligibility_summary", "eligibility", "criteria", "eligibility_criteria"),
        description="Eligibility criteria and student/college restrictions",
    )
    is_student_eligible: bool = Field(default=True, validation_alias=AliasChoices("is_student_eligible", "student_eligible"))
    number_of_rounds: int = Field(default=1, validation_alias=AliasChoices("number_of_rounds", "rounds", "num_rounds"))
    submission_requirements: str = Field(
        default="Online submission",
        validation_alias=AliasChoices("submission_requirements", "submission", "requirements"),
    )
    time_commitment_estimate: str = Field(
        default="Low to Moderate",
        validation_alias=AliasChoices("time_commitment_estimate", "time_commitment", "commitment"),
    )
    key_highlights: list[str] = Field(default_factory=list, validation_alias=AliasChoices("key_highlights", "highlights"))


class IntelligenceAgent:
    """Orchestrates AI analysis, eligibility verification, and scoring."""

    def __init__(self) -> None:
        self.llm = OllamaClient()
        self.scorer = OpportunityScorer()
        self.eligibility_engine = EligibilityEngine()

    def analyze_opportunity(self, opportunity_id: int) -> dict[str, Any]:
        """Perform full AI analysis on a single stored opportunity."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                return {"error": f"Opportunity #{opportunity_id} not found."}

            logger.info("Starting AI intelligence analysis on Opportunity #%d: '%s'", opp.id, opp.title)

            # Build rich prompt with all visible source evidence
            prompt = self._build_analysis_prompt(opp)
            system_prompt = (
                "You are an expert AI competition analyst for Unstop Autopilot. "
                "Analyze the competition metadata carefully. Extract accurate facts. "
                "Never invent unverified details. If an item is not specified, state that it is standard/unknown. "
                "Output valid JSON conforming to the requested schema."
            )

            # Execute Qwen 2.5 7B inference
            try:
                extraction = self.llm.generate_json(
                    prompt=prompt,
                    system=system_prompt,
                    response_model=LLMOpportunityExtraction,
                )
            except Exception as exc:
                logger.error("LLM inference failed for Opp #%d: %s", opp.id, exc)
                return {"error": f"LLM inference error: {exc}"}

            # Run eligibility engine
            opp_dict = {
                "title": opp.title,
                "description": opp.description,
                "eligibility": extraction.eligibility_summary,
                "source_evidence": opp.get_source_evidence(),
            }
            eligibility_eval = self.eligibility_engine.evaluate(opp_dict)

            # Run suitability scorer
            scoring_input = {
                "participation_type": extraction.participation_type,
                "participants_count": f"{extraction.team_size_min}-{extraction.team_size_max} members" if extraction.participation_type == "team" else "Individual",
                "fee": extraction.registration_fee,
                "certificate_info": extraction.certificate_details,
                "mode": extraction.format_type,
                "category": extraction.category,
                "deadline": opp.deadline,
                "num_rounds": str(extraction.number_of_rounds),
            }
            score_report = self.scorer.score(
                scoring_input,
                is_eligible=eligibility_eval.is_eligible,
                eligibility_confidence=eligibility_eval.confidence,
            )

            # Run classification
            classification = classify_opportunity(
                suitability_score=score_report.total_score,
                is_eligible=eligibility_eval.is_eligible,
                eligibility_confidence=eligibility_eval.confidence,
                confidence_factor=score_report.confidence_factor,
                status=opp.status,
            )

            # Persist results to Opportunity record
            opp.suitability_score = score_report.total_score
            opp.classification = classification.value
            opp.participation_type = extraction.participation_type
            opp.eligibility = extraction.eligibility_summary
            opp.num_rounds = str(extraction.number_of_rounds)
            opp.submission_requirements = extraction.submission_requirements
            opp.time_commitment = extraction.time_commitment_estimate
            opp.format_type = extraction.format_type
            opp.ai_analysis = json.dumps({
                "extraction": extraction.model_dump(),
                "scoring": score_report.to_dict(),
                "eligibility": eligibility_eval.to_dict(),
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            })
            opp.updated_at = datetime.now(timezone.utc)

            # Upsert EligibilityResult record
            existing_el = session.execute(
                select(EligibilityResult).where(EligibilityResult.opportunity_id == opp.id)
            ).scalar_one_or_none()

            if existing_el:
                existing_el.is_eligible = eligibility_eval.is_eligible
                existing_el.reason = eligibility_eval.reason
                existing_el.confidence = eligibility_eval.confidence
                existing_el.criteria_checked = json.dumps(eligibility_eval.criteria_checked)
                existing_el.evaluated_at = datetime.now(timezone.utc)
            else:
                new_el = EligibilityResult(
                    opportunity_id=opp.id,
                    is_eligible=eligibility_eval.is_eligible,
                    reason=eligibility_eval.reason,
                    confidence=eligibility_eval.confidence,
                    criteria_checked=json.dumps(eligibility_eval.criteria_checked),
                )
                session.add(new_el)

            # Activity log
            log = ActivityLog(
                action="opportunity_analyzed",
                details=f"Analyzed #{opp.id} '{opp.title[:30]}': Score={score_report.total_score}, Class={classification.value}",
                status="success",
                module="intelligence",
            )
            session.add(log)

            session.commit()

            return {
                "opportunity_id": opp.id,
                "title": opp.title,
                "score": score_report.total_score,
                "classification": classification.value,
                "is_eligible": eligibility_eval.is_eligible,
                "extraction": extraction.model_dump(),
                "scoring_breakdown": score_report.to_dict(),
            }

    def analyze_batch(
        self,
        limit: int = 10,
        only_unanalyzed: bool = True,
        callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Analyze a batch of opportunities sequentially with progress updates."""
        processed = 0
        errors = 0
        results: list[dict[str, Any]] = []

        with get_session() as session:
            query = select(Opportunity.id)
            if only_unanalyzed:
                query = query.where(Opportunity.suitability_score.is_(None))
            query = query.order_by(Opportunity.id.asc()).limit(limit)
            opp_ids = session.execute(query).scalars().all()

        total = len(opp_ids)
        if total == 0:
            return {"total": 0, "processed": 0, "message": "No unanalyzed opportunities found."}

        if callback:
            callback(f"Found {total} opportunities requiring AI evaluation. Starting sequential inference...")

        for idx, oid in enumerate(opp_ids, start=1):
            if callback:
                callback(f"[{idx}/{total}] Evaluating Opportunity #{oid} via Qwen 2.5 7B...")

            res = self.analyze_opportunity(oid)
            if "error" in res:
                errors += 1
                logger.warning("Batch analysis error on #%d: %s", oid, res["error"])
            else:
                processed += 1
                results.append(res)

        if callback:
            callback(f"Batch analysis complete: {processed} analyzed, {errors} errors.")

        return {
            "total_candidates": total,
            "processed": processed,
            "errors": errors,
            "results": results,
        }

    def _build_analysis_prompt(self, opp: Opportunity) -> str:
        """Construct the prompt containing all raw evidence for LLM evaluation."""
        evidence = opp.get_source_evidence()
        evidence_text = "\n".join(f"- {k}: {v}" for k, v in evidence.items())

        return f"""Analyze this opportunity listed on Unstop:

Opportunity Title: {opp.title}
Official URL: {opp.url}
Extracted Category: {opp.category}
Extracted Mode: {opp.mode}
Extracted Deadline: {opp.deadline}
Extracted Fee: {opp.fee}
Extracted Certificate Info: {opp.certificate_info}
Extracted Participants Count: {opp.participants_count}
Visible Description:
{opp.description or 'No full description available.'}

Scraped Evidence from Webpage:
{evidence_text}

Task:
Extract and evaluate the opportunity parameters. Return valid JSON using this exact structure:
{{
  "opportunity_name": "{opp.title}",
  "category": "competition",
  "participation_type": "individual",
  "team_size_min": 1,
  "team_size_max": 1,
  "registration_fee": "Free",
  "is_free": true,
  "certificate_available": true,
  "certificate_details": "Participation Certificate",
  "format_type": "online",
  "eligibility_summary": "Open to all undergraduate students",
  "is_student_eligible": true,
  "number_of_rounds": 1,
  "submission_requirements": "Online submission",
  "time_commitment_estimate": "Low to Moderate",
  "key_highlights": ["Highlight 1", "Highlight 2"]
}}"""
