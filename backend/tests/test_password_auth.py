"""Password accounts and sign-in."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.dependencies import get_authentication_service
from app.api.v1.security import get_principal
from app.api.v1.sessions import router as session_router
from app.connectors.postgres import PostgresConnector, metadata
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.connectors.postgres.users import SqlAlchemyUserStore
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.auth import Principal, Scope, hash_token
from app.domain.repositories import ConflictError
from app.domain.users import (
    AuthenticationFailed,
    PasswordPolicyError,
    User,
    UserStore,
    check_password_policy,
    hash_password,
    normalise_email,
    verify_password,
)
from app.services.authentication import AuthenticationService

from .conftest import INTEGRATION_DSN

PASSWORD = "a-sufficiently-long-password"


class TestPasswordPolicy:
    def test_a_short_password_is_refused(self) -> None:
        with pytest.raises(PasswordPolicyError, match="at least 12"):
            check_password_policy("short")

    def test_whitespace_is_not_a_password(self) -> None:
        with pytest.raises(PasswordPolicyError, match="whitespace"):
            check_password_policy(" " * 20)

    def test_the_email_is_not_a_password(self) -> None:
        with pytest.raises(PasswordPolicyError, match="email address"):
            check_password_policy("admin@example.com", email="admin@example.com")

    def test_a_long_password_is_accepted(self) -> None:
        check_password_policy(PASSWORD, email="someone@example.com")


class TestEmailNormalisation:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("Admin@Example.COM", "admin@example.com"),
            ("  admin@example.com  ", "admin@example.com"),
        ],
    )
    def test_emails_fold_to_one_account(self, given: str, expected: str) -> None:
        assert normalise_email(given) == expected

    @pytest.mark.parametrize("bad", ["", "   ", "nobody", "@example.com", "admin@"])
    def test_unusable_addresses_are_refused(self, bad: str) -> None:
        with pytest.raises(ValueError, match="usable email"):
            normalise_email(bad)


class TestHashing:
    def test_the_plaintext_is_not_recoverable(self) -> None:
        stored = hash_password(PASSWORD)
        assert PASSWORD not in stored
        assert stored.startswith("$argon2")

    def test_the_same_password_hashes_differently_each_time(self) -> None:
        """Distinct salts: two accounts sharing a password must not look alike."""
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_verification_accepts_the_right_password(self) -> None:
        assert verify_password(hash_password(PASSWORD), PASSWORD) is True

    def test_verification_rejects_the_wrong_password(self) -> None:
        assert verify_password(hash_password(PASSWORD), "not the password") is False

    def test_a_missing_account_still_costs_a_verification(self) -> None:
        """Otherwise response timing reveals which addresses are real."""
        real = hash_password(PASSWORD)

        start = time.perf_counter()
        verify_password(real, "wrong")
        with_account = time.perf_counter() - start

        start = time.perf_counter()
        assert verify_password(None, "wrong") is False
        without_account = time.perf_counter() - start

        assert without_account > with_account / 4


@pytest.mark.skipif(INTEGRATION_DSN is None, reason="needs FACEID_TEST_POSTGRES_DSN")
class TestUserStore:
    @pytest_asyncio.fixture
    async def connector(self) -> AsyncIterator[PostgresConnector]:
        assert INTEGRATION_DSN is not None
        connector = PostgresConnector(INTEGRATION_DSN)
        async with connector.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield connector
        async with connector.engine.begin() as connection:
            await connection.execute(text("TRUNCATE users, api_tokens CASCADE"))
        await connector.close()

    def _user(self, email: str = "admin@example.com") -> User:
        return User(
            email=normalise_email(email),
            password_hash=hash_password(PASSWORD),
            scopes=frozenset({Scope.ADMIN, Scope.REVIEW}),
        )

    async def test_satisfies_the_user_store_protocol(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            assert isinstance(SqlAlchemyUserStore(session), UserStore)

    async def test_an_account_round_trips(self, connector: PostgresConnector) -> None:
        user = self._user()
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).add(user)
        async with connector.session() as session:
            found = await SqlAlchemyUserStore(session).find_by_email("ADMIN@example.com")
        assert found is not None
        assert found.email == "admin@example.com"
        assert found.scopes == {Scope.ADMIN, Scope.REVIEW}

    async def test_the_password_is_never_stored_in_the_clear(
        self, connector: PostgresConnector
    ) -> None:
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).add(self._user())
        async with connector.session() as session:
            rows = await session.execute(text("SELECT * FROM users"))
            assert PASSWORD not in str(rows.all())

    async def test_a_duplicate_email_conflicts(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).add(self._user())
        with pytest.raises(ConflictError, match="already exists"):
            async with connector.session() as session:
                await SqlAlchemyUserStore(session).add(self._user())

    async def test_an_unknown_email_is_none(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            assert await SqlAlchemyUserStore(session).find_by_email("nobody@example.com") is None

    async def test_a_malformed_email_is_none_rather_than_an_error(
        self, connector: PostgresConnector
    ) -> None:
        async with connector.session() as session:
            assert await SqlAlchemyUserStore(session).find_by_email("not-an-email") is None

    async def test_a_password_can_be_changed(self, connector: PostgresConnector) -> None:
        user = self._user()
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).add(user)
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).set_password(
                user.user_uuid, hash_password("a completely different password")
            )
        async with connector.session() as session:
            found = await SqlAlchemyUserStore(session).find_by_email(user.email)
        assert found is not None
        assert verify_password(found.password_hash, "a completely different password")
        assert not verify_password(found.password_hash, PASSWORD)

    async def test_an_account_can_be_disabled_and_re_enabled(
        self, connector: PostgresConnector
    ) -> None:
        user = self._user()
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).add(user)
            await SqlAlchemyUserStore(session).set_disabled(user.user_uuid, True)
        async with connector.session() as session:
            found = await SqlAlchemyUserStore(session).find_by_email(user.email)
        assert found is not None and found.active is False


@pytest.mark.skipif(INTEGRATION_DSN is None, reason="needs FACEID_TEST_POSTGRES_DSN")
class TestSignIn:
    @pytest_asyncio.fixture
    async def connector(self) -> AsyncIterator[PostgresConnector]:
        assert INTEGRATION_DSN is not None
        connector = PostgresConnector(INTEGRATION_DSN)
        async with connector.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield connector
        async with connector.engine.begin() as connection:
            await connection.execute(text("TRUNCATE users, api_tokens CASCADE"))
        await connector.close()

    async def _account(self, connector: PostgresConnector, *, disabled: bool = False) -> User:
        user = User(
            email="admin@example.com",
            password_hash=hash_password(PASSWORD),
            scopes=frozenset({Scope.REVIEW}),
            disabled_at=datetime.now(UTC) if disabled else None,
        )
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).add(user)
        return user

    def _service(self, session: object) -> AuthenticationService:
        return AuthenticationService(
            users=SqlAlchemyUserStore(session),  # type: ignore[arg-type]
            tokens=SqlAlchemyTokenStore(session),  # type: ignore[arg-type]
            session_lifetime_seconds=3600,
        )

    async def test_a_correct_password_mints_a_usable_credential(
        self, connector: PostgresConnector
    ) -> None:
        user = await self._account(connector)
        async with connector.session() as session:
            session_result = await self._service(session).sign_in(user.email, PASSWORD)

        assert session_result.token.subject == "admin@example.com"
        assert session_result.token.scopes == {Scope.REVIEW}
        assert session_result.token.user_uuid == user.user_uuid
        assert session_result.token.expires_at is not None

        async with connector.session() as session:
            found = await SqlAlchemyTokenStore(session).find_by_hash(
                hash_token(session_result.secret)
            )
        assert found is not None and found.usable()

    async def test_the_session_secret_is_not_stored(self, connector: PostgresConnector) -> None:
        user = await self._account(connector)
        async with connector.session() as session:
            result = await self._service(session).sign_in(user.email, PASSWORD)
        async with connector.session() as session:
            rows = await session.execute(text("SELECT * FROM api_tokens"))
        assert result.secret not in str(rows.all())

    async def test_a_wrong_password_is_refused(self, connector: PostgresConnector) -> None:
        user = await self._account(connector)
        async with connector.session() as session:
            with pytest.raises(AuthenticationFailed, match="email or password is incorrect"):
                await self._service(session).sign_in(user.email, "wrong password entirely")

    async def test_an_unknown_email_fails_identically(self, connector: PostgresConnector) -> None:
        """Nothing distinguishes a missing account from a wrong password."""
        await self._account(connector)
        async with connector.session() as session:
            with pytest.raises(AuthenticationFailed, match="email or password is incorrect"):
                await self._service(session).sign_in("nobody@example.com", PASSWORD)

    async def test_a_malformed_email_fails_identically(self, connector: PostgresConnector) -> None:
        async with connector.session() as session:
            with pytest.raises(AuthenticationFailed, match="email or password is incorrect"):
                await self._service(session).sign_in("not-an-email", PASSWORD)

    async def test_a_disabled_account_cannot_sign_in(self, connector: PostgresConnector) -> None:
        user = await self._account(connector, disabled=True)
        async with connector.session() as session:
            with pytest.raises(AuthenticationFailed):
                await self._service(session).sign_in(user.email, PASSWORD)

    async def test_signing_in_is_recorded(self, connector: PostgresConnector) -> None:
        user = await self._account(connector)
        async with connector.session() as session:
            await self._service(session).sign_in(user.email, PASSWORD)
        async with connector.session() as session:
            found = await SqlAlchemyUserStore(session).find_by_email(user.email)
        assert found is not None and found.last_login_at is not None

    async def test_changing_the_password_ends_existing_sessions(
        self, connector: PostgresConnector
    ) -> None:
        """The point of changing a password is that the old one stops working."""
        user = await self._account(connector)
        async with connector.session() as session:
            first = await self._service(session).sign_in(user.email, PASSWORD)

        async with connector.session() as session:
            await self._service(session).change_password(user, "an entirely new password")

        async with connector.session() as session:
            found = await SqlAlchemyTokenStore(session).find_by_hash(hash_token(first.secret))
        assert found is not None
        assert found.usable() is False

    async def test_signing_out_revokes_only_that_session(
        self, connector: PostgresConnector
    ) -> None:
        user = await self._account(connector)
        async with connector.session() as session:
            first = await self._service(session).sign_in(user.email, PASSWORD)
        async with connector.session() as session:
            second = await self._service(session).sign_in(user.email, PASSWORD)

        async with connector.session() as session:
            await self._service(session).sign_out(first.token.token_uuid)

        async with connector.session() as session:
            store = SqlAlchemyTokenStore(session)
            gone = await store.find_by_hash(hash_token(first.secret))
            kept = await store.find_by_hash(hash_token(second.secret))
        assert gone is not None and gone.usable() is False
        assert kept is not None and kept.usable() is True


class TestSignInEndpoint:
    """The HTTP surface, against a stub service."""

    @pytest.fixture
    def client(self, settings: object) -> Iterator[TestClient]:
        class StubService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def sign_in(self, email: str, password: str) -> object:
                self.calls.append((email, password))
                if password != PASSWORD:
                    raise AuthenticationFailed("email or password is incorrect")
                from app.domain.auth import new_token
                from app.services.authentication import Session

                record, secret = new_token(
                    "admin@example.com",
                    "user",
                    [Scope.REVIEW],
                    lifetime_days=None,
                    lifetime_seconds=3600,
                )
                return Session(
                    token=record,
                    secret=secret,
                    user=User(
                        email="admin@example.com",
                        password_hash="x",
                        scopes=frozenset({Scope.REVIEW}),
                    ),
                )

            async def sign_out(self, token_uuid: object) -> bool:
                return True

        app = FastAPI()
        install_error_handlers(app)
        app.state.settings = settings
        app.include_router(session_router, prefix="/api/v1")
        app.dependency_overrides[get_authentication_service] = lambda: StubService()
        app.dependency_overrides[get_principal] = lambda: Principal(
            token_uuid=uuid4(), subject="admin@example.com", kind="user", scopes=frozenset()
        )
        with TestClient(app) as test_client:
            yield test_client

    def test_correct_credentials_return_a_token(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/sessions", json={"email": "admin@example.com", "password": PASSWORD}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["token"].startswith("faceid_")
        assert body["subject"] == "admin@example.com"
        assert body["scopes"] == ["review"]

    def test_wrong_credentials_are_a_structured_401(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/sessions", json={"email": "admin@example.com", "password": "wrong-one-here"}
        )
        assert response.status_code == 401
        assert ErrorResponse.model_validate(response.json()).error.code == "sign_in_failed"

    def test_the_password_is_never_echoed(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/sessions", json={"email": "admin@example.com", "password": PASSWORD}
        )
        assert PASSWORD not in response.text

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "not-an-email", "password": PASSWORD},
            {"email": "admin@example.com"},
            {"password": PASSWORD},
            {"email": "admin@example.com", "password": ""},
        ],
    )
    def test_malformed_requests_fail_validation(
        self, client: TestClient, payload: dict[str, str]
    ) -> None:
        response = client.post("/api/v1/sessions", json=payload)
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "validation_error"

    def test_signing_out_returns_no_content(self, client: TestClient) -> None:
        assert client.delete("/api/v1/sessions/current").status_code == 204

    def test_openapi_documents_the_endpoints(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/v1/sessions"]["post"]["responses"]["201"]
        assert "429" in schema["paths"]["/api/v1/sessions"]["post"]["responses"]


@pytest.mark.skipif(
    os.environ.get("FACEID_TEST_REDIS_DSN") is None,
    reason="sign-in throttling needs FACEID_TEST_REDIS_DSN",
)
def test_repeated_failures_are_throttled(settings: object) -> None:
    """A password is guessable in a way a token is not."""
    from contextlib import asynccontextmanager

    from app.connectors.redis import RedisConnector, RedisRateLimiter

    dsn = os.environ["FACEID_TEST_REDIS_DSN"]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        connector = RedisConnector(dsn)
        app.state.rate_limiter = RedisRateLimiter(connector)
        await connector.client.delete("faceid:ratelimit:sign-in:throttle@example.com")
        yield
        await connector.close()

    class AlwaysFails:
        async def sign_in(self, email: str, password: str) -> object:
            raise AuthenticationFailed("email or password is incorrect")

    app = FastAPI(lifespan=lifespan)
    install_error_handlers(app)
    app.state.settings = settings
    app.include_router(session_router, prefix="/api/v1")
    app.dependency_overrides[get_authentication_service] = lambda: AlwaysFails()

    with TestClient(app) as client:
        payload = {"email": "throttle@example.com", "password": "guess-guess-guess"}
        codes = [client.post("/api/v1/sessions", json=payload).status_code for _ in range(12)]

    assert codes.count(401) == 10
    assert codes[-1] == 429
