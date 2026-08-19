"""Rate limiting."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1.security import get_principal, rate_limited
from app.connectors.redis import RateLimit, RedisConnector, RedisRateLimiter
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.auth import Principal, Scope

REDIS_DSN = os.environ.get("FACEID_TEST_REDIS_DSN")

requires_redis = pytest.mark.skipif(
    REDIS_DSN is None, reason="set FACEID_TEST_REDIS_DSN to run rate limit tests"
)


class TestRateLimitValidation:
    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_nonsensical_limit_is_refused(self, bad: int) -> None:
        with pytest.raises(ValueError, match="limit must be"):
            RateLimit(limit=bad, window_seconds=60)

    @pytest.mark.parametrize("bad", [0, -5])
    def test_a_nonsensical_window_is_refused(self, bad: int) -> None:
        with pytest.raises(ValueError, match="window_seconds"):
            RateLimit(limit=10, window_seconds=bad)


@requires_redis
class TestLimiter:
    @pytest_asyncio.fixture
    async def limiter(self) -> AsyncIterator[RedisRateLimiter]:
        assert REDIS_DSN is not None
        connector = RedisConnector(REDIS_DSN)
        limiter = RedisRateLimiter(connector)
        yield limiter
        await connector.close()

    async def test_requests_within_the_limit_are_allowed(self, limiter: RedisRateLimiter) -> None:
        identity = str(uuid4())
        limit = RateLimit(limit=3, window_seconds=60)
        for expected_remaining in (2, 1, 0):
            verdict = await limiter.check(identity, "identify", limit)
            assert verdict.allowed is True
            assert verdict.remaining == expected_remaining

    async def test_the_next_request_is_refused(self, limiter: RedisRateLimiter) -> None:
        identity = str(uuid4())
        limit = RateLimit(limit=2, window_seconds=60)
        await limiter.check(identity, "identify", limit)
        await limiter.check(identity, "identify", limit)

        verdict = await limiter.check(identity, "identify", limit)
        assert verdict.allowed is False
        assert verdict.remaining == 0
        assert 0 < verdict.retry_after_seconds <= 60

    async def test_callers_are_counted_separately(self, limiter: RedisRateLimiter) -> None:
        limit = RateLimit(limit=1, window_seconds=60)
        first, second = str(uuid4()), str(uuid4())
        assert (await limiter.check(first, "identify", limit)).allowed is True
        assert (await limiter.check(second, "identify", limit)).allowed is True
        assert (await limiter.check(first, "identify", limit)).allowed is False

    async def test_actions_are_counted_separately(self, limiter: RedisRateLimiter) -> None:
        identity = str(uuid4())
        limit = RateLimit(limit=1, window_seconds=60)
        assert (await limiter.check(identity, "identify", limit)).allowed is True
        assert (await limiter.check(identity, "enrol", limit)).allowed is True
        assert (await limiter.check(identity, "identify", limit)).allowed is False

    async def test_the_window_expires(self, limiter: RedisRateLimiter) -> None:
        identity = str(uuid4())
        limit = RateLimit(limit=1, window_seconds=1)
        assert (await limiter.check(identity, "identify", limit)).allowed is True
        assert (await limiter.check(identity, "identify", limit)).allowed is False

        await asyncio.sleep(1.2)
        assert (await limiter.check(identity, "identify", limit)).allowed is True

    async def test_hammering_does_not_extend_the_window(self, limiter: RedisRateLimiter) -> None:
        """A refused caller must not be able to reset their own counter."""
        identity = str(uuid4())
        limit = RateLimit(limit=1, window_seconds=60)
        await limiter.check(identity, "identify", limit)
        first_refusal = await limiter.check(identity, "identify", limit)
        later_refusal = await limiter.check(identity, "identify", limit)
        assert later_refusal.retry_after_seconds <= first_refusal.retry_after_seconds

    async def test_reset_clears_a_window(self, limiter: RedisRateLimiter) -> None:
        identity = str(uuid4())
        limit = RateLimit(limit=1, window_seconds=60)
        await limiter.check(identity, "identify", limit)
        assert (await limiter.check(identity, "identify", limit)).allowed is False
        await limiter.reset(identity, "identify")
        assert (await limiter.check(identity, "identify", limit)).allowed is True


@requires_redis
class TestEndpointEnforcement:
    @pytest.fixture
    def principal(self) -> Principal:
        return Principal(
            token_uuid=uuid4(),
            subject="tester",
            kind="user",
            scopes=frozenset({Scope.IDENTIFY}),
        )

    @pytest.fixture
    def client(self, principal: Principal, settings: object) -> Iterator[TestClient]:
        assert REDIS_DSN is not None

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            # The app owns the connection, as it does in production, so it is
            # opened and closed on the same event loop the requests run on.
            connector = RedisConnector(REDIS_DSN)
            app.state.rate_limiter = RedisRateLimiter(connector)
            yield
            await connector.close()

        app = FastAPI(lifespan=lifespan)
        install_error_handlers(app)
        app.state.settings = settings

        @app.get("/limited")
        async def limited(
            _p: Annotated[
                Principal,
                Depends(rate_limited(Scope.IDENTIFY, "identify-test", lambda s: 2)),
            ],
        ) -> dict[str, bool]:
            return {"ok": True}

        app.dependency_overrides[get_principal] = lambda: principal
        with TestClient(app) as test_client:
            yield test_client

    def test_the_limit_is_enforced_over_http(self, client: TestClient) -> None:
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200
        response = client.get("/limited")
        assert response.status_code == 429
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "rate_limited"

    def test_a_refusal_says_when_to_come_back(self, client: TestClient) -> None:
        for _ in range(3):
            response = client.get("/limited")
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0

    def test_a_caller_without_the_scope_is_refused_before_counting(self) -> None:
        """Authorisation is checked first: a 403 must not consume a request."""
        principal = Principal(
            token_uuid=uuid4(), subject="x", kind="user", scopes=frozenset({Scope.REVIEW})
        )
        app = FastAPI()
        install_error_handlers(app)

        @app.get("/limited")
        async def limited(
            _p: Annotated[
                Principal,
                Depends(rate_limited(Scope.IDENTIFY, "identify-test", lambda s: 1)),
            ],
        ) -> dict[str, bool]:
            return {"ok": True}

        app.dependency_overrides[get_principal] = lambda: principal
        with TestClient(app) as client:
            assert client.get("/limited").status_code == 403


def test_an_unconfigured_limiter_fails_open(settings: object) -> None:
    """Better to serve real work than to refuse it, but it must be visible."""
    principal = Principal(
        token_uuid=uuid4(), subject="x", kind="user", scopes=frozenset({Scope.IDENTIFY})
    )
    app = FastAPI()
    install_error_handlers(app)
    app.state.settings = settings

    @app.get("/limited")
    async def limited(
        _p: Annotated[
            Principal, Depends(rate_limited(Scope.IDENTIFY, "identify-test", lambda s: 1))
        ],
    ) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_principal] = lambda: principal
    with TestClient(app) as client:
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200
