"""
Unstop Autopilot — Registration Agent

High-level agent coordinating candidate profile preferences, eligible
opportunity selection, guided browser registration, and audit records.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from browser.registration_copilot import RegistrationCopilot, RegistrationRequirements
from database.connection import get_session
from database.models import Opportunity, Registration, UserPreference

logger = logging.getLogger(__name__)


class RegistrationAgent:
    """Agent orchestrating assisted registrations and candidate profile management."""

    def __init__(self) -> None:
        self.copilot = RegistrationCopilot()

    def get_candidate_profile(self) -> dict[str, str]:
        """Fetch all stored profile fields from SQLite."""
        return self.copilot.get_candidate_profile()

    def update_profile_field(self, key: str, value: str) -> None:
        """Update or insert a profile preference field."""
        with get_session() as session:
            pref = session.execute(
                select(UserPreference)
                .where(UserPreference.key == key)
                .where(UserPreference.category == "profile")
            ).scalar_one_or_none()

            if pref:
                pref.value = value
                pref.updated_at = datetime.now(timezone.utc)
            else:
                new_pref = UserPreference(
                    key=key,
                    value=value,
                    category="profile",
                )
                session.add(new_pref)
            session.commit()
            logger.info("Updated candidate profile field '%s'", key)

    def get_registration_candidates(self) -> list[Opportunity]:
        """Fetch opportunities that are high/good priority and not yet registered."""
        with get_session() as session:
            # Query opportunities with high priority or good opportunity, excluding registered ones
            subquery = select(Registration.opportunity_id).where(
                Registration.status.in_(["registered", "pending"])
            )
            query = (
                select(Opportunity)
                .options(joinedload(Opportunity.registrations))
                .where(Opportunity.status == "open")
                .where(Opportunity.classification.in_(["HIGH PRIORITY", "GOOD OPPORTUNITY"]))
                .where(Opportunity.id.not_in(subquery))
                .order_by(Opportunity.suitability_score.desc().nullslast())
            )
            return session.execute(query).unique().scalars().all()

    def get_active_registrations(self) -> list[dict[str, Any]]:
        """Fetch all current registration records with opportunity details."""
        results = []
        with get_session() as session:
            registrations = session.execute(
                select(Registration).order_by(Registration.id.desc())
            ).scalars().all()

            for reg in registrations:
                opp = reg.opportunity
                results.append({
                    "registration_id": reg.id,
                    "opportunity_id": reg.opportunity_id,
                    "title": opp.title if opp else "Unknown Opportunity",
                    "status": reg.status,
                    "registered_at": reg.registered_at.strftime("%Y-%m-%d %H:%M") if reg.registered_at else "Pending",
                    "method": reg.registration_method,
                    "notes": reg.notes,
                })
        return results

    def inspect_opportunity(self, opportunity_id: int) -> RegistrationRequirements:
        """Inspect opportunity registration requirements."""
        return self.copilot.inspect_registration(opportunity_id)

    def register_opportunity(
        self,
        opportunity_id: int,
        confirm_callback: Callable[[dict[str, Any]], bool],
        headless: bool = False,
    ) -> dict[str, Any]:
        """Execute guided registration workflow."""
        return self.copilot.execute_guided_registration(
            opportunity_id=opportunity_id,
            confirm_callback=confirm_callback,
            headless=headless,
        )
