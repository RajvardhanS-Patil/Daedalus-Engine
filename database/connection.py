"""
Unstop Autopilot — Database Connection Management

Provides SQLite connection factory, session management, and database initialization.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from database.models import Base

logger = logging.getLogger(__name__)

# Module-level engine and session factory (initialized lazily)
_engine = None
_SessionFactory = None


def _get_db_path() -> Path:
    """Extract the database file path from the URL."""
    url = settings.database.url
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", ""))
    return settings.project_root / "data" / "unstop_autopilot.db"


def get_engine():
    """Get or create the SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        _engine = create_engine(
            settings.database.url,
            echo=settings.database.echo,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )

        # Enable WAL mode and foreign keys for SQLite
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        logger.info("Database engine created: %s", db_path)
    return _engine


def get_session_factory():
    """Get or create the session factory (singleton)."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager that yields a database session.

    Usage:
        with get_session() as session:
            session.query(Opportunity).all()

    Commits on success, rolls back on exception.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database() -> None:
    """Create all tables if they don't exist.

    This is safe to call multiple times — it won't drop existing tables.
    """
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database tables initialized.")


def check_database() -> bool:
    """Verify database connectivity."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("Database check failed: %s", e)
        return False


def get_table_counts() -> dict[str, int]:
    """Get row counts for all tables (for diagnostics)."""
    counts = {}
    try:
        with get_session() as session:
            for table in Base.metadata.sorted_tables:
                count = session.execute(
                    text(f"SELECT COUNT(*) FROM {table.name}")
                ).scalar()
                counts[table.name] = count or 0
    except Exception as e:
        logger.error("Failed to get table counts: %s", e)
    return counts
