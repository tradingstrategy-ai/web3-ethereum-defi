"""JSON-RPC provider fallback and redundancy mechanisms.

- See :py:class:`FallbackProvider`
"""
import enum
import time
from collections import defaultdict, Counter
from typing import List, Any
import logging

from web3.types import RPCEndpoint, RPCResponse

from eth_defi.middleware import is_retryable_http_exception, DEFAULT_RETRYABLE_EXCEPTIONS, DEFAULT_RETRYABLE_HTTP_STATUS_CODES, DEFAULT_RETRYABLE_RPC_ERROR_CODES
from eth_defi.provider.named import BaseNamedProvider, NamedProvider, get_provider_name

logger = logging.getLogger(__name__)


class FallbackStrategy(enum.Enum):
    """Different supported fallback strategies."""

    #: Automatically switch to the next provider on an error
    #:
    cycle_on_error = "cycle_on_error"


class FallbackProvider(BaseNamedProvider):
    """Fault-tolerance for JSON-RPC requests with multiple providers.

    Fall back to the next provider on the list if a JSON-RPC request fails.
    Contains build-in retry logic in round-robin manner.

    See also

    - :py:func:`eth_defi.middlware.exception_retry_middleware`

    .. warning::

        :py:class:`FallbackProvider` does not call any middlewares installed on the providers themselves.
    """

    def __init__(
        self,
        providers: List[NamedProvider],
        strategy=FallbackStrategy.cycle_on_error,
        retryable_exceptions=DEFAULT_RETRYABLE_EXCEPTIONS,
        retryable_status_codes=DEFAULT_RETRYABLE_HTTP_STATUS_CODES,
        retryable_rpc_error_codes=DEFAULT_RETRYABLE_RPC_ERROR_CODES,
        sleep: float = 5.0,
        backoff: float = 1.6,
        retries: int = 6,
        switchover_noisiness=logging.WARNING,
    ):
        """
        :param providers:
            List of provider we cycle through.

        :param strategy:
            What is the strategy to deal with errors.

            Currently on cycling supported.

        :param retryable_exceptions:
            List of exceptions we can retry.

        :param retryable_status_codes:
            List of HTTP status codes we can retry.

        :param retryable_rpc_error_codes:
            List of GoEthereum error codes we can retry.

        :param sleep:
            Seconds between retries.

        :param backoff:
            Multiplier to increase sleep.

        :param retries:
            How many retries we attempt before giving up.

        :param switchover_noisiness:
            How loud we are about switchover issues.

        """

        super().__init__()

        self.providers = providers

        for provider in providers:
            assert "http_retry_request" not in provider.middlewares, "http_retry_request middleware cannot be used with FallbackProvider"

        #: Currently active provider
        self.currently_active_provider = 0

        self.strategy = strategy

        self.retryable_exceptions = retryable_exceptions
        self.retryable_status_codes = retryable_status_codes
        self.retryable_rpc_error_codes = retryable_rpc_error_codes
        self.sleep = sleep
        self.backoff = backoff
        self.retries = retries

        #: provider number -> API name -> call count mappings.
        # This tracks completed API requests.
        self.api_call_counts = defaultdict(Counter)

        #: provider number-> api method name -> retry counts dict
        self.api_retry_counts = defaultdict(Counter)

        self.retry_count = 0
        self.switchover_noisiness = switchover_noisiness

    def __repr__(self):
        names = [get_provider_name(p) for p in self.providers]
        return f"<Fallback provider {', '.join(names)}>"

    @property
    def endpoint_uri(self):
        """Return the active node URI endpoint.

        For :py:class:`HTTPProvider` compatibility.
        """
        return self.get_active_provider().endpoint_uri

    def switch_provider(self):
        """Switch to next available provider."""
        self.currently_active_provider = (self.currently_active_provider + 1) % len(self.providers)

    def get_active_provider(self) -> NamedProvider:
        """Get currently active provider.

        If this provider fails, we are automatically recycled to the next one.
        """
        return self.providers[self.currently_active_provider]

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        """Make a request.

        - By default use the current active provider

        - If there are errors try cycle through providers and sleep
          between cycles until one provider works
        """
        current_sleep = self.sleep
        for i in range(self.retries + 1):
            provider = self.get_active_provider()
            try:
                # Call the underlying provider
                resp_data = provider.make_request(method, params)

                # We need to manually raise the exception here,
                # likely was raised by Web3.py itself in pre-6.0 versions.
                # If this behavior is some legacy Web3.py behavior and not present anymore,
                # we should replace this with a custom exception.
                # Might be also related to EthereumTester only code paths.
                if "error" in resp_data:
                    # {'jsonrpc': '2.0', 'id': 23, 'error': {'code': -32003, 'message': 'nonce too low'}}
                    # This will trigger exception that will be handled by is_retryable_http_exception()
                    raise ValueError(resp_data["error"])

                # Track API counts
                self.api_call_counts[self.currently_active_provider][method] += 1

                return resp_data

            except Exception as e:
                old_provider_name = get_provider_name(provider)
                if is_retryable_http_exception(
                    e,
                    retryable_rpc_error_codes=self.retryable_rpc_error_codes,
                    retryable_status_codes=self.retryable_status_codes,
                    retryable_exceptions=self.retryable_exceptions,
                ):
                    self.switch_provider()
                    new_provider_name = get_provider_name(self.get_active_provider())

                    if i < self.retries:
                        logger.log(self.switchover_noisiness, "Encountered JSON-RPC retryable error %s when calling method %s.\n" "Switching providers %s -> %s\n" "Retrying in %f seconds, retry #%d / %d", e, method, old_provider_name, new_provider_name, current_sleep, i, self.retries)
                        time.sleep(current_sleep)
                        current_sleep *= self.backoff
                        self.retry_count += 1
                        self.api_retry_counts[self.currently_active_provider][method] += 1
                        continue
                    else:
                        raise  # Out of retries
                logger.info("Will not retry on %s, method %s, as not a retryable exception %s: %s", old_provider_name, method, e.__class__, e)
                raise  # Not retryable exception

        raise AssertionError("Should never be reached")
