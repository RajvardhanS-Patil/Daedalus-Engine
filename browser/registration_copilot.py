"""
Unstop Autopilot — Registration Copilot

Assisted browser-based registration copilot. Automates permitted routine actions
(profile prefill, requirement extraction), strictly enforces user approval
policies for consequential actions (payments, terms, team creation), and maintains
a comprehensive audit trail.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from playwright.sync_api import Page, TimeoutError as PwTimeoutError
from sqlalchemy import select

from browser.browser_manager import BrowserManager
from config import settings
from config.policies import (
    ApprovalLevel,
    get_policy,
    is_action_blocked,
    requires_approval,
    security_policy,
)
from database.connection import get_session
from database.models import ActivityLog, Opportunity, Registration, UserPreference

logger = logging.getLogger(__name__)


@dataclass
class RegistrationRequirements:
    """Parsed requirements from an opportunity's official registration interface."""
    opportunity_url: str
    opportunity_title: str = ""
    is_open: bool = True
    already_registered: bool = False
    is_free: bool = True
    fee_amount: str = "Free"
    participation_type: str = "individual"  # individual or team
    team_size_range: str = "1"
    required_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    prefill_data: dict[str, str] = field(default_factory=dict)
    terms_and_declarations: list[str] = field(default_factory=list)
    actions_requiring_approval: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_title": self.opportunity_title,
            "opportunity_url": self.opportunity_url,
            "is_open": self.is_open,
            "already_registered": self.already_registered,
            "is_free": self.is_free,
            "fee_amount": self.fee_amount,
            "participation_type": self.participation_type,
            "required_fields": self.required_fields,
            "missing_fields": self.missing_fields,
            "prefill_data": self.prefill_data,
            "terms_and_declarations": self.terms_and_declarations,
            "actions_requiring_approval": self.actions_requiring_approval,
        }


class RegistrationCopilot:
    """Browser-based assisted registration manager."""

    def __init__(self, browser_manager: BrowserManager | None = None) -> None:
        self.browser = browser_manager or BrowserManager()
        self.preapproved_fields = security_policy.preapproved_profile_fields

    def get_candidate_profile(self) -> dict[str, str]:
        """Fetch candidate prefill profile from SQLite user_preferences."""
        profile: dict[str, str] = {
            "name": "Student Candidate",
            "email": "",
            "phone": "",
            "college": "Engineering College",
            "degree": "B.Tech",
            "branch": "Computer Science",
            "graduation_year": "2026",
            "city": "Mumbai",
            "country": "India",
        }
        try:
            with get_session() as session:
                records = session.execute(
                    select(UserPreference).where(UserPreference.category == "profile")
                ).scalars().all()
                for r in records:
                    if r.key in self.preapproved_fields or r.key in profile:
                        profile[r.key] = r.value
        except Exception as exc:
            logger.warning("Could not load user profile: %s", exc)
        return profile

    def inspect_registration(
        self,
        opportunity_id: int,
        page: Page | None = None,
    ) -> RegistrationRequirements:
        """Navigate to opportunity page, inspect registration state and form requirements."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                raise ValueError(f"Opportunity #{opportunity_id} not found.")

        reqs = RegistrationRequirements(
            opportunity_url=opp.url,
            opportunity_title=opp.title,
        )

        close_page = False
        if page is None:
            if not self.browser.is_running():
                self.browser.launch(headless=True)
            page = self.browser.new_page()
            close_page = True

        try:
            logger.info("Inspecting registration requirements for '%s'...", opp.title)
            page.goto(opp.url, wait_until="domcontentloaded")
            time.sleep(2.0)  # Allow dynamic components to render

            page_text = page.inner_text("body") or ""

            # 1. Check if already registered
            if re.search(r"\b(already registered|registered|view registration)\b", page_text, re.IGNORECASE):
                # Verify CTA
                btn = page.query_selector("button:has-text('Registered'), button:has-text('View Registration')")
                if btn:
                    reqs.already_registered = True
                    reqs.is_open = False
                    return reqs

            # 2. Check if registration is open or closed
            if re.search(r"\b(registration closed|applications closed|deadline passed)\b", page_text, re.IGNORECASE):
                reqs.is_open = False

            # 3. Check Fee & Payment
            if any(w in (opp.fee or "").lower() for w in ["free", "0", "nil"]):
                reqs.is_free = True
                reqs.fee_amount = "Free"
            elif any(c.isdigit() for c in (opp.fee or "")):
                reqs.is_free = False
                reqs.fee_amount = opp.fee
                reqs.actions_requiring_approval.append({
                    "action": "register_paid",
                    "level": ApprovalLevel.STRICT_CONFIRM.value,
                    "detail": f"Registration fee required: {opp.fee}",
                })
            else:
                # Inspect page text for fee
                match = re.search(r"₹\s*(\d[\d,]*)", page_text)
                if match and int(match.group(1).replace(",", "")) > 0:
                    reqs.is_free = False
                    reqs.fee_amount = f"₹{match.group(1)}"
                    reqs.actions_requiring_approval.append({
                        "action": "register_paid",
                        "level": ApprovalLevel.STRICT_CONFIRM.value,
                        "detail": f"Registration fee required: {reqs.fee_amount}",
                    })
                else:
                    reqs.is_free = True
                    reqs.fee_amount = "Free"

            # 4. Check Participation Type
            part_str = f"{opp.participation_type} {opp.participants_count}".lower()
            if "team" in part_str and "individual" not in part_str:
                reqs.participation_type = "team"
                reqs.actions_requiring_approval.append({
                    "action": "create_team",
                    "level": ApprovalLevel.STRICT_CONFIRM.value,
                    "detail": f"Team participation required ({opp.participants_count})",
                })
            else:
                reqs.participation_type = "individual"

            # 5. Check Legal Terms / Declarations
            if re.search(r"\b(terms and conditions|agree to the rules|declaration)\b", page_text, re.IGNORECASE):
                reqs.terms_and_declarations.append("Terms and Conditions / Platform Competition Rules")
                reqs.actions_requiring_approval.append({
                    "action": "accept_terms",
                    "level": ApprovalLevel.STRICT_CONFIRM.value,
                    "detail": "Acceptance of competition terms & declarations",
                })

            # 6. Profile Prefill Mapping
            candidate = self.get_candidate_profile()
            expected_fields = ["name", "email", "phone", "college", "degree", "graduation_year"]
            reqs.required_fields = expected_fields

            for fld in expected_fields:
                val = candidate.get(fld, "").strip()
                if val:
                    reqs.prefill_data[fld] = val
                else:
                    reqs.missing_fields.append(fld)

            # Final consequential action always requires approval
            reqs.actions_requiring_approval.append({
                "action": "final_submission",
                "level": ApprovalLevel.CONFIRM.value if reqs.is_free else ApprovalLevel.STRICT_CONFIRM.value,
                "detail": "Final confirmation to submit registration",
            })

            return reqs

        finally:
            if close_page:
                try:
                    page.close()
                except Exception:
                    pass

    def execute_guided_registration(
        self,
        opportunity_id: int,
        confirm_callback: Callable[[dict[str, Any]], bool],
        headless: bool = False,
    ) -> dict[str, Any]:
        """Perform guided registration with visible browser and approval gates."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                return {"error": f"Opportunity #{opportunity_id} not found."}

        # Step 1: Inspect in headless/headed
        reqs = self.inspect_registration(opportunity_id)
        if reqs.already_registered:
            self._record_registration(opportunity_id, status="registered", notes="Already registered on Unstop")
            return {"status": "already_registered", "message": "You are already registered for this opportunity!"}

        if not reqs.is_open:
            return {"status": "closed", "message": "Registrations for this opportunity are closed."}

        # Step 2: Request user approval through callback
        approval_payload = reqs.to_dict()
        user_approved = confirm_callback(approval_payload)
        if not user_approved:
            logger.info("Registration aborted by user policy rejection for #%d", opportunity_id)
            return {"status": "aborted", "message": "Registration was cancelled by the user."}

        # Step 3: Open visible browser session for execution
        logger.info("Launching visible browser for guided registration on #%d...", opportunity_id)
        if not self.browser.is_running():
            self.browser.launch(headless=headless)

        page = self.browser.new_page()
        try:
            page.goto(opp.url, wait_until="domcontentloaded")
            time.sleep(2.0)

            # Check if user is logged in
            if not self.browser.check_login_status(page):
                logger.warning("User is not authenticated on Unstop.")
                return {
                    "status": "login_required",
                    "message": "You must log in to your Unstop account in the browser before registration can proceed.",
                }

            # Locate the Register button
            reg_btn = page.query_selector(
                "button:has-text('Register'), a:has-text('Register'), button:has-text('Apply Now'), a:has-text('Apply Now')"
            )
            if not reg_btn:
                return {
                    "status": "manual_action_needed",
                    "message": "Registration button not found automatically. The page is open in your browser for you to complete.",
                }

            # Click register button to open registration form/modal
            reg_btn.click()
            time.sleep(2.5)

            # Prefill known form fields if visible
            candidate = self.get_candidate_profile()
            self._autofill_form_inputs(page, candidate)

            # Check if registration is completed immediately
            time.sleep(2.0)
            if page.query_selector("button:has-text('Registered'), div:has-text('Registration Successful')"):
                self._record_registration(
                    opportunity_id,
                    status="registered",
                    notes="Assisted registration completed automatically.",
                    approved=True,
                )
                return {"status": "success", "message": "Registration verified and confirmed!"}

            # Return assisted status letting user finalize in the open browser
            self._record_registration(
                opportunity_id,
                status="pending",
                notes="Registration form opened and prefilled. Awaiting user final click.",
                approved=True,
            )
            return {
                "status": "in_progress",
                "message": "Registration form pre-filled and opened in browser! Please review and click the final submit button in the browser.",
            }

        except Exception as exc:
            logger.error("Guided registration error on #%d: %s", opportunity_id, exc)
            return {"status": "error", "message": str(exc)}

    def _autofill_form_inputs(self, page: Page, profile: dict[str, str]) -> None:
        """Safely prefill standard text inputs matching preapproved profile fields."""
        for field_name, value in profile.items():
            if not value or field_name not in self.preapproved_fields:
                continue

            selectors = [
                f"input[name*='{field_name}' i]",
                f"input[placeholder*='{field_name}' i]",
                f"input[id*='{field_name}' i]",
            ]
            for sel in selectors:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible() and not el.input_value():
                        el.fill(value)
                        logger.debug("Prefilled field '%s' with '%s'", field_name, value)
                        break
                except Exception:
                    pass

    def _record_registration(
        self,
        opportunity_id: int,
        status: str = "registered",
        notes: str = "",
        approved: bool = True,
    ) -> None:
        """Update database registration table and write audit activity log."""
        try:
            with get_session() as session:
                existing = session.execute(
                    select(Registration).where(Registration.opportunity_id == opportunity_id)
                ).scalar_one_or_none()

                if existing:
                    existing.status = status
                    existing.notes = notes
                    existing.approval_given = approved
                    if status == "registered":
                        existing.registered_at = datetime.now(timezone.utc)
                else:
                    reg = Registration(
                        opportunity_id=opportunity_id,
                        status=status,
                        registration_method="assisted",
                        notes=notes,
                        approval_given=approved,
                        registered_at=datetime.now(timezone.utc) if status == "registered" else None,
                    )
                    session.add(reg)

                log = ActivityLog(
                    action=f"registration_{status}",
                    details=f"Opportunity #{opportunity_id} registration status: {status}. {notes}",
                    status="success" if status == "registered" else "warning",
                    module="registration",
                )
                session.add(log)
                session.commit()
                logger.info("Recorded registration status '%s' for Opp #%d", status, opportunity_id)
        except Exception as exc:
            logger.error("Failed to record registration in database: %s", exc)
