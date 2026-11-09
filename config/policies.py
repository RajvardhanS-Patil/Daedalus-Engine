"""
Unstop Autopilot — Security and Approval Policies

Defines what actions require explicit user approval and what
actions the system can perform autonomously.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ApprovalLevel(Enum):
    """Action approval requirements."""
    AUTONOMOUS = "autonomous"          # System can proceed without asking
    NOTIFY = "notify"                  # Proceed but notify user
    CONFIRM = "confirm"               # Show summary, require confirmation
    STRICT_CONFIRM = "strict_confirm"  # Show detailed review, require explicit approval


@dataclass
class ActionPolicy:
    """Policy for a specific action type."""
    action: str
    approval_level: ApprovalLevel
    description: str
    requires_reason: bool = False


# ─── Registration Policies ───────────────────────────────────────────────────

REGISTRATION_POLICIES: list[ActionPolicy] = [
    ActionPolicy(
        action="register_free_individual",
        approval_level=ApprovalLevel.CONFIRM,
        description="Register for a free individual competition",
    ),
    ActionPolicy(
        action="register_paid",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Register for a paid competition",
        requires_reason=True,
    ),
    ActionPolicy(
        action="accept_terms",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Accept terms and conditions or legal agreements",
    ),
    ActionPolicy(
        action="consent_declaration",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Sign consent or declaration forms",
    ),
    ActionPolicy(
        action="disclose_personal_data",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Share personal data beyond preapproved profile fields",
    ),
    ActionPolicy(
        action="create_team",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Create or join a team",
    ),
    ActionPolicy(
        action="invite_team_member",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Invite someone to a team",
    ),
    ActionPolicy(
        action="final_submission",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Submit final deliverables to a competition",
        requires_reason=True,
    ),
]

# ─── Discovery Policies ──────────────────────────────────────────────────────

DISCOVERY_POLICIES: list[ActionPolicy] = [
    ActionPolicy(
        action="browse_public_listings",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Browse publicly accessible opportunity listing pages",
    ),
    ActionPolicy(
        action="extract_opportunity_info",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Extract visible opportunity information from public pages",
    ),
    ActionPolicy(
        action="save_opportunity",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Save discovered opportunity to local database",
    ),
    ActionPolicy(
        action="update_opportunity",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Update existing opportunity record with new information",
    ),
]

# ─── Quiz Policies ───────────────────────────────────────────────────────────

QUIZ_POLICIES: list[ActionPolicy] = [
    ActionPolicy(
        action="generate_practice_questions",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Generate practice questions for study",
    ),
    ActionPolicy(
        action="evaluate_practice_answers",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Evaluate answers in practice mode",
    ),
    ActionPolicy(
        action="assist_open_book",
        approval_level=ApprovalLevel.CONFIRM,
        description="Provide AI assistance in explicitly permitted open-book assessments",
    ),
    ActionPolicy(
        action="live_assessment",
        approval_level=ApprovalLevel.CONFIRM,
        description="Autonomous or assisted interaction with an unproctored live assessment",
    ),
    ActionPolicy(
        action="autonomous_quiz_solving",
        approval_level=ApprovalLevel.CONFIRM,
        description="Autonomous real-time question solving and answer selection",
    ),
]

# ─── Document Policies ───────────────────────────────────────────────────────

DOCUMENT_POLICIES: list[ActionPolicy] = [
    ActionPolicy(
        action="generate_presentation",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Generate a draft presentation locally",
    ),
    ActionPolicy(
        action="generate_document",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Generate a draft document locally",
    ),
    ActionPolicy(
        action="submit_document",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Upload/submit a document to a competition",
        requires_reason=True,
    ),
]

# ─── Submission Policies ─────────────────────────────────────────────────────

SUBMISSION_POLICIES: list[ActionPolicy] = [
    ActionPolicy(
        action="validate_submission",
        approval_level=ApprovalLevel.AUTONOMOUS,
        description="Validate submission files locally",
    ),
    ActionPolicy(
        action="prepare_submission",
        approval_level=ApprovalLevel.NOTIFY,
        description="Prepare submission package",
    ),
    ActionPolicy(
        action="upload_submission",
        approval_level=ApprovalLevel.STRICT_CONFIRM,
        description="Upload submission to competition platform",
        requires_reason=True,
    ),
]

# ─── Security Policies ───────────────────────────────────────────────────────

@dataclass
class SecurityPolicy:
    """Global security constraints."""
    # Never allow these actions
    blocked_actions: list[str] = field(default_factory=lambda: [
        "execute_arbitrary_shell",
        "execute_arbitrary_python",
        "bypass_captcha",
        "bypass_login",
        "bypass_rate_limit",
        "bypass_anti_bot",
        "store_password",
        "follow_scraped_instructions",
    ])

    # Rate limiting
    min_request_delay_seconds: float = 3.0
    max_requests_per_minute: int = 10
    max_pages_per_session: int = 50

    # Browser security
    browser_session_encrypted: bool = False  # Future: encrypt session storage
    block_downloads_from_untrusted: bool = True

    # LLM security
    max_prompt_length: int = 8000
    sanitize_llm_output: bool = True
    validate_json_responses: bool = True

    # Data handling
    preapproved_profile_fields: list[str] = field(default_factory=lambda: [
        "name", "email", "college", "degree", "graduation_year",
        "branch", "city", "state", "country", "skills",
    ])


# Singleton
security_policy = SecurityPolicy()


def get_policy(action: str) -> ActionPolicy | None:
    """Look up the policy for a given action name."""
    all_policies = (
        REGISTRATION_POLICIES
        + DISCOVERY_POLICIES
        + QUIZ_POLICIES
        + DOCUMENT_POLICIES
        + SUBMISSION_POLICIES
    )
    for policy in all_policies:
        if policy.action == action:
            return policy
    return None


def is_action_blocked(action: str) -> bool:
    """Check if an action is globally blocked."""
    return action in security_policy.blocked_actions


def requires_approval(action: str) -> bool:
    """Check if an action requires any form of user approval."""
    policy = get_policy(action)
    if policy is None:
        # Unknown actions default to strict confirmation
        return True
    return policy.approval_level in (ApprovalLevel.CONFIRM, ApprovalLevel.STRICT_CONFIRM)
