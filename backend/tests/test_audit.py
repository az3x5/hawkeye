"""The audit log and identification records.

Integration tests against a real database: the append-only property and the
review rules are enforced by the schema and the store, so fakes would prove
nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.connectors.postgres import PostgresConnector, SqlAlchemyPersonRepository, metadata
from app.connectors.postgres.audit import SqlAlchemyAuditLog, SqlAlchemyIdentificationStore
from app.domain.audit import SYSTEM_ACTOR, Actor, AuditAction, AuditEvent, AuditLog
from app.domain.identity import (
    Candidate,
    DecisionOutcome,
    DecisionThresholds,
    IdentityDecision,
    ReviewOutcome,
)
from app.domain.models import Person
from app.domain.repositories import ConflictError

from .conftest import INTEGRATION_DSN

pytestmark = pytest.mark.skipif(
    INTEGRATION_DSN is None,
    reason="set FACEID_TEST_POSTGRES_DSN to run audit tests",
)

POLICY = DecisionThresholds(accept_at=0.62, review_at=0.42, policy_version="test-v1")


def _decision(
    outcome: DecisionOutcome, score: float = 0.5, person: UUID | None = None
) -> IdentityDecision:
    return IdentityDecision(
        outcome=outcome,
        thresholds=POLICY,
        candidates=(
            Candidate(
                person_uuid=person or uuid4(),
                face_sample_uuid=uuid4(),
                score=score,
                sample_count=2,
            ),
        ),
    )


@pytest_asyncio.fixture
async def known_person(connector: PostgresConnector) -> Person:
    """A person the identification's candidate can legitimately refer to."""
    person = Person()
    async with connector.session() as session:
        await SqlAlchemyPersonRepository(session).add(person)
    return person


@pytest_asyncio.fixture
async def connector() -> AsyncIterator[PostgresConnector]:
    assert INTEGRATION_DSN is not None
    connector = PostgresConnector(INTEGRATION_DSN)
    async with connector.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    yield connector
    async with connector.engine.begin() as connection:
        await connection.execute(text("TRUNCATE audit_events, identifications CASCADE"))
    await connector.close()


class TestAuditLog:
    async def test_satisfies_the_audit_log_protocol(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            assert isinstance(SqlAlchemyAuditLog(session), AuditLog)

    async def test_exposes_no_way_to_change_or_remove_a_record(self) -> None:
        """Append and read only: a rewritable log is not evidence."""
        surface = {name for name in dir(SqlAlchemyAuditLog) if not name.startswith("_")}
        assert surface == {"record", "for_person", "for_identification"}

    async def test_an_event_round_trips(self, connector: PostgresConnector) -> None:
        person = Person()
        event = AuditEvent(
            action=AuditAction.IDENTIFICATION_PERFORMED,
            actor=SYSTEM_ACTOR,
            person_uuid=person.person_uuid,
            policy_version="test-v1",
            details={"outcome": "review", "best_score": 0.5},
        )
        async with connector.session() as session:
            await SqlAlchemyPersonRepository(session).add(person)
            await SqlAlchemyAuditLog(session).record(event)

        async with connector.session() as session:
            found = await SqlAlchemyAuditLog(session).for_person(person.person_uuid)
        assert len(found) == 1
        assert found[0].action is AuditAction.IDENTIFICATION_PERFORMED
        assert found[0].details["best_score"] == pytest.approx(0.5)

    async def test_a_system_action_is_never_attributed_to_a_person(
        self, connector: PostgresConnector
    ) -> None:
        identification = uuid4()
        async with connector.session() as session:
            await SqlAlchemyAuditLog(session).record(
                AuditEvent(
                    action=AuditAction.IDENTIFICATION_PERFORMED,
                    actor=SYSTEM_ACTOR,
                    identification_uuid=identification,
                )
            )
        async with connector.session() as session:
            events = await SqlAlchemyAuditLog(session).for_identification(identification)
        assert events[0].actor.kind == "system"
        assert events[0].actor.identifier == "faceid"

    async def test_events_survive_deletion_of_what_they_describe(
        self, connector: PostgresConnector
    ) -> None:
        """The log carries no foreign keys, so it can evidence a deletion."""
        person = Person()
        async with connector.session() as session:
            await SqlAlchemyPersonRepository(session).add(person)
            await SqlAlchemyAuditLog(session).record(
                AuditEvent(
                    action=AuditAction.IDENTIFICATION_PERFORMED,
                    actor=SYSTEM_ACTOR,
                    person_uuid=person.person_uuid,
                )
            )
        async with connector.session() as session:
            await session.execute(
                text("DELETE FROM persons WHERE person_uuid = :u"),
                {"u": str(person.person_uuid)},
            )
        async with connector.session() as session:
            assert len(await SqlAlchemyAuditLog(session).for_person(person.person_uuid)) == 1

    async def test_an_actor_must_be_identified(self) -> None:
        with pytest.raises(ValueError, match="must be identified"):
            Actor(identifier="  ")

    async def test_an_unknown_actor_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="actor kind"):
            Actor(identifier="alice", kind="robot")

    async def test_events_for_an_identification_are_oldest_first(
        self, connector: PostgresConnector
    ) -> None:
        identification = uuid4()
        early = datetime(2026, 1, 1, tzinfo=UTC)
        late = datetime(2026, 6, 1, tzinfo=UTC)
        async with connector.session() as session:
            log = SqlAlchemyAuditLog(session)
            await log.record(
                AuditEvent(
                    action=AuditAction.IDENTIFICATION_REVIEWED,
                    actor=Actor("alice"),
                    identification_uuid=identification,
                    occurred_at=late,
                )
            )
            await log.record(
                AuditEvent(
                    action=AuditAction.IDENTIFICATION_PERFORMED,
                    actor=SYSTEM_ACTOR,
                    identification_uuid=identification,
                    occurred_at=early,
                )
            )
        async with connector.session() as session:
            events = await SqlAlchemyAuditLog(session).for_identification(identification)
        assert [e.action for e in events] == [
            AuditAction.IDENTIFICATION_PERFORMED,
            AuditAction.IDENTIFICATION_REVIEWED,
        ]


class TestIdentificationStore:
    async def test_a_decision_round_trips_with_its_policy(
        self, connector: PostgresConnector, known_person: Person
    ) -> None:
        identification = uuid4()
        decision = _decision(DecisionOutcome.REVIEW, 0.51, known_person.person_uuid)
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).add(identification, "a" * 64, decision)

        async with connector.session() as session:
            stored = await SqlAlchemyIdentificationStore(session).get(identification)
        assert stored is not None
        assert stored.decision.outcome is DecisionOutcome.REVIEW
        assert stored.decision.thresholds.policy_version == "test-v1"
        assert stored.decision.thresholds.accept_at == pytest.approx(0.62)
        assert stored.decision.candidates[0].score == pytest.approx(0.51)

    async def test_the_thresholds_are_stored_not_merely_referenced(
        self, connector: PostgresConnector, known_person: Person
    ) -> None:
        """A past decision stays readable against the rules that produced it."""
        identification = uuid4()
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).add(
                identification,
                "b" * 64,
                _decision(DecisionOutcome.ACCEPT, 0.9, known_person.person_uuid),
            )
        async with connector.session() as session:
            row = await session.execute(
                text(
                    "SELECT accept_at, review_at, policy_version FROM identifications "
                    "WHERE identification_uuid = :u"
                ),
                {"u": str(identification)},
            )
        accept_at, review_at, version = row.one()
        assert (accept_at, review_at, version) == (0.62, 0.42, "test-v1")

    async def test_an_identification_may_name_a_person_who_no_longer_exists(
        self, connector: PostgresConnector
    ) -> None:
        """The vector store can still hold a candidate the database forgot.

        A foreign key here used to turn that into a 500 on identification. An
        identification is a historical record and must outlive the person it
        named.
        """
        identification = uuid4()
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).add(
                identification, "e" * 64, _decision(DecisionOutcome.ACCEPT, 0.9, uuid4())
            )
        async with connector.session() as session:
            stored = await SqlAlchemyIdentificationStore(session).get(identification)
        assert stored is not None
        assert stored.decision.best is not None

    async def test_a_recorded_identification_survives_deleting_its_person(
        self, connector: PostgresConnector, known_person: Person
    ) -> None:
        identification = uuid4()
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).add(
                identification,
                "f" * 64,
                _decision(DecisionOutcome.ACCEPT, 0.9, known_person.person_uuid),
            )
        async with connector.session() as session:
            await session.execute(
                text("DELETE FROM persons WHERE person_uuid = :u"),
                {"u": str(known_person.person_uuid)},
            )
        async with connector.session() as session:
            stored = await SqlAlchemyIdentificationStore(session).get(identification)
        assert stored is not None
        assert stored.decision.best is not None
        assert stored.decision.best.person_uuid == known_person.person_uuid

    async def test_an_unknown_identification_is_none(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            assert await SqlAlchemyIdentificationStore(session).get(uuid4()) is None

    async def test_a_review_is_recorded(
        self, connector: PostgresConnector, known_person: Person
    ) -> None:
        identification = uuid4()
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).add(
                identification,
                "c" * 64,
                _decision(DecisionOutcome.REVIEW, person=known_person.person_uuid),
            )
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).record_review(
                identification,
                outcome=ReviewOutcome.CONFIRMED,
                reviewer="alice",
                note="clear match",
                reviewed_at=datetime.now(UTC),
            )
        async with connector.session() as session:
            stored = await SqlAlchemyIdentificationStore(session).get(identification)
        assert stored is not None
        assert stored.review_outcome is ReviewOutcome.CONFIRMED
        assert stored.reviewed_by == "alice"
        assert stored.review_note == "clear match"

    async def test_a_recorded_review_cannot_be_overwritten(
        self, connector: PostgresConnector, known_person: Person
    ) -> None:
        """Changing a recorded judgement would erase the first one."""
        identification = uuid4()
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).add(
                identification,
                "d" * 64,
                _decision(DecisionOutcome.REVIEW, person=known_person.person_uuid),
            )
        async with connector.session() as session:
            await SqlAlchemyIdentificationStore(session).record_review(
                identification,
                outcome=ReviewOutcome.CONFIRMED,
                reviewer="alice",
                note=None,
                reviewed_at=datetime.now(UTC),
            )
        with pytest.raises(ConflictError, match="already reviewed"):
            async with connector.session() as session:
                await SqlAlchemyIdentificationStore(session).record_review(
                    identification,
                    outcome=ReviewOutcome.REJECTED,
                    reviewer="mallory",
                    note="actually no",
                    reviewed_at=datetime.now(UTC),
                )

    async def test_reviewing_an_unknown_identification_conflicts(
        self, connector: PostgresConnector
    ) -> None:
        with pytest.raises(ConflictError, match="unknown or already reviewed"):
            async with connector.session() as session:
                await SqlAlchemyIdentificationStore(session).record_review(
                    uuid4(),
                    outcome=ReviewOutcome.CONFIRMED,
                    reviewer="alice",
                    note=None,
                    reviewed_at=datetime.now(UTC),
                )
