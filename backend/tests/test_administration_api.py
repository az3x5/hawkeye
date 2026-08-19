"""Account and credential administration over HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.administration import router as administration_router
from app.api.v1.dependencies import (
    get_account_administration,
    get_default_token_lifetime,
    get_token_administration,
)
from app.api.v1.security import get_principal
from app.connectors.postgres import PostgresConnector, metadata
from app.connectors.postgres.audit import SqlAlchemyAuditLog
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.connectors.postgres.users import SqlAlchemyUserStore
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.auth import Principal, Scope, hash_token
from app.services.administration import AccountAdministration, TokenAdministration

from .conftest import INTEGRATION_DSN

pytestmark = pytest.mark.skipif(
    INTEGRATION_DSN is None, reason="administration tests need FACEID_TEST_POSTGRES_DSN"
)

PASSWORD = "a-sufficiently-long-password"
ADMIN_EMAIL = "admin@example.com"


def _run[T](work: Callable[[PostgresConnector], Awaitable[T]]) -> T:
    """Run a database task on its own connector and loop.

    The app owns its own pool inside the TestClient's event loop; assertions
    here must not borrow it, or asyncpg ends up with connections bound to a
    loop that has already gone.
    """

    async def run() -> T:
        assert INTEGRATION_DSN is not None
        connector = PostgresConnector(INTEGRATION_DSN)
        try:
            return await work(connector)
        finally:
            await connector.close()

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def schema() -> Iterator[None]:
    async def create(connector: PostgresConnector) -> None:
        async with connector.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def clean(connector: PostgresConnector) -> None:
        async with connector.engine.begin() as connection:
            await connection.execute(text("TRUNCATE users, api_tokens, audit_events CASCADE"))

    _run(create)
    yield
    _run(clean)


@pytest.fixture
def admin() -> Principal:
    return Principal(
        token_uuid=uuid4(),
        subject=ADMIN_EMAIL,
        kind="user",
        scopes=frozenset({Scope.ADMIN}),
    )


@pytest.fixture
def app(admin: Principal) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        assert INTEGRATION_DSN is not None
        connector = PostgresConnector(INTEGRATION_DSN)
        application.state.connector = connector
        yield
        await connector.close()

    application = FastAPI(lifespan=lifespan)
    install_error_handlers(application)
    application.include_router(administration_router, prefix="/api/v1")

    async def accounts() -> AsyncIterator[AccountAdministration]:
        async with application.state.connector.session() as session:
            yield AccountAdministration(
                users=SqlAlchemyUserStore(session),
                tokens=SqlAlchemyTokenStore(session),
                audit=SqlAlchemyAuditLog(session),
            )

    async def tokens() -> AsyncIterator[TokenAdministration]:
        async with application.state.connector.session() as session:
            yield TokenAdministration(
                tokens=SqlAlchemyTokenStore(session), audit=SqlAlchemyAuditLog(session)
            )

    application.dependency_overrides[get_account_administration] = accounts
    application.dependency_overrides[get_token_administration] = tokens
    application.dependency_overrides[get_default_token_lifetime] = lambda: 90
    application.dependency_overrides[get_principal] = lambda: admin
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _create(
    client: TestClient, email: str = "reviewer@example.com", **over: object
) -> dict[str, Any]:
    payload: dict[str, object] = {
        "email": email,
        "password": PASSWORD,
        "scopes": ["review"],
    }
    payload.update(over)
    created: dict[str, Any] = client.post("/api/v1/accounts", json=payload).json()
    return created


class TestAccounts:
    def test_an_account_can_be_created(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/accounts",
            json={"email": "New@Example.com", "password": PASSWORD, "scopes": ["review"]},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "new@example.com"
        assert body["scopes"] == ["review"]
        assert body["active"] is True

    def test_the_password_is_never_returned(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/accounts",
            json={"email": "x@example.com", "password": PASSWORD, "scopes": ["review"]},
        )
        assert PASSWORD not in response.text
        assert "password" not in response.json()

    def test_a_duplicate_email_conflicts(self, client: TestClient) -> None:
        _create(client)
        response = client.post(
            "/api/v1/accounts",
            json={"email": "reviewer@example.com", "password": PASSWORD, "scopes": ["review"]},
        )
        assert response.status_code == 409
        assert ErrorResponse.model_validate(response.json()).error.code == "account_conflict"

    def test_a_weak_password_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/accounts",
            json={"email": "weak@example.com", "password": "short", "scopes": ["review"]},
        )
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "invalid_password"

    def test_an_account_needs_at_least_one_scope(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/accounts",
            json={"email": "noscope@example.com", "password": PASSWORD, "scopes": []},
        )
        assert response.status_code == 422

    def test_accounts_can_be_listed_without_hashes(self, client: TestClient) -> None:
        _create(client)
        response = client.get("/api/v1/accounts")
        assert response.status_code == 200
        assert "password_hash" not in response.text
        assert [a["email"] for a in response.json()] == ["reviewer@example.com"]

    def test_a_password_can_be_set_by_an_administrator(self, client: TestClient) -> None:
        created = _create(client)
        response = client.post(
            f"/api/v1/accounts/{created['user_uuid']}/password",
            json={"password": "an-entirely-different-password"},
        )
        assert response.status_code == 200

    def test_setting_a_password_on_an_unknown_account_is_404(self, client: TestClient) -> None:
        response = client.post(f"/api/v1/accounts/{uuid4()}/password", json={"password": PASSWORD})
        assert response.status_code == 404
        assert ErrorResponse.model_validate(response.json()).error.code == "account_not_found"

    def test_an_account_can_be_disabled_and_re_enabled(self, client: TestClient) -> None:
        created = _create(client)
        disabled = client.post(f"/api/v1/accounts/{created['user_uuid']}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["active"] is False

        enabled = client.post(f"/api/v1/accounts/{created['user_uuid']}/enable")
        assert enabled.json()["active"] is True

    def test_you_cannot_disable_your_own_account(self, client: TestClient) -> None:
        """An administrator locking themselves out is an accident."""
        mine = _create(client, email=ADMIN_EMAIL)
        response = client.post(f"/api/v1/accounts/{mine['user_uuid']}/disable")
        assert response.status_code == 409
        assert "signed in as" in ErrorResponse.model_validate(response.json()).error.message


class TestOwnPassword:
    def test_a_user_can_change_their_own_password(
        self, app: FastAPI, client: TestClient, admin: Principal
    ) -> None:
        _create(client, email=ADMIN_EMAIL)
        response = client.post(
            "/api/v1/me/password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        )
        assert response.status_code == 204

    def test_the_current_password_must_be_correct(self, client: TestClient) -> None:
        """A borrowed session must not become ownership of the account."""
        _create(client, email=ADMIN_EMAIL)
        response = client.post(
            "/api/v1/me/password",
            json={"current_password": "not-the-password", "new_password": "a-brand-new-one"},
        )
        assert response.status_code == 403
        assert ErrorResponse.model_validate(response.json()).error.code == "wrong_password"

    def test_the_new_password_must_differ(self, client: TestClient) -> None:
        _create(client, email=ADMIN_EMAIL)
        response = client.post(
            "/api/v1/me/password",
            json={"current_password": PASSWORD, "new_password": PASSWORD},
        )
        assert response.status_code == 422

    def test_a_credential_without_an_account_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/me/password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        )
        assert response.status_code == 404


class TestTokens:
    def test_a_credential_is_issued_with_its_secret_once(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/tokens",
            json={"subject": "ingest", "kind": "service", "scopes": ["enrol", "identify"]},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["token"].startswith("faceid_")
        assert body["scopes"] == ["enrol", "identify"]
        assert body["expires_at"] is not None

    def test_the_secret_is_never_listed(self, client: TestClient) -> None:
        secret = client.post(
            "/api/v1/tokens", json={"subject": "ingest", "scopes": ["identify"]}
        ).json()["token"]
        listed = client.get("/api/v1/tokens")
        assert secret not in listed.text
        assert "token" not in listed.json()[0]

    def test_a_never_expiring_credential_must_be_asked_for(self, client: TestClient) -> None:
        default = client.post(
            "/api/v1/tokens", json={"subject": "a", "scopes": ["identify"]}
        ).json()
        forever = client.post(
            "/api/v1/tokens",
            json={"subject": "b", "scopes": ["identify"], "never_expires": True},
        ).json()
        assert default["expires_at"] is not None
        assert forever["expires_at"] is None

    def test_a_credential_can_be_revoked(self, client: TestClient) -> None:
        issued = client.post(
            "/api/v1/tokens", json={"subject": "ingest", "scopes": ["identify"]}
        ).json()
        assert client.delete(f"/api/v1/tokens/{issued['token_uuid']}").status_code == 204
        assert client.get("/api/v1/tokens").json()[0]["active"] is False

    def test_revoking_twice_is_404(self, client: TestClient) -> None:
        issued = client.post(
            "/api/v1/tokens", json={"subject": "ingest", "scopes": ["identify"]}
        ).json()
        client.delete(f"/api/v1/tokens/{issued['token_uuid']}")
        response = client.delete(f"/api/v1/tokens/{issued['token_uuid']}")
        assert response.status_code == 404
        assert ErrorResponse.model_validate(response.json()).error.code == "token_not_found"

    def test_an_issued_credential_actually_works(self, client: TestClient) -> None:
        """The endpoint mints the same kind of credential the CLI does."""
        secret = client.post(
            "/api/v1/tokens", json={"subject": "ingest", "scopes": ["identify"]}
        ).json()["token"]

        async def lookup(connector: PostgresConnector) -> object:
            async with connector.session() as session:
                return await SqlAlchemyTokenStore(session).find_by_hash(hash_token(secret))

        found = _run(lookup)
        assert found is not None
        assert found.usable() is True  # type: ignore[attr-defined]


class TestAuthorisation:
    @pytest.fixture
    def reviewer_client(self, app: FastAPI) -> Iterator[TestClient]:
        app.dependency_overrides[get_principal] = lambda: Principal(
            token_uuid=uuid4(),
            subject="reviewer@example.com",
            kind="user",
            scopes=frozenset({Scope.REVIEW}),
        )
        with TestClient(app) as client:
            yield client

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/accounts"),
            ("get", "/api/v1/accounts"),
            ("post", "/api/v1/tokens"),
            ("get", "/api/v1/tokens"),
        ],
    )
    def test_administration_requires_the_admin_scope(
        self, reviewer_client: TestClient, method: str, path: str
    ) -> None:
        response = (
            reviewer_client.post(path, json={}) if method == "post" else reviewer_client.get(path)
        )
        assert response.status_code == 403
        assert ErrorResponse.model_validate(response.json()).error.code == "not_authorised"

    def test_changing_your_own_password_needs_no_admin_scope(
        self, reviewer_client: TestClient
    ) -> None:
        response = reviewer_client.post(
            "/api/v1/me/password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        )
        # 404 because no such account exists here — not 403, which is the point.
        assert response.status_code == 404


class TestAuditing:
    def test_every_administrative_action_is_recorded(self, client: TestClient) -> None:
        created = _create(client)
        client.post(
            f"/api/v1/accounts/{created['user_uuid']}/password",
            json={"password": "another-long-one"},
        )
        client.post(f"/api/v1/accounts/{created['user_uuid']}/disable")
        issued = client.post(
            "/api/v1/tokens", json={"subject": "ingest", "scopes": ["identify"]}
        ).json()
        client.delete(f"/api/v1/tokens/{issued['token_uuid']}")

        async def actions(connector: PostgresConnector) -> list[str]:
            async with connector.session() as session:
                rows = await session.execute(
                    text("SELECT action, actor_identifier FROM audit_events ORDER BY occurred_at")
                )
                return [f"{r.action}:{r.actor_identifier}" for r in rows.all()]

        recorded = _run(actions)
        assert recorded == [
            f"account_created:{ADMIN_EMAIL}",
            f"account_password_changed:{ADMIN_EMAIL}",
            f"account_disabled:{ADMIN_EMAIL}",
            f"token_issued:{ADMIN_EMAIL}",
            f"token_revoked:{ADMIN_EMAIL}",
        ]

    def test_the_audit_never_records_a_secret(self, client: TestClient) -> None:
        secret = client.post(
            "/api/v1/tokens", json={"subject": "ingest", "scopes": ["identify"]}
        ).json()["token"]
        _create(client)

        async def details(connector: PostgresConnector) -> str:
            async with connector.session() as session:
                rows = await session.execute(text("SELECT details FROM audit_events"))
                return str(rows.all())

        recorded = _run(details)
        assert secret not in recorded
        assert PASSWORD not in recorded
