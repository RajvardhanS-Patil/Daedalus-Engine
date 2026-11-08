"""
Unstop Autopilot — PDF Report & Brief Generator

Generates competition briefs, executive summary decks, and submission
documents using ReportLab with clean styling, typography, and metadata tables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)


@dataclass
class ReportSection:
    """A titled section of a PDF document."""
    heading: str
    body: str = ""
    bullets: list[str] = field(default_factory=list)
    table_data: list[list[str]] = field(default_factory=list)


@dataclass
class BriefContent:
    """High-level document content for a competition brief or proposal."""
    title: str
    subtitle: str
    competition_name: str
    author: str
    date_str: str
    overview: str
    metadata: dict[str, str] = field(default_factory=dict)
    sections: list[ReportSection] = field(default_factory=list)


class PDFReportGenerator:
    """Generates structured PDF documents for competitions."""

    def __init__(self) -> None:
        self.styles = getSampleStyleSheet()
        self._init_custom_styles()

    def _init_custom_styles(self) -> None:
        """Register custom typography styles."""
        self.doc_title_style = ParagraphStyle(
            "DocTitle",
            parent=self.styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=28,
            textColor=colors.HexColor("#0F172A"),
            alignment=0,
            spaceAfter=6,
        )

        self.doc_subtitle_style = ParagraphStyle(
            "DocSubtitle",
            parent=self.styles["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#64748B"),
            spaceAfter=14,
        )

        self.h1_style = ParagraphStyle(
            "Heading1_Custom",
            parent=self.styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            textColor=colors.HexColor("#1E3A8A"),
            spaceBefore=14,
            spaceAfter=8,
        )

        self.body_style = ParagraphStyle(
            "Body_Custom",
            parent=self.styles["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#334155"),
            spaceAfter=8,
        )

        self.bullet_style = ParagraphStyle(
            "Bullet_Custom",
            parent=self.styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#1E293B"),
            leftIndent=16,
            spaceAfter=4,
        )

    def generate_brief_pdf(self, content: BriefContent, output_path: str | Path) -> str:
        """Compile and generate the PDF document."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            str(out_file),
            pagesize=letter,
            leftMargin=40,
            rightMargin=40,
            topMargin=40,
            bottomMargin=40,
        )

        story: list[Any] = []

        # 1. Header Banner
        header_text = f"<b>UNSTOP AUTOPILOT BRIEF</b> | {content.competition_name.upper()}"
        story.append(Paragraph(header_text, self.doc_subtitle_style))
        story.append(Spacer(1, 4))

        # 2. Document Title & Subtitle
        story.append(Paragraph(content.title, self.doc_title_style))
        if content.subtitle:
            story.append(Paragraph(content.subtitle, self.doc_subtitle_style))

        # Thin dividing rule
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0284C7"), spaceAfter=14))

        # 3. Metadata Table (Author, Date, Category, Score, etc.)
        if content.metadata:
            meta_rows = []
            keys = list(content.metadata.keys())
            for i in range(0, len(keys), 2):
                k1 = keys[i]
                v1 = content.metadata[k1]
                row = [f"<b>{k1}:</b>", v1]
                if i + 1 < len(keys):
                    k2 = keys[i + 1]
                    v2 = content.metadata[k2]
                    row.extend([f"<b>{k2}:</b>", v2])
                else:
                    row.extend(["", ""])
                meta_rows.append(row)

            # Format meta table
            formatted_rows = [
                [Paragraph(cell, self.body_style) for cell in r] for r in meta_rows
            ]
            meta_table = Table(formatted_rows, colWidths=[100, 160, 100, 160])
            meta_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ])
            )
            story.append(meta_table)
            story.append(Spacer(1, 14))

        # 4. Overview Section
        if content.overview:
            story.append(Paragraph("Executive Overview", self.h1_style))
            story.append(Paragraph(content.overview, self.body_style))
            story.append(Spacer(1, 10))

        # 5. Additional Dynamic Sections
        for sec in content.sections:
            sec_elements = []
            sec_elements.append(Paragraph(sec.heading, self.h1_style))
            if sec.body:
                sec_elements.append(Paragraph(sec.body, self.body_style))

            for bullet in sec.bullets:
                sec_elements.append(Paragraph(f"•  {bullet}", self.bullet_style))

            if sec.table_data:
                sec_elements.append(Spacer(1, 6))
                tbl_rows = [
                    [Paragraph(f"<b>{cell}</b>" if r_idx == 0 else cell, self.body_style) for cell in row]
                    for r_idx, row in enumerate(sec.table_data)
                ]
                custom_table = Table(tbl_rows, colWidths=[130] + [390 // (len(sec.table_data[0]) - 1)] * (len(sec.table_data[0]) - 1))
                custom_table.setStyle(
                    TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ])
                )
                sec_elements.append(custom_table)

            sec_elements.append(Spacer(1, 12))
            story.append(KeepTogether(sec_elements))

        # Build document
        doc.build(story)
        logger.info("Generated PDF report at '%s'", out_file)
        return str(out_file)
