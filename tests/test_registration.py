"""
Tests for Registration Copilot and Policy Approvals
"""

import pytest
from agents.registration_agent import RegistrationAgent
from browser.registration_copilot import RegistrationCopilot, RegistrationRequirements
from config.policies import ApprovalLevel, get_policy, is_action_blocked, requires_approval
from database.connection import get_session
from database.models import Opportunity, Registration, UserPreference


def test_registration_policies():
    """Verify security policies require approvals for paid, terms, and final submissions."""
    assert is_action_blocked("store_password") is True
    assert is_action_blocked("bypass_captcha") is True

    paid_policy = get_policy("register_paid")
    assert paid_policy is not None
    assert paid_policy.approval_level == ApprovalLevel.STRICT_CONFIRM
    assert requires_approval("register_paid") is True

    terms_policy = get_policy("accept_terms")
    assert terms_policy is not None
    assert terms_policy.approval_level == ApprovalLevel.STRICT_CONFIRM

    final_sub_policy = get_policy("final_submission")
    assert final_sub_policy is not None
    assert requires_approval("final_submission") is True


def test_candidate_profile_persistence():
    """Verify storing and retrieving candidate prefill profile preferences."""
    agent = RegistrationAgent()
    agent.update_profile_field("college", "IIT Bombay")
    agent.update_profile_field("graduation_year", "2027")

    profile = agent.get_candidate_profile()
    assert profile["college"] == "IIT Bombay"
    assert profile["graduation_year"] == "2027"


def test_record_registration_persistence():
    """Verify recording and querying registrations in SQLite database."""
    agent = RegistrationAgent()

    import uuid
    test_url = f"https://unstop.com/hackathons/reg-test-{uuid.uuid4()}"
    with get_session() as session:
        opp = Opportunity(
            title="Registration Test Hackathon",
            url=test_url,
            fee="Free",
            category="hackathon",
            status="open",
            classification="HIGH PRIORITY",
            suitability_score=90,
        )
        session.add(opp)
        session.commit()
        opp_id = opp.id

    # Record registration
    copilot = RegistrationCopilot()
    copilot._record_registration(
        opportunity_id=opp_id,
        status="registered",
        notes="Automated test registration",
        approved=True,
    )

    # Verify query
    regs = agent.get_active_registrations()
    matching = [r for r in regs if r["opportunity_id"] == opp_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "registered"
    assert matching[0]["title"] == "Registration Test Hackathon"


def test_registration_candidates_query():
    """Verify get_registration_candidates filters out already registered opportunities."""
    agent = RegistrationAgent()
    candidates = agent.get_registration_candidates()
    # No candidate should have a status of registered
    for c in candidates:
        for reg in c.registrations:
            assert reg.status != "registered"
