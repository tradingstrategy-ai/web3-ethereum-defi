"""Authenticated Lighter SDK call helpers.

The official Lighter SDK expects callers to create and pass a short-lived auth
token to each authenticated request. This module keeps that SDK optional and
provides a small adapter that refreshes the token before expiry and retries a
call once after an ``UnauthorizedException`` response. The operation receives
the token explicitly, so the helper works with any authenticated SDK method and
does not need to know its parameter names.

The helper is asynchronous because the official Lighter SDK exposes these
operations only as coroutines; it does not introduce asynchronous HTTP into
the rest of eth-defi.

Do not wrap a non-idempotent order or withdrawal submission unless the caller
can prove that an unauthorised response means the request was not accepted.
Use this helper for read-only follow-up calls, such as withdrawal-history
polling, where replaying the request is safe.

Authoritative Lighter documentation:

- Auth tokens: https://apidocs.lighter.xyz/docs/authentication
- Transaction API: https://github.com/elliottech/lighter-python/blob/main/docs/TransactionApi.md
"""

import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: The Lighter SDK currently defaults auth tokens to ten minutes.
DEFAULT_LIGHTER_AUTH_TOKEN_TIMEOUT = 10 * 60.0

#: Refresh a token before its nominal expiry to allow for request latency.
DEFAULT_LIGHTER_AUTH_TOKEN_REFRESH_MARGIN = 30.0


class LighterAuthTokenError(RuntimeError):
    """Raised when an authenticated Lighter SDK token cannot be created."""


def is_lighter_unauthorized_exception(error: BaseException) -> bool:
    """Return whether an SDK exception indicates an expired or rejected token.

    Lighter SDK releases have exposed the HTTP status as either ``status`` or
    ``status_code`` and use ``UnauthorizedException`` for the generated error
    type. Checking these representations keeps this adapter independent of a
    particular SDK release while avoiding a hard dependency on the optional
    ``lighter`` package.

    :param error:
        Exception raised by an authenticated SDK operation.
    :return:
        ``True`` for a Lighter HTTP 401 or ``UnauthorizedException``.
    """
    status = getattr(error, "status", None)
    status_code = getattr(error, "status_code", None)
    response_status = getattr(getattr(error, "response", None), "status_code", None)
    return status in (401, "401") or status_code in (401, "401") or response_status in (401, "401") or type(error).__name__ == "UnauthorizedException"


@dataclass(slots=True)
class LighterAuthTokenManager:
    """Manage and retry short-lived auth tokens for asynchronous SDK calls.

    The manager stores only the current token in memory. ``token_factory`` must
    call the SDK's token-generation method and return its ``(token, error)``
    tuple. ``call`` passes the token to an arbitrary asynchronous operation,
    proactively refreshes it before expiry, and retries one rejected token.
    Token values and SDK error payloads are deliberately excluded from
    representations and logs.

    The token lifetime must match the expiry requested from the SDK. The
    default is ten minutes, matching the current Lighter SDK default; callers
    can use a shorter lifetime in tests to exercise rotation.

    """

    #: Zero-argument SDK callback returning ``(auth_token, error)``.
    token_factory: Callable[[], tuple[str | None, object | None]] = field(repr=False)

    #: Token lifetime in seconds.
    token_lifetime: float = DEFAULT_LIGHTER_AUTH_TOKEN_TIMEOUT

    #: Seconds before expiry at which the current token is refreshed.
    refresh_margin: float = DEFAULT_LIGHTER_AUTH_TOKEN_REFRESH_MARGIN

    #: Monotonic clock, injectable for deterministic tests.
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    #: Current bearer token, deliberately excluded from representations.
    _auth_token: str | None = field(default=None, init=False, repr=False)

    #: Monotonic expiry time for the current token.
    _expires_at: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate token timing settings at construction time."""
        if not math.isfinite(self.token_lifetime) or self.token_lifetime <= 0:
            raise ValueError("token_lifetime must be a finite positive number")
        if not math.isfinite(self.refresh_margin) or self.refresh_margin < 0:
            raise ValueError("refresh_margin must be a finite non-negative number")

    def _refresh_token(self) -> str:
        """Create a new token without exposing SDK error payloads."""
        self._auth_token = None
        self._expires_at = 0.0
        try:
            auth_token, error = self.token_factory()
        except Exception as error:  # noqa: BLE001 - SDK versions expose varied errors
            logger.warning(
                "Lighter authentication token creation failed (%s)",
                type(error).__name__,
            )
            raise LighterAuthTokenError("Could not create a Lighter authentication token") from None
        if error is not None or not isinstance(auth_token, str) or not auth_token:
            logger.warning("Lighter authentication token creation returned no token")
            raise LighterAuthTokenError("Could not create a Lighter authentication token")
        self._auth_token = auth_token
        self._expires_at = self.clock() + self.token_lifetime
        return auth_token

    def _get_token(self) -> str:
        """Return a usable token, refreshing it near its expiry."""
        if self._auth_token is None or self.clock() >= self._expires_at - self.refresh_margin:
            return self._refresh_token()
        return self._auth_token

    async def call(
        self,
        operation: Callable[[str], Awaitable[T]],
        *,
        operation_name: str = "Lighter SDK call",
    ) -> T:
        """Call an authenticated SDK operation with token refresh and retry.

        ``operation`` is invoked with the current auth token. Only exceptions
        recognised by :func:`is_lighter_unauthorized_exception` are retried;
        application errors and transport failures propagate unchanged. A
        retry warning contains no token, private key or SDK exception payload.

        :param operation:
            Async SDK call accepting an auth token.
        :param operation_name:
            Non-secret description used in retry logs.
        :return:
            The SDK operation's result.
        """
        try:
            return await operation(self._get_token())
        except Exception as error:  # noqa: BLE001 - SDK exception type is optional
            if not is_lighter_unauthorized_exception(error):
                raise

        logger.warning(
            "Lighter authentication token rejected for %s; refreshing and retrying once",
            operation_name,
        )
        return await operation(self._refresh_token())
