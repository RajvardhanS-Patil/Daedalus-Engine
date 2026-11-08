"""
Unit tests for CertificateManager and PointsTracker
"""

import uuid
from pathlib import Path

import pytest
from achievements.certificate_manager import CertificateManager
from achievements.points_tracker import PointsTracker
from database.connection import get_session
from database.models import Opportunity


def test_certificate_manager_lifecycle(tmp_path):
    """Verify adding, summarizing, and listing certificates."""
    cert_mgr = CertificateManager(cert_dir=tmp_path)

    # Add certificate with mock local file
    mock_cert_file = tmp_path / "mock_cert.pdf"
    mock_cert_file.write_text("%PDF-1.4 Mock Certificate Content")

    c1 = cert_mgr.add_certificate(
        name="Winner - National AI Hackathon 2026",
        issuer="Unstop",
        certificate_type="winner",
        local_path=str(mock_cert_file),
    )
    assert c1.id is not None
    assert c1.downloaded is True

    c2 = cert_mgr.add_certificate(
        name="Participant - Case Study Challenge",
        issuer="IIT Delhi",
        certificate_type="participation",
    )
    assert c2.id is not None
    assert c2.downloaded is False

    # Summary
    summary = cert_mgr.get_certificate_summary()
    assert summary["total_certificates"] >= 2
    assert summary["downloaded_locally"] >= 1
    assert "winner" in summary["by_type"]

    # Listing
    all_certs = cert_mgr.list_certificates()
    names = [c["name"] for c in all_certs]
    assert "Winner - National AI Hackathon 2026" in names


def test_certificate_manager_scan_archive(tmp_path):
    """Verify scanning directory for new unindexed certificate files."""
    cert_mgr = CertificateManager(cert_dir=tmp_path)

    # Place two new PDF files
    f1 = tmp_path / f"unindexed_{uuid.uuid4().hex[:6]}.pdf"
    f1.write_text("%PDF-1.4 Data")

    indexed = cert_mgr.scan_local_archive()
    assert indexed >= 1


def test_points_tracker_tiers_and_summary():
    """Verify points aggregation, rank tiers, and completion rewards."""
    tracker = PointsTracker()

    # Create opportunity
    with get_session() as session:
        opp = Opportunity(
            title="Algorithm Cup 2026",
            url=f"https://unstop.com/competitions/algo-{uuid.uuid4().hex[:6]}",
            category="competition",
            status="open",
        )
        session.add(opp)
        session.commit()
        opp_id = opp.id

    # Award completion points
    ach1 = tracker.record_competition_completion(
        opportunity_id=opp_id,
        points=120,
        badge_name="Algorithm Master",
    )
    assert ach1.id is not None
    assert ach1.value == 120
    assert ach1.verified is True

    # Check summary & rank progression
    summary = tracker.get_points_summary()
    assert summary["verified_points"] >= 120
    assert summary["current_tier"] in ["Bronze Competitor", "Silver Achiever", "Gold Champion", "Grandmaster"]
    assert summary["tier_progress_pct"] > 0
    assert "recent_achievements" in summary
