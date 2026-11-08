"""
Unit tests for Submission Validator and Human-in-the-Loop Policy Gates
"""

import os
import uuid
from pathlib import Path

import pytest
from database.connection import get_session
from database.models import Opportunity, Submission
from documents.ppt_generator import DeckContent, PowerPointGenerator, SlideData
from submissions.validator import SubmissionValidator, ValidationResult


@pytest.fixture
def sample_pptx(tmp_path):
    """Generate a sample presentation deck for validation testing."""
    gen = PowerPointGenerator()
    out = tmp_path / "valid_deck.pptx"
    content = DeckContent(
        title="Test Pitch",
        subtitle="Subtitle",
        presenter_name="Student",
        competition_name="Test Challenge",
        slides=[
            SlideData(title="Slide 1", bullet_points=["A", "B"]),
            SlideData(title="Slide 2", bullet_points=["C", "D"]),
            SlideData(title="Slide 3", bullet_points=["E", "F"]),
        ],
    )
    gen.generate_deck(content, out)
    return out


def test_validator_file_not_found():
    """Verify validator catches missing files."""
    validator = SubmissionValidator()
    res = validator.validate_file("/non/existent/file.pptx")
    assert res.is_valid is False
    assert any("does not exist" in e for e in res.errors)


def test_validator_empty_file(tmp_path):
    """Verify validator catches 0-byte empty files."""
    empty = tmp_path / "empty.pptx"
    empty.touch()
    validator = SubmissionValidator()
    res = validator.validate_file(empty)
    assert res.is_valid is False
    assert any("empty" in e for e in res.errors)


def test_validator_disallowed_extension(tmp_path):
    """Verify validator rejects files with forbidden extensions."""
    sh_file = tmp_path / "script.sh"
    sh_file.write_text("echo 'hello'")
    validator = SubmissionValidator()
    res = validator.validate_file(sh_file, allowed_extensions=[".pptx", ".pdf"])
    assert res.is_valid is False
    assert any("extension" in e for e in res.errors)


def test_validator_valid_pptx(sample_pptx):
    """Verify validator inspects valid PPTX structure and reports slide counts."""
    validator = SubmissionValidator()
    res = validator.validate_file(sample_pptx)
    assert res.is_valid is True
    assert res.checklist["file_exists"] is True
    assert res.checklist["extension_allowed"] is True
    assert res.checklist["size_within_limit"] is True
    assert res.checklist["content_parsable"] is True
    assert res.file_stats["slide_count"] == 4  # 1 title + 3 content


def test_submission_policy_gate_enforcement(sample_pptx):
    """Verify final submission requires explicit human confirmation."""
    with get_session() as session:
        opp = Opportunity(
            title="Policy Gate Competition",
            url=f"https://unstop.com/competitions/policy-{uuid.uuid4().hex[:6]}",
            category="competition",
            status="open",
        )
        session.add(opp)
        session.commit()
        opp_id = opp.id

    validator = SubmissionValidator()

    # Attempting submission without user confirmation must raise PermissionError
    with pytest.raises(PermissionError) as exc_info:
        validator.record_submission(
            opportunity_id=opp_id,
            file_path=sample_pptx,
            confirmed_by_user=False,
        )
    assert "Action 'final_submission' requires explicit human confirmation" in str(exc_info.value)

    # With user confirmation, submission succeeds
    sub = validator.record_submission(
        opportunity_id=opp_id,
        file_path=sample_pptx,
        notes="Approved by user",
        confirmed_by_user=True,
    )
    assert sub.id is not None
    assert sub.status == "submitted"
    assert sub.verified is True

    # Verify querying
    submissions = validator.get_submissions(opp_id)
    assert len(submissions) == 1
    assert submissions[0]["status"] == "submitted"
    assert submissions[0]["verified"] is True
