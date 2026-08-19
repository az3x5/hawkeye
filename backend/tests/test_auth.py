"""Authentication, authorisation and credential storage."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.security import NotAuthenticatedError, get_principal, require
from app.connectors.postgres import PostgresConnector, metadata
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.auth import (
    ApiToken,
    AuthorisationError,
    Principal,
    Scope,
    TokenStore,
    generate_token,
    hash_token,
    new_token,
)

from .conftest import INTEGRATION_DSN


class TestTokenGeneration:
    def test_a_minted_secret_is_unguessable_and_prefixed(self) -> None:
        first, second = generate_token(), generate_token()
        assert first != second
        assert first.startswith("faceid_")
        assert len(first) > 40

    def test_the_secret_is_not_recoverable_from_the_record(self) -> None:
        record, secret = new_token("alice", "user", [Scope.REVIEW])
        assert secret not in str(record)
        assert record.token_sha256 == hash_token(secret)

    def test_hashing_is_stable_and_distinguishing(self) -> None:
        assert hash_token("abc") == hash_token("abc")
        assert hash_token("abc") != hash_token("abd")


class TestPrincipal:
    def test_a_principal_must_be_identified(self) -> None:
        with pytest.raises(ValueError, match="must have a subject"):
            Principal(token_uuid=uuid4(), subject="  ", kind="user", scopes=frozenset())

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="principal kind"):
            Principal(token_uuid=uuid4(), subject="a", kind="robot", scopes=frozenset())

    def test_scopes_are_enforced(self) -> None:
        principal = Principal(
            token_uuid=uuid4(), subject="a", kind="user", scopes=frozenset({Scope.REVIEW})
        )
        assert principal.has(Scope.REVIEW)
        assert not principal.has(Scope.ENROL)
        principal.require(Scope.REVIEW)
        with pytest.raises(AuthorisationError, match="enrol"):
            principal.require(Scope.ENROL)

    def test_a_human_is_recorded_as_a_user_actor(self) -> None:
        actor = Principal(
            token_uuid=uuid4(), subject="alice", kind="user", scopes=frozenset()
        ).as_actor()
        assert (actor.identifier, actor.kind) == ("alice", "user")

    def test_a_service_is_never_recorded_as_a_person(self) -> None:
        """A service credential must not look like a human judgement."""
        actor = Principal(
            token_uuid=uuid4(), subject="ingest", kind="service", scopes=frozenset()
        ).as_actor()
        assert actor.kind == "system"


@pytest.mark.skipif(INTEGRATION_DSN is None, reason="needs FACEID_TEST_POSTGRES_DSN")
class TestTokenStore:
    @pytest_asyncio.fixture
    async def connector(self) -> AsyncIterator[PostgresConnector]:
        assert INTEGRATION_DSN is not None
        connector = PostgresConnector(INTEGRATION_DSN)
        async with connector.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield connector
        async with connector.engine.begin() as connection:
            await connection.execute(text("TRUNCATE api_tokens CASCADE"))
        await connector.close()

    async def test_satisfies_the_token_store_protocol(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            assert isinstance(SqlAlchemyTokenStore(session), TokenStore)

    async def test_a_credential_is_found_by_its_hash(self, connector: PostgresConnector) -> None:
        record, secret = new_token("alice", "user", [Scope.REVIEW])
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).add(record)
        async with connector.session() as session:
            found = await SqlAlchemyTokenStore(session).find_by_hash(hash_token(secret))
        assert found is not None
        assert found.subject == "alice"
        assert found.scopes == frozenset({Scope.REVIEW})

    async def test_the_secret_is_never_stored(self, connector: PostgresConnector) -> None:
        record, secret = new_token("alice", "user", [Scope.REVIEW])
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).add(record)
        async with connector.session() as session:
            rows = await session.execute(text("SELECT * FROM api_tokens"))
            assert secret not in str(rows.all())

    async def test_an_unknown_hash_is_none(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            assert await SqlAlchemyTokenStore(session).find_by_hash("0" * 64) is None

    async def test_revoking_marks_it_unusable(self, connector: PostgresConnector) -> None:
        record, secret = new_token("alice", "user", [Scope.REVIEW])
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).add(record)
        async with connector.session() as session:
            assert await SqlAlchemyTokenStore(session).disable(record.token_uuid) is True
        async with connector.session() as session:
            found = await SqlAlchemyTokenStore(session).find_by_hash(hash_token(secret))
        assert found is not None
        assert found.active is False

    async def test_revoking_twice_changes_nothing(self, connector: PostgresConnector) -> None:
        record, _ = new_token("alice", "user", [Scope.REVIEW])
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).add(record)
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).disable(record.token_uuid)
        async with connector.session() as session:
            assert await SqlAlchemyTokenStore(session).disable(record.token_uuid) is False

    async def test_an_unknown_scope_name_does_not_lock_anyone_out(
        self, connector: PostgresConnector
    ) -> None:
        record, secret = new_token("alice", "user", [Scope.REVIEW])
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).add(record)
            await session.execute(
                text(
                    "UPDATE api_tokens SET scopes = ARRAY['review','retired_scope'] "
                    "WHERE token_uuid = :u"
                ),
                {"u": str(record.token_uuid)},
            )
        async with connector.session() as session:
            found = await SqlAlchemyTokenStore(session).find_by_hash(hash_token(secret))
        assert found is not None
        assert found.scopes == frozenset({Scope.REVIEW})


class TestEndpointEnforcement:
    """The dependency, exercised over HTTP against a stub store."""

    @pytest.fixture
    def tokens(self) -> dict[str, ApiToken]:
        return {}

    @pytest.fixture
    def client(self, tokens: dict[str, ApiToken]) -> Iterator[TestClient]:
        app = FastAPI()
        install_error_handlers(app)

        @app.get("/guarded")
        async def guarded(
            principal: Annotated[Principal, Depends(require(Scope.REVIEW))],
        ) -> dict[str, str]:
            return {"subject": principal.subject}

        app.dependency_overrides[get_principal] = _principal_from(tokens)
        with TestClient(app) as test_client:
            yield test_client

    def test_a_holder_of_the_scope_is_admitted(
        self, client: TestClient, tokens: dict[str, ApiToken]
    ) -> None:
        tokens["good"] = _token("alice", "user", {Scope.REVIEW})
        response = client.get("/guarded", headers={"Authorization": "Bearer good"})
        assert response.status_code == 200
        assert response.json() == {"subject": "alice"}

    def test_a_missing_credential_is_401(self, client: TestClient) -> None:
        response = client.get("/guarded")
        assert response.status_code == 401
        assert ErrorResponse.model_validate(response.json()).error.code == "not_authenticated"

    def test_an_unknown_credential_is_401(self, client: TestClient) -> None:
        response = client.get("/guarded", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

    def test_a_credential_without_the_scope_is_403(
        self, client: TestClient, tokens: dict[str, ApiToken]
    ) -> None:
        tokens["weak"] = _token("bob", "user", {Scope.ENROL})
        response = client.get("/guarded", headers={"Authorization": "Bearer weak"})
        assert response.status_code == 403
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "not_authorised"
        assert "review" in body.error.message

    def test_a_revoked_credential_is_401(
        self, client: TestClient, tokens: dict[str, ApiToken]
    ) -> None:
        tokens["dead"] = _token("carol", "user", {Scope.REVIEW}, disabled=True)
        assert client.get("/guarded", headers={"Authorization": "Bearer dead"}).status_code == 401


def _token(subject: str, kind: str, scopes: set[Scope], *, disabled: bool = False) -> ApiToken:
    return ApiToken(
        token_uuid=uuid4(),
        subject=subject,
        kind=kind,
        token_sha256="x" * 64,
        scopes=frozenset(scopes),
        created_at=datetime.now(UTC),
        disabled_at=datetime.now(UTC) if disabled else None,
    )


def _principal_from(
    tokens: dict[str, ApiToken],
) -> Callable[[Request], Awaitable[Principal]]:
    """Build a get_principal override backed by an in-memory token map."""

    async def override(request: Request) -> Principal:
        header = request.headers.get("Authorization", "")
        secret = header.removeprefix("Bearer ").strip()
        token = tokens.get(secret)
        if token is None or not token.active:
            raise NotAuthenticatedError("the presented credential is not valid")
        return Principal(
            token_uuid=token.token_uuid,
            subject=token.subject,
            kind=token.kind,
            scopes=token.scopes,
        )

    return override
