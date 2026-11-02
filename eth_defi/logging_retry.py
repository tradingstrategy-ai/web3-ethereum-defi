"""Loggable ``Retry()`` adapter for ``requests`` package"""

import logging
import time

from urllib3 import Retry


class LoggingRetry(Retry):
    """In the case we need to throttle Coingecko or other HTTP API, be verbose about it.

    Example how to use:

    .. code-block:: python

        from requests.sessions import HTTPAdapter

        # Set up dealing with network connectivity flakey
        if retry_policy is None:
            # https://stackoverflow.com/a/35504626/315168
            retry_policy = LoggingRetry(
                total=5,
                backoff_factor=0.1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            session.mount("http://", HTTPAdapter(max_retries=retry_policy))
            session.mount("https://", HTTPAdapter(max_retries=retry_policy))

    To ride out an upstream outage for a fixed time rather than a fixed number
    of attempts, pass ``max_retry_duration``. Live executors use this for
    read-only account valuation, where giving up stops the trading loop:

    .. code-block:: python

        # Exponential backoff of 0, 1, 2, 4 ... seconds, at most 120 seconds
        # per wait, until 10 minutes have passed since the first failure:
        # 0, 1, 2, 4, 8, 16, 32, 64, 120, 120, 120 and a final 113 seconds.
        retry_policy = LoggingRetry(
            total=None,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            max_retry_duration=600,
        )
    """

    def __init__(self, *args, **kwargs):
        self.logger = kwargs.pop("logger", logging.getLogger(__name__))
        self.log_level = kwargs.pop("log_level", logging.WARNING)
        #: Seconds after the first failure during which a request may be retried, or ``None`` for no time limit.
        #:
        #: Backoff sleeps are shortened so that the final attempt starts when
        #: the budget runs out; a failure after that is not retried. The time
        #: of the final request adds to the total, and a ``Retry-After``
        #: header, which ``urllib3`` honours instead of the backoff, can
        #: request a sleep beyond the budget.
        #: Any count limit such as ``total`` also applies; pass ``total=None``
        #: to bound retries by time alone.
        self.max_retry_duration = kwargs.pop("max_retry_duration", None)
        #: ``time.monotonic()`` of the first failed attempt of the current request.
        #:
        #: Set only on the clones that ``urllib3`` creates for each retry, so
        #: the policy mounted on a session is shared between requests unchanged.
        self.retry_started_at = kwargs.pop("retry_started_at", None)
        super().__init__(*args, **kwargs)

    def new(self, **kw: object) -> "LoggingRetry":
        """Create a retry clone that preserves custom logging configuration.

        ``urllib3.Retry.increment()`` returns a cloned retry object after each
        failed attempt. Without carrying these attributes forward, only the
        first retry uses the caller-provided logger and log level.

        The first clone of a request also starts its retry-duration clock.
        """
        retry = super().new(**kw)
        retry.logger = self.logger
        retry.log_level = self.log_level
        retry.max_retry_duration = self.max_retry_duration
        retry.retry_started_at = self.retry_started_at if self.retry_started_at is not None else time.monotonic()
        return retry

    def _get_remaining_retry_duration(self) -> float | None:
        """Seconds left of :py:attr:`max_retry_duration`, or ``None`` without a running budget."""
        if self.max_retry_duration is None or self.retry_started_at is None:
            return None
        return self.max_retry_duration - (time.monotonic() - self.retry_started_at)

    def get_backoff_time(self) -> float:
        """Get the sleep before the next retry, shortened to fit the time budget.

        ``urllib3`` sleeps ``backoff_factor * 2 ** (retries - 1)`` seconds,
        capped at ``backoff_max``, between failed attempts. With a
        :py:attr:`max_retry_duration`, the sleep is cut to the remaining
        budget so that the final attempt starts when the budget runs out
        instead of after it.

        :return:
            Seconds to sleep before the next attempt.
        """
        backoff = super().get_backoff_time()
        remaining = self._get_remaining_retry_duration()
        if remaining is None:
            return backoff
        return max(0.0, min(backoff, remaining))

    def is_exhausted(self) -> bool:
        """Check whether the request must fail instead of being retried.

        ``urllib3`` calls this on each clone after a failed attempt and raises
        ``MaxRetryError`` when it returns ``True``. A request is exhausted when
        any ``urllib3`` count limit is used up or, with a
        :py:attr:`max_retry_duration`, when the time budget has run out.

        :return:
            ``True`` when no further retry is allowed.
        """
        if super().is_exhausted():
            return True
        remaining = self._get_remaining_retry_duration()
        return remaining is not None and remaining <= 0

    def increment(self, method=None, url=None, response=None, error=None, _pool=None, _stacktrace=None):
        if response:
            status = response.status
            reason = response.reason
        else:
            status = None
            reason = str(error)

        url_shortened = url[0:96]

        self.logger.log(self.log_level, f"Retrying: {method} {url_shortened} (status: {status}, reason: {reason})")
        return super().increment(method, url, response, error, _pool, _stacktrace)
