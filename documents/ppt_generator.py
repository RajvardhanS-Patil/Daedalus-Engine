"""
Unstop Autopilot — PowerPoint Presentation Generator

Generates competition pitch and case study decks using python-pptx.
Features 16:9 widescreen layout, dark modern and clean light themes,
structured architectural cards, and roadmap timelines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

logger = logging.getLogger(__name__)


@dataclass
class SlideData:
    """Data content for a single slide."""
    title: str
    subtitle: str = ""
    bullet_points: list[str] = field(default_factory=list)
    cards: list[dict[str, str]] = field(default_factory=list)  # title, content
    notes: str = ""


@dataclass
class DeckContent:
    """Complete presentation structure."""
    title: str
    subtitle: str
    presenter_name: str
    competition_name: str
    date_str: str = ""
    slides: list[SlideData] = field(default_factory=list)


# Theme palettes
THEMES = {
    "dark": {
        "bg": RGBColor(15, 23, 42),        # #0F172A slate-900
        "card_bg": RGBColor(30, 41, 59),   # #1E293B slate-800
        "primary": RGBColor(6, 182, 212),  # #06B6D4 cyan-500
        "accent": RGBColor(59, 130, 246),  # #3B82F6 blue-500
        "text": RGBColor(248, 250, 252),   # #F8FAFC white
        "muted": RGBColor(148, 163, 184),  # #94A3B8 slate-400
    },
    "light": {
        "bg": RGBColor(255, 255, 255),
        "card_bg": RGBColor(241, 245, 249),
        "primary": RGBColor(37, 99, 235),
        "accent": RGBColor(14, 165, 233),
        "text": RGBColor(15, 23, 42),
        "muted": RGBColor(100, 116, 139),
    },
}


class PowerPointGenerator:
    """Generates styled PPTX presentations for competitions."""

    def __init__(self, theme: str = "dark") -> None:
        self.theme_name = theme if theme in THEMES else "dark"
        self.colors = THEMES[self.theme_name]

    def generate_deck(self, content: DeckContent, output_path: str | Path) -> str:
        """Create a complete widescreen PowerPoint presentation."""
        prs = Presentation()
        # 16:9 widescreen dimensions
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        blank_layout = prs.slide_layouts[6]

        # 1. Title Slide
        title_slide = prs.slides.add_slide(blank_layout)
        self._apply_background(title_slide)
        self._build_title_slide(title_slide, content)

        # 2. Content Slides
        for s_data in content.slides:
            slide = prs.slides.add_slide(blank_layout)
            self._apply_background(slide)
            self._build_content_slide(slide, s_data)

        # Save presentation
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(out_file))
        logger.info("Generated PowerPoint deck at '%s'", out_file)
        return str(out_file)

    def _apply_background(self, slide) -> None:
        """Draw full-screen background rectangle with theme color."""
        bg = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5)
        )
        bg.fill.solid()
        bg.fill.fore_color.rgb = self.colors["bg"]
        bg.line.fill.background()

    def _build_title_slide(self, slide, content: DeckContent) -> None:
        """Create a striking modern title slide."""
        # Cyan accent top bar
        bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(1.0), Inches(1.2), Inches(1.5), Inches(0.08)
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = self.colors["primary"]
        bar.line.fill.background()

        # Competition category pill
        comp_box = slide.shapes.add_textbox(Inches(1.0), Inches(1.4), Inches(11.3), Inches(0.5))
        tf_comp = comp_box.text_frame
        tf_comp.word_wrap = True
        p_comp = tf_comp.paragraphs[0]
        p_comp.text = content.competition_name.upper()
        p_comp.font.size = Pt(13)
        p_comp.font.bold = True
        p_comp.font.color.rgb = self.colors["primary"]

        # Main Title
        title_box = slide.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(11.3), Inches(2.2))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = content.title
        p.font.size = Pt(44)
        p.font.bold = True
        p.font.color.rgb = self.colors["text"]

        # Subtitle
        if content.subtitle:
            p_sub = tf.add_paragraph()
            p_sub.text = content.subtitle
            p_sub.font.size = Pt(20)
            p_sub.font.color.rgb = self.colors["muted"]
            p_sub.space_before = Pt(12)

        # Presenter & Date Footer
        footer_box = slide.shapes.add_textbox(Inches(1.0), Inches(5.8), Inches(11.3), Inches(0.8))
        tf_foot = footer_box.text_frame
        p_pres = tf_foot.paragraphs[0]
        p_pres.text = f"Candidate / Team: {content.presenter_name}"
        p_pres.font.size = Pt(14)
        p_pres.font.bold = True
        p_pres.font.color.rgb = self.colors["text"]

        if content.date_str:
            p_date = tf_foot.add_paragraph()
            p_date.text = f"Submission Date: {content.date_str}"
            p_date.font.size = Pt(12)
            p_date.font.color.rgb = self.colors["muted"]

    def _build_content_slide(self, slide, s_data: SlideData) -> None:
        """Create a structured slide with header, cards, or bullet points."""
        # Top Header
        header_box = slide.shapes.add_textbox(Inches(1.0), Inches(0.8), Inches(11.3), Inches(1.2))
        tf = header_box.text_frame
        tf.word_wrap = True
        p_title = tf.paragraphs[0]
        p_title.text = s_data.title
        p_title.font.size = Pt(28)
        p_title.font.bold = True
        p_title.font.color.rgb = self.colors["text"]

        if s_data.subtitle:
            p_sub = tf.add_paragraph()
            p_sub.text = s_data.subtitle
            p_sub.font.size = Pt(14)
            p_sub.font.color.rgb = self.colors["muted"]
            p_sub.space_before = Pt(4)

        # Horizontal accent divider
        div = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(1.0), Inches(2.1), Inches(11.3), Inches(0.02)
        )
        div.fill.solid()
        div.fill.fore_color.rgb = self.colors["card_bg"]
        div.line.fill.background()

        # If cards are provided, render as 3 horizontal column cards
        if s_data.cards:
            num_cards = min(len(s_data.cards), 3)
            card_width = Inches(3.5)
            spacing = Inches(0.4)
            left_base = Inches(1.0)
            top_base = Inches(2.5)
            card_height = Inches(4.2)

            for i, card_info in enumerate(s_data.cards[:3]):
                left = left_base + i * (card_width + spacing)
                # Card container
                card_shape = slide.shapes.add_shape(
                    MSO_SHAPE.ROUNDED_RECTANGLE, left, top_base, card_width, card_height
                )
                card_shape.fill.solid()
                card_shape.fill.fore_color.rgb = self.colors["card_bg"]
                card_shape.line.fill.background()

                # Card content
                tb = slide.shapes.add_textbox(left + Inches(0.2), top_base + Inches(0.2), card_width - Inches(0.4), card_height - Inches(0.4))
                c_tf = tb.text_frame
                c_tf.word_wrap = True

                cp_title = c_tf.paragraphs[0]
                cp_title.text = card_info.get("title", f"Point {i+1}")
                cp_title.font.size = Pt(18)
                cp_title.font.bold = True
                cp_title.font.color.rgb = self.colors["primary"]

                cp_body = c_tf.add_paragraph()
                cp_body.text = card_info.get("content", "")
                cp_body.font.size = Pt(13)
                cp_body.font.color.rgb = self.colors["text"]
                cp_body.space_before = Pt(10)

        # Otherwise render as rich bullet points
        elif s_data.bullet_points:
            content_box = slide.shapes.add_textbox(Inches(1.0), Inches(2.5), Inches(11.3), Inches(4.2))
            c_tf = content_box.text_frame
            c_tf.word_wrap = True

            for idx, pt in enumerate(s_data.bullet_points):
                p = c_tf.paragraphs[0] if idx == 0 else c_tf.add_paragraph()
                p.text = f"•  {pt}"
                p.font.size = Pt(18)
                p.font.color.rgb = self.colors["text"]
                p.space_before = Pt(16)
