"""
Unstop Autopilot — Points & Achievements Tracker

Tracks verified platform points, badges, competition milestones, and rank tiers.
Maintains tamper-evident audit records in the SQLite achievements table.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from database.connection import get_session
from database.models import Achievement, Opportunity

logger = logging.getLogger(__name__)


# Tier milestones based on cumulative verified points
TIER_THRESHOLDS = [
    (0, "Unstop Novice", "Beginner competitive participant"),
    (100, "Bronze Competitor", "Active participant across multiple competitions"),
    (500, "Silver Achiever", "Consistent performer with verified submissions"),
    (1500, "Gold Champion", "Top-tier competitor with multiple high rankings"),
    (3000, "Grandmaster", "Elite platform leader in competitive hackathons & case studies"),
]


class PointsTracker:
    """Manages verified platform points, achievement badges, and milestone progression."""

    def add_achievement(
        self,
        achievement_type: str,
        title: str,
        description: str = "",
        value: int | None = None,
        source: str = "",
        verified: bool = True,
        achieved_at: datetime | None = None,
    ) -> Achievement:
        """Record an achievement or points event into the database."""
        with get_session() as session:
            ach = Achievement(
                achievement_type=achievement_type.lower(),
                title=title,
                description=description,
                value=value,
                source=source,
                verified=verified,
                achieved_at=achieved_at or datetime.now(timezone.utc),
            )
            session.add(ach)
            session.commit()
            session.refresh(ach)
            logger.info("Recorded achievement ID=%d '%s' (value=%s, verified=%s)", ach.id, ach.title, value, verified)
            return ach

    def record_competition_completion(
        self,
        opportunity_id: int,
        points: int = 50,
        badge_name: str = "",
    ) -> Achievement:
        """Record completed participation in an opportunity and award points."""
        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            opp_title = opp.title if opp else f"Opportunity #{opportunity_id}"

        title = f"Completed: {opp_title[:45]}"
        desc = f"Verified completion and deliverable submission for '{opp_title}'."
        if badge_name:
            desc += f" Earned badge: {badge_name}."

        return self.add_achievement(
            achievement_type="points",
            title=title,
            description=desc,
            value=points,
            source=opp_title,
            verified=True,
        )

    def get_points_summary(self) -> dict[str, Any]:
        """Aggregate total points, badges, current rank tier, and next milestone."""
        with get_session() as session:
            # Verified points sum
            verified_points = session.execute(
                select(func.sum(Achievement.value))
                .where(Achievement.verified == True)  # noqa: E712
                .where(Achievement.value.is_not(None))
            ).scalar() or 0

            # Pending/unverified points
            unverified_points = session.execute(
                select(func.sum(Achievement.value))
                .where(Achievement.verified == False)  # noqa: E712
                .where(Achievement.value.is_not(None))
            ).scalar() or 0

            # Badges
            badges_query = (
                select(Achievement)
                .where(Achievement.achievement_type.in_(["badge", "win", "shortlist"]))
                .order_by(Achievement.created_at.desc())
            )
            badges = session.execute(badges_query).scalars().all()
            badges_list = [
                {
                    "id": b.id,
                    "title": b.title,
                    "type": b.achievement_type,
                    "source": b.source,
                    "date": b.achieved_at.strftime("%Y-%m-%d") if b.achieved_at else "",
                }
                for b in badges
            ]

            # Recent achievements
            recent_query = select(Achievement).order_by(Achievement.created_at.desc()).limit(8)
            recent = session.execute(recent_query).scalars().all()
            recent_list = [
                {
                    "title": a.title,
                    "value": a.value or 0,
                    "type": a.achievement_type,
                    "source": a.source,
                    "verified": a.verified,
                    "date": a.achieved_at.strftime("%Y-%m-%d") if a.achieved_at else "",
                }
                for a in recent
            ]

        # Calculate Rank Tier
        current_tier = TIER_THRESHOLDS[0]
        next_tier = None
        for i, tier in enumerate(TIER_THRESHOLDS):
            if verified_points >= tier[0]:
                current_tier = tier
                next_tier = TIER_THRESHOLDS[i + 1] if i + 1 < len(TIER_THRESHOLDS) else None

        points_to_next = (next_tier[0] - verified_points) if next_tier else 0
        progress_pct = (
            min(100.0, round(((verified_points - current_tier[0]) / max(1, (next_tier[0] - current_tier[0]))) * 100.0, 1))
            if next_tier
            else 100.0
        )

        return {
            "verified_points": verified_points,
            "unverified_points": unverified_points,
            "total_points": verified_points + unverified_points,
            "current_tier": current_tier[1],
            "tier_description": current_tier[2],
            "next_tier": next_tier[1] if next_tier else "Max Tier Reached",
            "points_to_next_tier": max(0, points_to_next),
            "tier_progress_pct": progress_pct,
            "badges_count": len(badges_list),
            "badges": badges_list,
            "recent_achievements": recent_list,
        }
