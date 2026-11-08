"""
Unstop Autopilot — Submission Validator & Manager

Validates competitive deliverables against strict format, size, slide/page count,
and content compliance rules. Enforces Human-in-the-Loop policy approvals before
any final submission action.
"""

from __future__ import annotations

import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pptx import Presentation
from sqlalchemy import select

from config.policies import is_action_blocked, requires_approval
from database.connection import get_session
from database.models import ActivityLog, Opportunity, Submission

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Detailed file validation report."""
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_stats: dict[str, Any] = field(default_factory=dict)
    checklist: dict[str, bool] = field(default_factory=dict)


class SubmissionValidator:
    """Verifies deliverable files and manages submission records with policy gates."""

    DEFAULT_ALLOWED_EXTENSIONS = [".pptx", ".pdf", ".zip", ".docx"]
    DEFAULT_MAX_SIZE_MB = 50.0

    def validate_file(
        self,
        file_path: str | Path,
        allowed_extensions: list[str] | None = None,
        max_size_mb: float = DEFAULT_MAX_SIZE_MB,
        required_keywords: list[str] | None = None,
    ) -> ValidationResult:
        """Thoroughly inspect a deliverable file before submission."""
        path = Path(file_path).resolve()
        errors: list[str] = []
        warnings: list[str] = []
        checklist: dict[str, bool] = {
            "file_exists": False,
            "non_empty": False,
            "extension_allowed": False,
            "size_within_limit": False,
            "content_parsable": False,
        }
        stats: dict[str, Any] = {
            "path": str(path),
            "filename": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": 0,
            "size_mb": 0.0,
        }

        # 1. Existence check
        if not path.exists() or not path.is_file():
            errors.append(f"File does not exist: {path}")
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings, file_stats=stats, checklist=checklist)
        checklist["file_exists"] = True

        # 2. Non-empty check
        size_bytes = path.stat().st_size
        stats["size_bytes"] = size_bytes
        size_mb = round(size_bytes / (1024 * 1024), 2)
        stats["size_mb"] = size_mb

        if size_bytes == 0:
            errors.append("File is empty (0 bytes).")
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings, file_stats=stats, checklist=checklist)
        checklist["non_empty"] = True

        # 3. Extension check
        allowed = [e.lower() for e in (allowed_extensions or self.DEFAULT_ALLOWED_EXTENSIONS)]
        ext = path.suffix.lower()
        if ext not in allowed:
            errors.append(f"File extension '{ext}' is not in allowed list: {allowed}")
        else:
            checklist["extension_allowed"] = True

        # 4. Size limit check
        if size_mb > max_size_mb:
            errors.append(f"File size ({size_mb} MB) exceeds maximum allowed ({max_size_mb} MB).")
        else:
            checklist["size_within_limit"] = True

        # 5. Format-specific deep parsing
        try:
            if ext == ".pptx":
                prs = Presentation(str(path))
                slide_count = len(prs.slides)
                stats["slide_count"] = slide_count
                if slide_count == 0:
                    errors.append("Presentation contains 0 slides.")
                else:
                    checklist["content_parsable"] = True
                    if slide_count < 3:
                        warnings.append(f"Presentation has only {slide_count} slides; competitions usually expect 5-10.")

                    # Keyword presence check
                    if required_keywords:
                        full_text = ""
                        for slide in prs.slides:
                            for shape in slide.shapes:
                                if shape.has_text_frame:
                                    full_text += " " + shape.text_frame.text
                        full_text_lower = full_text.lower()
                        for kw in required_keywords:
                            if kw.lower() not in full_text_lower:
                                warnings.append(f"Recommended section keyword '{kw}' not found in slides.")

            elif ext == ".pdf":
                # Verify PDF header magic bytes
                with open(path, "rb") as f:
                    header = f.read(5)
                if header.startswith(b"%PDF-"):
                    checklist["content_parsable"] = True
                    stats["format"] = "Valid PDF header"
                else:
                    errors.append("File does not have a valid PDF header.")

            elif ext == ".zip":
                with zipfile.ZipFile(str(path), "r") as zf:
                    bad_file = zf.testzip()
                    if bad_file:
                        errors.append(f"Corrupted file inside zip: {bad_file}")
                    else:
                        checklist["content_parsable"] = True
                        stats["archived_files_count"] = len(zf.namelist())

            else:
                checklist["content_parsable"] = True

        except Exception as exc:
            errors.append(f"Failed to inspect file structure: {exc}")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            file_stats=stats,
            checklist=checklist,
        )

    def record_submission(
        self,
        opportunity_id: int,
        file_path: str | Path,
        notes: str = "",
        confirmed_by_user: bool = False,
    ) -> Submission:
        """Record and validate a submission with strict policy gate enforcement."""
        # Security Policy Check
        if requires_approval("final_submission") and not confirmed_by_user:
            raise PermissionError(
                "Policy violation: Action 'final_submission' requires explicit human confirmation. "
                "Provide confirmed_by_user=True after user approval."
            )

        val = self.validate_file(file_path)
        if not val.is_valid:
            raise ValueError(f"Submission file validation failed: {'; '.join(val.errors)}")

        path = Path(file_path).resolve()
        status = "submitted" if confirmed_by_user else "validated"

        with get_session() as session:
            opp = session.get(Opportunity, opportunity_id)
            if not opp:
                raise ValueError(f"Opportunity ID {opportunity_id} not found")

            sub = Submission(
                opportunity_id=opportunity_id,
                file_path=str(path),
                file_type=path.suffix.lstrip(".").lower(),
                file_size_bytes=val.file_stats.get("size_bytes", 0),
                submission_url=opp.url,
                status=status,
                checklist=json.dumps(val.checklist),
                notes=notes,
                submitted_at=datetime.now(timezone.utc) if confirmed_by_user else None,
                verified=confirmed_by_user,
            )
            session.add(sub)

            # Log activity
            log = ActivityLog(
                module="SubmissionValidator",
                action="record_submission",
                status="success",
                details=f"Recorded submission for '{opp.title[:40]}' (status={status}, file={path.name})",
            )
            session.add(log)
            session.commit()
            session.refresh(sub)
            logger.info("Persisted Submission ID=%d status=%s", sub.id, sub.status)
            return sub

    def get_submissions(self, opportunity_id: int | None = None) -> list[dict[str, Any]]:
        """Fetch all submission records joined with opportunity titles."""
        with get_session() as session:
            query = select(Submission, Opportunity.title).join(
                Opportunity, Submission.opportunity_id == Opportunity.id
            ).order_by(Submission.created_at.desc())
            if opportunity_id is not None:
                query = query.where(Submission.opportunity_id == opportunity_id)

            rows = session.execute(query).all()
            results = []
            for sub, opp_title in rows:
                results.append({
                    "id": sub.id,
                    "opportunity_id": sub.opportunity_id,
                    "opportunity_title": opp_title,
                    "file_path": sub.file_path,
                    "file_type": sub.file_type,
                    "file_size_bytes": sub.file_size_bytes,
                    "status": sub.status,
                    "submitted_at": sub.submitted_at.strftime("%Y-%m-%d %H:%M") if sub.submitted_at else "Not yet submitted",
                    "verified": sub.verified,
                    "notes": sub.notes,
                })
            return results
