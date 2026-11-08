"""
Unstop Autopilot — Certificate Manager

Tracks and archives earned certificates from Unstop competitions, hackathons,
and assessments. Manages local filesystem storage under data/certificates/.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from database.connection import get_session
from database.models import Certificate, Opportunity

logger = logging.getLogger(__name__)


class CertificateManager:
    """Manages earned certificates, local document archives, and platform badges."""

    def __init__(self, cert_dir: str | Path = "data/certificates") -> None:
        self.cert_dir = Path(cert_dir).resolve()
        self.cert_dir.mkdir(parents=True, exist_ok=True)

    def add_certificate(
        self,
        name: str,
        issuer: str = "Unstop",
        certificate_type: str = "participation",
        opportunity_id: int | None = None,
        local_path: str = "",
        certificate_url: str = "",
        earned_at: datetime | None = None,
    ) -> Certificate:
        """Register an earned certificate into the database."""
        with get_session() as session:
            # Check if matching opportunity exists if given
            if opportunity_id:
                opp = session.get(Opportunity, opportunity_id)
                if not opp:
                    opportunity_id = None

            downloaded = bool(local_path and Path(local_path).exists())

            cert = Certificate(
                opportunity_id=opportunity_id,
                name=name,
                issuer=issuer,
                certificate_url=certificate_url,
                local_path=str(local_path),
                certificate_type=certificate_type.lower(),
                earned_at=earned_at or datetime.now(timezone.utc),
                downloaded=downloaded,
            )
            session.add(cert)
            session.commit()
            session.refresh(cert)
            logger.info("Registered certificate ID=%d '%s' (%s)", cert.id, cert.name, cert.certificate_type)
            return cert

    def list_certificates(self) -> list[dict[str, Any]]:
        """Fetch all certificates with opportunity context."""
        with get_session() as session:
            query = (
                select(Certificate, Opportunity.title)
                .outerjoin(Opportunity, Certificate.opportunity_id == Opportunity.id)
                .order_by(Certificate.created_at.desc())
            )
            rows = session.execute(query).all()
            results = []
            for cert, opp_title in rows:
                results.append({
                    "id": cert.id,
                    "name": cert.name,
                    "issuer": cert.issuer,
                    "certificate_type": cert.certificate_type,
                    "opportunity_title": opp_title or "Direct / Independent",
                    "local_path": cert.local_path,
                    "certificate_url": cert.certificate_url,
                    "downloaded": cert.downloaded,
                    "earned_at": cert.earned_at.strftime("%Y-%m-%d") if cert.earned_at else "Unknown",
                })
            return results

    def get_certificate_summary(self) -> dict[str, Any]:
        """Aggregate counts and breakdown of earned certificates."""
        with get_session() as session:
            total = session.execute(select(func.count(Certificate.id))).scalar() or 0
            downloaded = session.execute(
                select(func.count(Certificate.id)).where(Certificate.downloaded == True)  # noqa: E712
            ).scalar() or 0

            type_counts_query = (
                select(Certificate.certificate_type, func.count(Certificate.id))
                .group_by(Certificate.certificate_type)
            )
            type_rows = session.execute(type_counts_query).all()
            type_breakdown = {row[0]: row[1] for row in type_rows}

            return {
                "total_certificates": total,
                "downloaded_locally": downloaded,
                "pending_download": total - downloaded,
                "by_type": type_breakdown,
                "archive_directory": str(self.cert_dir),
            }

    def scan_local_archive(self) -> int:
        """Scan data/certificates directory and link any new unindexed files."""
        indexed_count = 0
        supported_exts = {".pdf", ".png", ".jpg", ".jpeg"}

        with get_session() as session:
            existing_paths = set(
                session.execute(select(Certificate.local_path)).scalars().all()
            )

            for item in self.cert_dir.rglob("*"):
                if item.is_file() and item.suffix.lower() in supported_exts:
                    rel_or_abs = str(item.resolve())
                    if rel_or_abs not in existing_paths and str(item) not in existing_paths:
                        cert = Certificate(
                            name=item.stem.replace("_", " ").title(),
                            issuer="Unstop / External",
                            local_path=rel_or_abs,
                            certificate_type="participation",
                            downloaded=True,
                            earned_at=datetime.now(timezone.utc),
                        )
                        session.add(cert)
                        indexed_count += 1

            if indexed_count > 0:
                session.commit()
                logger.info("Indexed %d new certificates from local archive", indexed_count)

        return indexed_count
