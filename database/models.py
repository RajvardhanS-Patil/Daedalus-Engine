"""
Unstop Autopilot — SQLAlchemy ORM Models

All database tables are defined here as SQLAlchemy 2.0 declarative models.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


def _utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


class Opportunity(Base):
    """Discovered competition/hackathon/quiz opportunity."""
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[str] = mapped_column(String(200), default="unknown")
    fee: Mapped[str] = mapped_column(String(100), default="unknown")
    mode: Mapped[str] = mapped_column(String(50), default="unknown")  # online/offline/hybrid
    category: Mapped[str] = mapped_column(String(100), default="unknown")  # competition/hackathon/quiz
    certificate_info: Mapped[str] = mapped_column(String(500), default="unknown")
    participants_count: Mapped[str] = mapped_column(String(100), default="unknown")
    source_url: Mapped[str] = mapped_column(String(1000), default="")  # which listing page

    # JSON text storing raw evidence for each field
    source_evidence: Mapped[str] = mapped_column(Text, default="{}")

    # Status tracking
    status: Mapped[str] = mapped_column(String(50), default="open")  # open/closed/expired
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # AI analysis results (populated in Phase 2)
    suitability_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    classification: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ai_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    # Detailed fields extracted by AI (Phase 2)
    participation_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # individual/team
    eligibility: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    num_rounds: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    submission_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    time_commitment: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    format_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # online/offline/hybrid

    # Timestamps
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    registrations: Mapped[list["Registration"]] = relationship(back_populates="opportunity")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="opportunity")
    certificates: Mapped[list["Certificate"]] = relationship(back_populates="opportunity")
    documents: Mapped[list["GeneratedDocument"]] = relationship(back_populates="opportunity")

    def __repr__(self) -> str:
        return f"<Opportunity(id={self.id}, title='{self.title[:40]}', status='{self.status}')>"

    def get_source_evidence(self) -> dict:
        """Parse source_evidence JSON."""
        try:
            return json.loads(self.source_evidence)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_source_evidence(self, evidence: dict) -> None:
        """Store source_evidence as JSON."""
        self.source_evidence = json.dumps(evidence)


class OpportunitySource(Base):
    """Track which listing pages an opportunity was found on."""
    __tablename__ = "opportunity_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(Integer, ForeignKey("opportunities.id"), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("opportunity_id", "source_url", name="uq_opp_source"),
    )


class EligibilityResult(Base):
    """Stores eligibility evaluation results for an opportunity."""
    __tablename__ = "eligibility_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(Integer, ForeignKey("opportunities.id"), nullable=False)
    is_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0 to 1.0
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    criteria_checked: Mapped[str] = mapped_column(Text, default="{}")  # JSON


class Registration(Base):
    """Tracks registration status for opportunities."""
    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(Integer, ForeignKey("opportunities.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # pending/registered/failed/cancelled
    registered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    registration_method: Mapped[str] = mapped_column(String(50), default="manual")
    # manual/assisted/auto
    notes: Mapped[str] = mapped_column(Text, default="")
    approval_given: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    opportunity: Mapped["Opportunity"] = relationship(back_populates="registrations")


class QuizQuestion(Base):
    """Question bank for practice and preparation."""
    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of strings
    correct_answer: Mapped[str] = mapped_column(String(500), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(200), default="general")
    difficulty: Mapped[str] = mapped_column(String(50), default="medium")  # easy/medium/hard
    source: Mapped[str] = mapped_column(String(200), default="generated")
    times_shown: Mapped[int] = mapped_column(Integer, default=0)
    times_correct: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def get_options(self) -> list[str]:
        try:
            return json.loads(self.options)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_options(self, opts: list[str]) -> None:
        self.options = json.dumps(opts)

    @property
    def accuracy(self) -> float:
        if self.times_shown == 0:
            return 0.0
        return self.times_correct / self.times_shown


class QuizSession(Base):
    """Tracks quiz practice sessions."""
    __tablename__ = "quiz_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)  # practice/open_book/live
    topic: Mapped[str] = mapped_column(String(200), default="general")
    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    correct_answers: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    question_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of IDs
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    live_attempts: Mapped[list["LiveQuizAttempt"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class LiveQuizAttempt(Base):
    """Tracks questions, AI decisions, and answers in live quiz sessions."""
    __tablename__ = "live_quiz_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("quiz_sessions.id"), nullable=True
    )
    question_number: Mapped[int] = mapped_column(Integer, default=1)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[str] = mapped_column(Text, default="[]")
    chosen_option: Mapped[str] = mapped_column(String(500), default="")
    chosen_index: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    time_remaining_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    session: Mapped[Optional["QuizSession"]] = relationship(back_populates="live_attempts")

    def get_options(self) -> list[str]:
        try:
            return json.loads(self.options_json)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_options(self, opts: list[str]) -> None:
        self.options_json = json.dumps(opts)


class GeneratedDocument(Base):
    """Tracks generated presentations and documents."""
    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("opportunities.id"), nullable=True
    )
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)  # pptx/pdf/docx
    title: Mapped[str] = mapped_column(String(500), default="")
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    template_used: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(50), default="draft")  # draft/reviewed/submitted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="documents")


class Submission(Base):
    """Tracks submission status for opportunities."""
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(Integer, ForeignKey("opportunities.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), default="")
    file_type: Mapped[str] = mapped_column(String(50), default="")
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    submission_url: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(50), default="preparing")
    # preparing/validated/submitted/confirmed/failed
    checklist: Mapped[str] = mapped_column(Text, default="{}")  # JSON checklist
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    opportunity: Mapped["Opportunity"] = relationship(back_populates="submissions")


class Certificate(Base):
    """Tracks earned certificates."""
    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("opportunities.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    issuer: Mapped[str] = mapped_column(String(200), default="Unstop")
    certificate_url: Mapped[str] = mapped_column(String(1000), default="")
    local_path: Mapped[str] = mapped_column(String(1000), default="")
    certificate_type: Mapped[str] = mapped_column(String(100), default="participation")
    # participation/completion/winner/merit
    earned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    downloaded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="certificates")


class Achievement(Base):
    """Tracks overall achievements and milestones."""
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    achievement_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # points/badge/shortlist/win/round_completion
    title: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # e.g., points earned
    source: Mapped[str] = mapped_column(String(200), default="")  # which competition
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    achieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AutomationJob(Base):
    """Tracks scheduled automation jobs."""
    __tablename__ = "automation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # discovery/refresh/deadline_check/certificate_check/summary/report
    schedule: Mapped[str] = mapped_column(String(200), default="")  # cron or interval
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(50), default="pending")
    last_result: Mapped[str] = mapped_column(Text, default="")
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ActivityLog(Base):
    """Audit trail for all system actions."""
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="success")  # success/error/warning
    module: Mapped[str] = mapped_column(String(100), default="system")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_activity_logs_timestamp", "timestamp"),
        Index("ix_activity_logs_action", "action"),
    )


class UserPreference(Base):
    """User profile and preferences for eligibility matching."""
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    value: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="general")
    # general/profile/filter/scoring
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
