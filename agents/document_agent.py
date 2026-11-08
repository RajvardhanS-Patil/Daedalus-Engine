"""
Unstop Autopilot — Document Studio Agent

Orchestrates automated generation of competition presentation decks (.pptx)
and executive briefs (.pdf) using local Qwen 2.5 7B and templates.
Records all generated artifacts in SQLite database.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from agents.llm_client import OllamaClient, OllamaError
from database.connection import get_session
from database.models import GeneratedDocument, Opportunity
from documents.pdf_generator import BriefContent, PDFReportGenerator, ReportSection
from documents.ppt_generator import DeckContent, PowerPointGenerator, SlideData

logger = logging.getLogger(__name__)


class LLMSlideSchema(BaseModel):
    """Pydantic schema for an LLM-generated slide."""
    title: str = Field(description="Slide heading")
    subtitle: str = Field(default="", description="Slide subheading or context")
    card_titles: list[str] = Field(default_factory=list, description="Titles for 2-3 cards")
    card_contents: list[str] = Field(default_factory=list, description="Body content for each card")
    bullet_points: list[str] = Field(default_factory=list, description="Alternative bullet points")


class LLMDeckSchema(BaseModel):
    """Pydantic schema for an LLM-generated pitch deck."""
    project_title: str = Field(description="Catchy project name")
    tagline: str = Field(description="Short one-line pitch")
    slides: list[LLMSlideSchema] = Field(description="List of content slides")


class DocumentAgent:
    """Agent that synthesizes presentation decks and PDF briefs for competitions."""

    def __init__(self, llm_client: OllamaClient | None = None) -> None:
        self.llm = llm_client or OllamaClient()
        self.ppt_generator = PowerPointGenerator()
        self.pdf_generator = PDFReportGenerator()
        self.output_dir = Path("data/generated").resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_presentation_for_opportunity(
        self,
        opportunity_id: int,
        theme: str = "dark",
        team_name: str = "Autonomous Innovator",
    ) -> GeneratedDocument:
        """Create a complete PowerPoint pitch deck tailored to an opportunity."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                raise ValueError(f"Opportunity ID {opportunity_id} not found")

            opp_title = opp.title
            opp_desc = opp.description or ""
            opp_cat = opp.category or "Competition"

        # Attempt AI synthesis of slide structure
        deck_content = self._synthesize_deck_content(opp_title, opp_desc, opp_cat, team_name)

        # Generate PPTX file
        clean_slug = re.sub(r"[^\w\-_]", "_", opp_title[:30]).strip("_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Pitch_{clean_slug}_{timestamp}.pptx"
        output_path = self.output_dir / filename

        self.ppt_generator.generate_deck(deck_content, output_path)

        # Record in database
        with get_session() as session:
            doc_record = GeneratedDocument(
                opportunity_id=opportunity_id,
                doc_type="pptx",
                title=f"Pitch Deck: {deck_content.title}",
                file_path=str(output_path),
                template_used=f"{theme}_modern",
                version=1,
                status="draft",
            )
            session.add(doc_record)
            session.commit()
            session.refresh(doc_record)
            logger.info("Persisted PPTX GeneratedDocument record ID=%d", doc_record.id)
            return doc_record

    def generate_brief_for_opportunity(
        self,
        opportunity_id: int,
        author: str = "Unstop Autopilot",
    ) -> GeneratedDocument:
        """Create an executive PDF brief and strategic preparation checklist."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                raise ValueError(f"Opportunity ID {opportunity_id} not found")

            opp_title = opp.title
            opp_desc = opp.description or "No description provided."
            opp_cat = opp.category or "Competition"
            deadline = opp.deadline or "Unknown"
            fee = opp.fee or "Free"
            mode = opp.mode or "Online"
            score = str(opp.suitability_score or "N/A")
            classification = opp.classification or "NEEDS VERIFICATION"

        # Build PDF brief structure
        brief = BriefContent(
            title=f"Strategic Brief: {opp_title[:55]}",
            subtitle="Autonomous Opportunity Analysis & Competition Playbook",
            competition_name=opp_title,
            author=author,
            date_str=datetime.now().strftime("%B %d, %Y"),
            overview=f"This strategic brief evaluates the opportunity '{opp_title}'. "
                     f"Classified as {classification} with a priority score of {score}/100. "
                     f"Preparation requirements and submission milestones are detailed below.",
            metadata={
                "Category": opp_cat.capitalize(),
                "Mode": mode.capitalize(),
                "Registration Fee": fee,
                "Deadline": deadline,
                "Suitability Score": f"{score} / 100",
                "Classification": classification,
            },
            sections=[
                ReportSection(
                    heading="Competition Overview & Scope",
                    body=opp_desc[:800],
                    bullets=[
                        f"Target Category: {opp_cat}",
                        f"Participation Mode: {mode}",
                        f"Opportunity Classification: {classification}",
                    ],
                ),
                ReportSection(
                    heading="Winning Strategy & Execution Milestones",
                    body="Key execution phases to maximize scoring and final submission quality:",
                    table_data=[
                        ["Phase", "Target Timeline", "Core Deliverable", "Verification"],
                        ["1. Research", "Days 1-2", "Problem decomposition & dataset review", "Scope Approval"],
                        ["2. Architecture", "Days 3-5", "Prototype system or pitch deck outline", "Deck Review"],
                        ["3. Synthesis", "Days 6-8", "Final pitch deck & documentation", "Pre-submission Check"],
                        ["4. Submission", "48h before deadline", "Final files uploaded to Unstop", "Confirmation Screen"],
                    ],
                ),
                ReportSection(
                    heading="Pre-Submission Checklist",
                    body="Mandatory compliance checks before finalizing submission:",
                    bullets=[
                        "Ensure candidate and team details match official registration.",
                        "Verify file format conforms strictly to competition rules (PPTX/PDF).",
                        "Ensure presentation slide count and file size stay within limits.",
                        "Double check all problem statements and evaluation criteria are addressed.",
                    ],
                ),
            ],
        )

        clean_slug = re.sub(r"[^\w\-_]", "_", opp_title[:30]).strip("_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Brief_{clean_slug}_{timestamp}.pdf"
        output_path = self.output_dir / filename

        self.pdf_generator.generate_brief_pdf(brief, output_path)

        # Record in database
        with get_session() as session:
            doc_record = GeneratedDocument(
                opportunity_id=opportunity_id,
                doc_type="pdf",
                title=f"Brief: {opp_title[:50]}",
                file_path=str(output_path),
                template_used="executive_brief",
                version=1,
                status="draft",
            )
            session.add(doc_record)
            session.commit()
            session.refresh(doc_record)
            logger.info("Persisted PDF GeneratedDocument record ID=%d", doc_record.id)
            return doc_record

    def get_generated_documents(self, opportunity_id: int | None = None) -> list[GeneratedDocument]:
        """Fetch all generated document records."""
        with get_session() as session:
            query = select(GeneratedDocument).order_by(GeneratedDocument.created_at.desc())
            if opportunity_id is not None:
                query = query.where(GeneratedDocument.opportunity_id == opportunity_id)
            return list(session.execute(query).scalars().all())

    def _synthesize_deck_content(
        self,
        opp_title: str,
        opp_desc: str,
        opp_cat: str,
        team_name: str,
    ) -> DeckContent:
        """Synthesize slide content using Qwen 2.5 7B with resilient deterministic fallback."""
        system_prompt = (
            "You are an elite competition coach and pitch deck architect. "
            "Given a competition title and description, design a compelling 5-slide pitch structure. "
            "Output strictly valid JSON matching the requested schema."
        )

        user_prompt = (
            f"Competition: {opp_title}\n"
            f"Category: {opp_cat}\n"
            f"Description: {opp_desc[:1200]}\n\n"
            "Generate a structured pitch deck with:\n"
            "1. Problem Statement slide (2-3 pain points as cards)\n"
            "2. Proposed Solution slide (2-3 feature components as cards)\n"
            "3. System Architecture slide (2-3 technical components)\n"
            "4. Market & Business Impact slide (2-3 impact metrics)\n"
            "5. Implementation Roadmap slide (Phases and deliverables)\n"
        )

        try:
            llm_deck = self.llm.generate_json(
                prompt=user_prompt,
                system=system_prompt,
                response_model=LLMDeckSchema,
            )
            if isinstance(llm_deck, LLMDeckSchema) and llm_deck.slides:
                slides = []
                for s in llm_deck.slides:
                    cards = []
                    for c_title, c_content in zip(s.card_titles, s.card_contents):
                        cards.append({"title": c_title, "content": c_content})
                    slides.append(
                        SlideData(
                            title=s.title,
                            subtitle=s.subtitle,
                            cards=cards,
                            bullet_points=s.bullet_points,
                        )
                    )
                return DeckContent(
                    title=llm_deck.project_title,
                    subtitle=llm_deck.tagline,
                    presenter_name=team_name,
                    competition_name=opp_title,
                    date_str=datetime.now().strftime("%B %d, %Y"),
                    slides=slides,
                )
        except (OllamaError, Exception) as err:
            logger.info("Using deterministic deck synthesis (LLM bypass: %s)", err)

        # Fallback deterministic pitch deck synthesis
        return self._fallback_deck_content(opp_title, opp_cat, team_name)

    def _fallback_deck_content(self, opp_title: str, opp_cat: str, team_name: str) -> DeckContent:
        """Deterministic pitch deck template ensuring high quality when offline."""
        project_title = f"{opp_title[:25]} Solution"
        tagline = f"Next-Generation Autonomous Framework for {opp_cat.capitalize()}"

        slides = [
            SlideData(
                title="Problem Statement & Market Gap",
                subtitle="Core inefficiencies and technical bottlenecks in current industry practices",
                cards=[
                    {"title": "High Manual Overhead", "content": "Manual workflows cause delays, inconsistent outcomes, and scalability limits."},
                    {"title": "Fragmented Tooling", "content": "Teams juggle disparate tools lacking unified intelligence and real-time coordination."},
                    {"title": "Data Latency", "content": "Delayed feedback loops prevent rapid competitive pivots and critical decision making."},
                ],
            ),
            SlideData(
                title="Proposed Solution Architecture",
                subtitle="Modular, autonomous, and high-performance system design",
                cards=[
                    {"title": "Autonomous Engine", "content": "Local AI reasoning delivers zero-latency insights and edge-native decision execution."},
                    {"title": "Unified Data Hub", "content": "Persistent ACID relational datastore ensuring complete auditability and integrity."},
                    {"title": "Human-in-the-Loop", "content": "Strict policy enforcement prevents consequential actions without user sign-off."},
                ],
            ),
            SlideData(
                title="Market Impact & Feasibility",
                subtitle="Quantifiable advantages and competitive benchmarks",
                cards=[
                    {"title": "10x Speedup", "content": "Drastically reduces preparation and synthesis cycles from days to minutes."},
                    {"title": "100% Privacy", "content": "Zero telemetry leakage and zero third-party vendor lock-in via local inference."},
                    {"title": "Scalable Model", "content": "Lightweight containerized architecture adaptable to diverse domains and formats."},
                ],
            ),
            SlideData(
                title="Implementation Roadmap",
                subtitle="Milestone execution plan from proof-of-concept to production",
                cards=[
                    {"title": "Sprint 1: Core Scaffolding", "content": "Scaffold architecture, relational models, and initial inference pipeline."},
                    {"title": "Sprint 2: Verification", "content": "Full test coverage, policy enforcement gates, and real-world trial runs."},
                    {"title": "Sprint 3: Final Launch", "content": "Packaged distribution, automated service unit, and submission sign-off."},
                ],
            ),
        ]

        return DeckContent(
            title=project_title,
            subtitle=tagline,
            presenter_name=team_name,
            competition_name=opp_title,
            date_str=datetime.now().strftime("%B %d, %Y"),
            slides=slides,
        )
