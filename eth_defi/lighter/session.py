"""HTTP session management for Lighter API.

Every Lighter API call goes through one of the classes in this module:

- :py:class:`LighterSession` for direct ``requests`` reads, with retry logic and
  rate limiting. Rate limiting is thread-safe using SQLite backend, so the
  session can be shared across multiple threads when using ``joblib.Parallel``
  or similar. The session carries the API URL so that downstream functions do
  not need a separate ``api_url`` argument.
- :py:class:`LighterSDKSession` for the official SDK, which has its own
  ``aiohttp`` client. Create SDK clients with
  :py:func:`eth_defi.lighter.sdk.create_lighter_signer_client` and
  :py:func:`eth_defi.lighter.sdk.create_lighter_api_client`, which install it.
- :py:func:`create_lighter_submission_session` for a pre-signed transaction
  whose outcome is reconciled by hash.

Limits published in Lighter's `rate limit documentation
<https://docs.lighter.xyz/developers/reference/rate-limits.md>`__, read on
2026-11-05:

- ``L2CreateSubAccount`` and ``L2CreatePublicPool``: 2 requests per minute.
- ``L2Withdraw``: 2 requests per minute and 3 per 2 hours, per L1 address.
- Standard accounts: 60 requests per rolling minute, per IP and L1 address.
- Exceeding a limit returns HTTP 429 or HTTP 405.
- The firewall cooldown is a static 60 seconds. No ``Retry-After`` header is
  documented.

Executors on one host share the IP budget, so a limit can be reached without
the current process being busy.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from pyrate_limiter import SQLiteBucket
from requests import Response, Session
from requests_ratelimiter import LimiterAdapter

from eth_defi.lighter.constants import LIGHTER_DEFAULT_REQUESTS_PER_SECOND, LIGHTER_ETHEREUM, LighterAPIConfig
from eth_defi.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Default SQLite database path for rate limiting state.
#:
#: Using SQLite ensures thread-safe rate limiting across multiple threads
#: when using ``joblib.Parallel`` or similar parallel processing.
LIGHTER_RATE_LIMIT_SQLITE_DATABASE: Path = Path("~/.tradingstrategy/lighter/rate-limit.sqlite").expanduser()

#: Default number of retries for API requests.
#:
#: With :py:data:`DEFAULT_BACKOFF_FACTOR` the waits total about 127 seconds, which
#: outlasts Lighter's 60-second firewall cooldown.
DEFAULT_RETRIES: int = 8

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR: float = 0.5

#: Statuses Lighter documents for an exceeded rate limit.
LIGHTER_RATE_LIMIT_STATUSES: frozenset[int] = frozenset({405, 429})

#: Statuses retried for a read.
LIGHTER_RETRYABLE_READ_STATUSES: frozenset[int] = LIGHTER_RATE_LIMIT_STATUSES | {500, 502, 503, 504}

#: The only mutation path :py:class:`LighterSDKSession` retries.
LIGHTER_SEND_TX_PATH: str = "/api/v1/sendTx"

#: Seconds after which a pre-signed submission is not attempted again.
#:
#: Short because signed pool transactions have no USDC slippage limit and their
#: sizing comes from an observation at most 30 seconds old.
LIGHTER_SUBMISSION_DEADLINE: float = 15.0


@dataclass(slots=True, frozen=True)
class LighterSDKRetryPolicy:
    """Backoff settings for :py:class:`LighterSDKSession`.

    Deadlines are measured from the first attempt. No new attempt starts if its
    wait would pass the deadline, so a call overruns by at most one request.
    """

    #: Wait before the first retry, in seconds. Doubles for each retry.
    initial_delay: float = 1.0

    #: Longest wait between attempts, in seconds, including jitter. Longer than
    #: the firewall cooldown.
    max_delay: float = 64.0

    #: Deadline for a read, in seconds.
    read_deadline: float = 130.0

    #: Deadline for a signed transaction, in seconds.
    #:
    #: Short by default, because a transaction may be sized from market data and
    #: a live cycle waits for it.
    transaction_deadline: float = 30.0

    #: Timeout of one attempt, in seconds. The SDK's own default is 300.
    request_timeout: float = 30.0


#: Default policy for reads and live transactions.
LIGHTER_SDK_DEFAULT_POLICY = LighterSDKRetryPolicy()

#: Policy for transactions with a low per-minute limit and no price or sizing
#: exposure, such as sub-account creation. A signed transaction stays valid for
#: longer than this deadline.
LIGHTER_SDK_SLOW_TRANSACTION_POLICY = replace(LIGHTER_SDK_DEFAULT_POLICY, transaction_deadline=300.0)


class LighterSession(Session):
    """A :py:class:`requests.Session` subclass that carries the Lighter API URL.

    All Lighter API functions accept a ``LighterSession`` and read
    :py:attr:`api_url` from it, removing the need for a separate
    ``api_url`` argument on every call.

    Use :py:func:`create_lighter_session` to create instances.
    """

    #: Lighter API base URL (e.g. ``https://mainnet.zklighter.elliot.ai``).
    api_url: str

    #: Deployment whose API and vault namespace this session accesses.
    deployment: LighterAPIConfig

    def __init__(
        self,
        api_url: str | None = None,
        deployment: LighterAPIConfig = LIGHTER_ETHEREUM,
    ):
        super().__init__()
        self.api_url = api_url or deployment.api_url
        self.deployment = deployment

    def __repr__(self) -> str:
        return f"<LighterSession deployment={self.deployment.slug!r} api_url={self.api_url!r}>"


@dataclass(slots=True)
class LighterSDKSession:
    """Exponential backoff for all HTTP traffic of the official Lighter SDK.

    The SDK sends everything through ``ApiClient.rest_client.request()`` using
    ``aiohttp``, so :py:class:`LighterSession` cannot see it. This class replaces
    ``rest_client`` by composition, which keeps the optional SDK out of eth-defi's
    imports. It does not change the SDK's error handling: the final response is
    returned unchanged and the SDK raises its usual ``ApiException``.

    Retrying here rather than around a signer method matters. The SDK does not
    roll its nonce back when a 429 escapes, so a retry above this layer would
    sign again with a later nonce. Here the same signed body is resent, so the
    nonce and the transaction hash do not change.

    Which requests are retried:

    - ``GET`` and ``HEAD``: :py:data:`LIGHTER_RETRYABLE_READ_STATUSES`, and
      connection, timeout or body-read errors.
    - ``POST`` to :py:data:`LIGHTER_SEND_TX_PATH`:
      :py:data:`LIGHTER_RATE_LIMIT_STATUSES` only. The resend carries the same
      nonce, so the sequencer cannot execute it twice. A 5xx or a timeout leaves
      the outcome unknown and is never retried.
    - Anything else: never.

    The SDK holds its per-key nonce lock during a wait. That keeps transactions in
    nonce order, and clients are built per operation, so nothing else is blocked.

    Authenticated Lighter requests can carry a token in the query string, and
    bodies hold signed transactions, so logs only name the method and URL path.

    The native Go signer has its own HTTP client, which this class cannot wrap. It
    is used when an SDK transaction method receives ``api_key_index`` without a
    ``nonce``, and by ``SignerClient.check_client()``. Do not call them that way:
    omit ``api_key_index`` and the Python nonce manager fetches the nonce through
    this class.

    See `Lighter's rate limits <https://docs.lighter.xyz/developers/reference/rate-limits.md>`__
    and the `SDK's REST client <https://github.com/elliottech/lighter-python/blob/main/lighter/rest.py>`__.
    """

    #: The SDK's ``RESTClientObject``.
    rest_client: Any = field(repr=False)

    #: Delays and deadlines.
    policy: LighterSDKRetryPolicy = LIGHTER_SDK_DEFAULT_POLICY

    #: Monotonic clock, injectable for deterministic tests.
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    #: Async sleep, injectable for deterministic tests.
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)

    async def close(self) -> None:
        """Close the wrapped SDK client."""
        await self.rest_client.close()

    async def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: Any = None,
        post_params: Any = None,
        _request_timeout: float | tuple[float, float] | None = None,
    ) -> Any:
        """Send one SDK request, matching ``RESTClientObject.request()``.

        The body is read here because the SDK reads it only after this returns,
        which would leave a truncated read outside the retries. ``RESTResponse``
        caches it, so the SDK's later read returns the same bytes.

        :param method:
            HTTP method.
        :param url:
            Full URL. May contain a token, so it is never logged whole.
        :param headers:
            Request headers.
        :param body:
            JSON body.
        :param post_params:
            Form parameters.
        :param _request_timeout:
            Timeout of one attempt. Defaults to the policy's.
        :return:
            SDK ``RESTResponse`` with its body read.
        """
        method = method.upper()
        path = urlsplit(url).path
        is_read = method in {"GET", "HEAD"}
        if is_read:
            statuses, deadline = LIGHTER_RETRYABLE_READ_STATUSES, self.policy.read_deadline
        elif method == "POST" and path == LIGHTER_SEND_TX_PATH:
            statuses, deadline = LIGHTER_RATE_LIMIT_STATUSES, self.policy.transaction_deadline
        else:
            statuses, deadline = frozenset(), 0.0

        started_at = self.clock()
        attempt = 0
        while True:
            attempt += 1
            failure: Exception | None = None
            try:
                # The SDK mutates headers, so each attempt gets its own copy.
                response = await self.rest_client.request(
                    method,
                    url,
                    headers=dict(headers) if headers is not None else None,
                    body=body,
                    post_params=post_params,
                    _request_timeout=_request_timeout or self.policy.request_timeout,
                )
                await response.read()
                if response.status not in statuses:
                    return response
                reason = f"HTTP {response.status}"
            except (aiohttp.ClientError, TimeoutError) as error:
                if not is_read:
                    raise
                failure = error
                reason = type(error).__name__

            # Jitter stops executors that share an IP address from retrying together.
            delay = min(self.policy.initial_delay * 2 ** (attempt - 1) * random.uniform(1.0, 1.25), self.policy.max_delay)
            if self.clock() - started_at + delay > deadline:
                if failure is not None:
                    raise failure
                return response

            logger.warning("Lighter SDK %s %s got %s on attempt %d, retrying in %.1f seconds", method, path, reason, attempt, delay)
            await self.sleep(delay)


class LighterSubmissionSession(LighterSession):
    """Send one pre-signed transaction, resending it only after a rate limit.

    A pool executor persists a transaction before posting it and reconciles an
    uncertain outcome by hash, so nothing here may replay one. Only
    :py:data:`LIGHTER_RATE_LIMIT_STATUSES` are retried. Connection and read errors,
    and every other status, reach the caller after one attempt.

    No attempt starts after a wall-clock deadline, because a slow refusal must not
    lead to a replay of stale sizing. Each attempt's timeout is the time remaining.
    ``requests`` applies it to the connection and the read separately, so an attempt
    already under way can finish slightly late, which cannot cause a replay. The
    loop lives here rather than in a urllib3 ``Retry``, which only counts attempts
    and which retries a 413 or 503 carrying ``Retry-After`` whatever its status list
    says.

    This session has no client-side limiter: a submission is a single request.

    See the `sendTx API reference <https://apidocs.lighter.xyz/reference/sendtx>`__.
    """

    #: Seconds after which :py:meth:`post` starts no further attempt.
    deadline: float

    def __init__(
        self,
        api_url: str | None = None,
        deployment: LighterAPIConfig = LIGHTER_ETHEREUM,
        deadline: float = LIGHTER_SUBMISSION_DEADLINE,
    ):
        """Create the session without retry adapters.

        :param api_url:
            Optional Lighter API base URL override.
        :param deployment:
            Deployment whose API this session accesses.
        :param deadline:
            Seconds after which :py:meth:`post` starts no further attempt.
        """
        super().__init__(api_url=api_url, deployment=deployment)
        self.deadline = deadline

    def post(self, url: str, data: Any = None, **kwargs: Any) -> Response:
        """Post a signed transaction within the deadline.

        :param url:
            ``sendTx`` URL.
        :param data:
            Form fields holding the signed transaction. Never logged.
        :param kwargs:
            Passed to :py:meth:`requests.Session.post`. The timeout comes from the
            remaining deadline.
        :return:
            The final response. A rate limit that outlasts the deadline is returned
            for the caller to handle.
        """
        expires_at = time.monotonic() + self.deadline
        attempt = 0
        while True:
            attempt += 1
            kwargs["timeout"] = max(expires_at - time.monotonic(), 0.001)
            response = super().post(url, data=data, **kwargs)
            if response.status_code not in LIGHTER_RATE_LIMIT_STATUSES:
                return response
            delay = 2.0 ** (attempt - 1)
            if time.monotonic() + delay >= expires_at:
                return response
            logger.warning("Lighter sendTx got HTTP %d on attempt %d, retrying in %.1f seconds", response.status_code, attempt, delay)
            time.sleep(delay)
            # A suspended process can wake after the deadline.
            if time.monotonic() >= expires_at:
                return response


def create_lighter_session(
    api_url: str | None = None,
    retries: int | None = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_retry_duration: float | None = None,
    requests_per_second: float = LIGHTER_DEFAULT_REQUESTS_PER_SECOND,
    pool_maxsize: int = 32,
    rate_limit_db_path: Path = LIGHTER_RATE_LIMIT_SQLITE_DATABASE,
    deployment: LighterAPIConfig = LIGHTER_ETHEREUM,
) -> LighterSession:
    """Create a :py:class:`LighterSession` configured for Lighter API.

    The session is configured with:

    - The API URL stored in :py:attr:`LighterSession.api_url`
    - Rate limiting to respect Lighter API throttling (thread-safe via SQLite)
    - Retry logic for handling transient errors using exponential backoff

    Only idempotent reads are retried. ``LoggingRetry`` leaves POST out by default.
    SDK traffic is not covered: see :py:class:`LighterSDKSession`.

    The rate limiter uses SQLite backend for thread-safe coordination across
    multiple threads (e.g., when using ``joblib.Parallel`` with threading backend).

    Example::

        from eth_defi.lighter.session import create_lighter_session

        session = create_lighter_session()

    :param api_url:
        Optional Lighter API base URL override. Defaults to the configured
        deployment URL.
    :param retries:
        Maximum number of retry attempts for failed requests.
        ``None`` bounds retries by ``max_retry_duration`` alone.
    :param backoff_factor:
        Backoff factor for exponential retry delays.
    :param max_retry_duration:
        Optional seconds after the first failure during which a request is
        retried. Live executors set this to ride out a Lighter API outage
        instead of failing a fail-closed valuation after a few seconds.
        See :py:attr:`~eth_defi.logging_retry.LoggingRetry.max_retry_duration`.
    :param requests_per_second:
        Maximum requests per second to avoid rate limiting.
    :param pool_maxsize:
        Maximum number of connections to keep in the connection pool.
        Should be at least as large as ``max_workers`` when using parallel requests.
    :param rate_limit_db_path:
        Path to SQLite database for storing rate limit state.
        Using SQLite ensures thread-safe rate limiting across multiple threads.
    :param deployment:
        Deployment whose pool namespace this session accesses. Defaults to the
        backwards-compatible Ethereum deployment.
    :return:
        Configured :py:class:`LighterSession` with rate limiting and retry logic.
    """
    assert retries is not None or max_retry_duration is not None, "retries=None needs max_retry_duration, otherwise requests are retried forever"
    rate_limit_db_path.parent.mkdir(parents=True, exist_ok=True)

    session = LighterSession(api_url=api_url, deployment=deployment)

    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=sorted(LIGHTER_RETRYABLE_READ_STATUSES),
        respect_retry_after_header=True,
        logger=logger,
        max_retry_duration=max_retry_duration,
    )

    # LimiterAdapter combines rate limiting with retry logic.
    # SQLite bucket ensures thread-safe rate limiting across all threads sharing this session.
    adapter = LimiterAdapter(
        per_second=requests_per_second,
        max_retries=retry_policy,
        pool_connections=pool_maxsize,
        pool_maxsize=pool_maxsize,
        bucket_class=SQLiteBucket,
        bucket_kwargs={"path": str(rate_limit_db_path)},
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def create_lighter_submission_session(
    api_url: str | None = None,
    deployment: LighterAPIConfig = LIGHTER_ETHEREUM,
    deadline: float = LIGHTER_SUBMISSION_DEADLINE,
) -> LighterSubmissionSession:
    """Create a session for one pre-signed, persisted transaction.

    :param api_url:
        Optional Lighter API base URL override.
    :param deployment:
        Deployment whose API this session accesses.
    :param deadline:
        Seconds after which ``post()`` starts no further attempt.
    :return:
        Session whose ``post()`` retries rate limits only, within the deadline.
    """
    return LighterSubmissionSession(api_url=api_url, deployment=deployment, deadline=deadline)
