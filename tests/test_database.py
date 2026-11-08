"""
Tests for Database Models and Connection Management
"""

import json
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base,
    Opportunity,
    Registration,
    QuizQuestion,
    QuizSession,
    Certificate,
    Achievement,
    ActivityLog,
)


@pytest.fixture
def test_db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_create_opportunity(test_db_session):
    """Test inserting and querying an Opportunity."""
    opp = Opportunity(
        title="Test Hackathon 2026",
        url="https://unstop.com/hackathons/test-hackathon-2026",
        description="A great student hackathon",
        deadline="Nov 30, 2026",
        fee="Free",
        mode="online",
        category="hackathon",
        certificate_info="Participation certificate provided",
    )
    opp.set_source_evidence({"fee": "Free Registration", "mode": "Online Event"})

    test_db_session.add(opp)
    test_db_session.commit()

    queried = test_db_session.execute(
        select(Opportunity).where(Opportunity.url == opp.url)
    ).scalar_one()

    assert queried.title == "Test Hackathon 2026"
    assert queried.mode == "online"
    assert queried.fee == "Free"
    assert queried.get_source_evidence() == {"fee": "Free Registration", "mode": "Online Event"}


def test_unique_url_constraint(test_db_session):
    """Test that duplicate URLs violate unique constraint."""
    opp1 = Opportunity(title="Opp 1", url="https://unstop.com/o/1")
    opp2 = Opportunity(title="Opp 2", url="https://unstop.com/o/1")

    test_db_session.add(opp1)
    test_db_session.commit()

    test_db_session.add(opp2)
    with pytest.raises(Exception):
        test_db_session.commit()


def test_registration_relationship(test_db_session):
    """Test Opportunity <-> Registration relationship."""
    opp = Opportunity(title="Quiz Champ", url="https://unstop.com/q/champ")
    test_db_session.add(opp)
    test_db_session.commit()

    reg = Registration(opportunity_id=opp.id, status="registered", approval_given=True)
    test_db_session.add(reg)
    test_db_session.commit()

    assert len(opp.registrations) == 1
    assert opp.registrations[0].status == "registered"


def test_quiz_question_accuracy(test_db_session):
    """Test QuizQuestion helper methods and accuracy calculation."""
    q = QuizQuestion(
        question="What is Python?",
        correct_answer="A programming language",
        topic="python",
        difficulty="easy",
    )
    q.set_options(["A fruit", "A snake", "A programming language"])
    test_db_session.add(q)
    test_db_session.commit()

    assert len(q.get_options()) == 3
    assert q.accuracy == 0.0

    q.times_shown = 10
    q.times_correct = 8
    assert q.accuracy == 0.8
