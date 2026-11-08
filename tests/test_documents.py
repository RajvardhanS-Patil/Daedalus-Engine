"""
Unit tests for Presentation & Document Studio
Verifies PPTX generation, PDF brief generation, and DocumentAgent persistence.
"""

import os
import uuid
from pathlib import Path

import pytest
from pptx import Presentation

from agents.document_agent import DocumentAgent
from database.connection import get_session
from database.models import GeneratedDocument, Opportunity
from documents.pdf_generator import BriefContent, PDFReportGenerator, ReportSection
from documents.ppt_generator import DeckContent, PowerPointGenerator, SlideData


def test_pptx_generation(tmp_path):
    """Verify PowerPointGenerator creates a valid 16:9 widescreen presentation."""
    gen = PowerPointGenerator(theme="dark")
    out_file = tmp_path / "test_deck.pptx"

    content = DeckContent(
        title="AI Automation Framework",
        subtitle="Autonomous Competition Agent",
        presenter_name="Student Team",
        competition_name="National Hackathon 2026",
        date_str="November 2026",
        slides=[
            SlideData(
                title="Problem Overview",
                subtitle="Key Challenges",
                cards=[
                    {"title": "Card 1", "content": "Challenge details here"},
                    {"title": "Card 2", "content": "Additional details"},
                ],
            ),
            SlideData(
                title="Key Deliverables",
                bullet_points=["Deliverable 1", "Deliverable 2", "Deliverable 3"],
            ),
        ],
    )

    path_str = gen.generate_deck(content, out_file)
    assert os.path.exists(path_str)
    assert os.path.getsize(path_str) > 0

    # Validate with python-pptx
    prs = Presentation(path_str)
    assert len(prs.slides) == 3  # 1 title + 2 content
    # Widescreen check (13.333 inches = 12192000 emus)
    assert prs.slide_width > 12000000


def test_pdf_brief_generation(tmp_path):
    """Verify PDFReportGenerator builds a valid, structured PDF document."""
    pdf_gen = PDFReportGenerator()
    out_file = tmp_path / "test_brief.pdf"

    brief = BriefContent(
        title="Competition Strategic Brief",
        subtitle="Opportunity Analysis",
        competition_name="Unstop National Innovation Challenge",
        author="Tester",
        date_str="November 2026",
        overview="High priority competition targeting machine learning and agentic workflows.",
        metadata={"Category": "Hackathon", "Mode": "Online", "Fee": "Free"},
        sections=[
            ReportSection(
                heading="Key Milestones",
                body="Structured phases for project execution.",
                table_data=[
                    ["Milestone", "Target", "Status"],
                    ["Design", "Week 1", "Completed"],
                    ["Testing", "Week 2", "Pending"],
                ],
            ),
            ReportSection(
                heading="Requirements Checklist",
                bullets=["Requirement A", "Requirement B", "Requirement C"],
            ),
        ],
    )

    path_str = pdf_gen.generate_brief_pdf(brief, out_file)
    assert os.path.exists(path_str)
    assert os.path.getsize(path_str) > 1000  # Non-trivial PDF size


def test_document_agent_integration():
    """Verify DocumentAgent synthesizes PPTX & PDF for an Opportunity and saves to DB."""
    # Create test opportunity
    with get_session() as session:
        opp = Opportunity(
            title="Autonomous Robotics Challenge 2026",
            url=f"https://unstop.com/competitions/robotics-{uuid.uuid4().hex[:6]}",
            category="competition",
            description="Autonomous navigation and computer vision robotics championship.",
            status="open",
            suitability_score=88,
            classification="HIGH PRIORITY",
        )
        session.add(opp)
        session.commit()
        opp_id = opp.id

    agent = DocumentAgent()

    # Generate PPTX
    pptx_doc = agent.generate_presentation_for_opportunity(opp_id, theme="dark")
    assert pptx_doc.id is not None
    assert pptx_doc.doc_type == "pptx"
    assert os.path.exists(pptx_doc.file_path)

    # Generate PDF Brief
    pdf_doc = agent.generate_brief_for_opportunity(opp_id)
    assert pdf_doc.id is not None
    assert pdf_doc.doc_type == "pdf"
    assert os.path.exists(pdf_doc.file_path)

    # Verify query
    docs = agent.get_generated_documents(opp_id)
    assert len(docs) >= 2
    types = [d.doc_type for d in docs]
    assert "pptx" in types
    assert "pdf" in types
