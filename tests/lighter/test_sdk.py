"""Test authenticated Lighter SDK call handling without importing the SDK."""

import asyncio
import logging

import pytest
from pytest import LogCaptureFixture

from eth_defi.lighter.sdk import (
    LighterAuthTokenError,
    LighterAuthTokenManager,
)


@pytest.mark.timeout(30)
def test_lighter_auth_token_refreshes_before_expiry() -> None:
    """Refresh a reusable token before expiry while calling arbitrary SDK methods.

    1. Create a manager around a fake token factory and monotonic clock.
    2. Call an arbitrary asynchronous operation twice across token expiry.
    3. Assert that the operation receives the rotated token.
    """
    # 1. Create deterministic token and clock stand-ins.
    current_time = [100.0]
    generated_tokens: list[str] = []

    def create_auth_token() -> tuple[str, None]:
        token = f"token-{len(generated_tokens) + 1}"
        generated_tokens.append(token)
        return token, None

    manager = LighterAuthTokenManager(
        token_factory=create_auth_token,
        token_lifetime=10.0,
        refresh_margin=2.0,
        clock=lambda: current_time[0],
    )

    # 2. Call the same generic operation before and near token expiry.
    observed_tokens: list[str] = []

    async def operation(auth_token: str) -> str:
        observed_tokens.append(auth_token)
        return auth_token

    async def exercise() -> tuple[str, str]:
        first_result = await manager.call(operation, operation_name="test read")
        current_time[0] = 109.0
        second_result = await manager.call(operation, operation_name="test read")
        return first_result, second_result

    first_result, second_result = asyncio.run(exercise())

    # 3. The second call receives a newly generated token.
    assert first_result == "token-1"
    assert second_result == "token-2"
    assert observed_tokens == ["token-1", "token-2"]
    assert generated_tokens == ["token-1", "token-2"]


@pytest.mark.timeout(30)
def test_lighter_auth_token_retries_unauthorised_operation(
    caplog: LogCaptureFixture,
) -> None:
    """Retry one rejected token and propagate permanent SDK failures.

    1. Make the first fake SDK call fail with an HTTP 401-shaped exception.
    2. Assert that the manager creates a new token and retries the call once.
    3. Assert that a non-authentication error is propagated without a retry.
    """
    # 1. Create a manager and an operation whose first request is unauthorised.
    generated_tokens: list[str] = []

    def create_auth_token() -> tuple[str, None]:
        token = f"token-{len(generated_tokens) + 1}"
        generated_tokens.append(token)
        return token, None

    manager = LighterAuthTokenManager(
        token_factory=create_auth_token,
        token_lifetime=60.0,
        refresh_margin=0.0,
    )
    observed_tokens: list[str] = []

    async def authorised_after_retry(auth_token: str) -> str:
        observed_tokens.append(auth_token)
        if len(observed_tokens) == 1:
            error = RuntimeError("signed-request-secret")
            setattr(error, "status", 401)
            raise error
        return "ok"

    calls = 0

    async def permanently_failed_operation(_auth_token: str) -> None:
        nonlocal calls
        calls += 1
        raise ValueError("operation failed")

    # 2. A 401 refreshes the token and retries the idempotent operation.
    async def exercise() -> None:
        assert (
            await manager.call(
                authorised_after_retry,
                operation_name="test withdrawal history",
            )
            == "ok"
        )

        # 3. A non-authentication failure is not retried or rewritten.
        with pytest.raises(ValueError, match="operation failed"):
            await manager.call(
                permanently_failed_operation,
                operation_name="test read",
            )

    with caplog.at_level(logging.WARNING, logger="eth_defi.lighter.sdk"):
        asyncio.run(exercise())
    assert observed_tokens == ["token-1", "token-2"]
    assert generated_tokens == ["token-1", "token-2"]
    assert calls == 1
    assert "signed-request-secret" not in caplog.text


@pytest.mark.timeout(30)
def test_lighter_auth_token_error_does_not_expose_factory_error() -> None:
    """Hide SDK token-generation details when a token cannot be created.

    1. Return a fake SDK error containing signed request material.
    2. Assert that the public exception is generic and excludes that material.
    3. Assert that the operation is never called without a valid token.
    """
    # 1. The fake SDK reports an error payload that must not reach the caller.
    secret_error = "signed-request-secret"

    def create_auth_token() -> tuple[None, str]:
        return None, secret_error

    manager = LighterAuthTokenManager(token_factory=create_auth_token)
    calls = 0

    async def operation(_auth_token: str) -> None:
        nonlocal calls
        calls += 1

    # 2. Token-generation failures are normalised without the SDK payload.
    async def exercise() -> None:
        with pytest.raises(LighterAuthTokenError) as raised:
            await manager.call(operation, operation_name="test read")
        assert str(raised.value) == "Could not create a Lighter authentication token"
        assert secret_error not in str(raised.value)

    asyncio.run(exercise())

    # 3. No unauthenticated SDK operation is attempted.
    assert calls == 0
