"""JSON-RPC provider fallback and redundancy mechanisms.

- See :py:class:`FallbackProvider`
"""

import enum
import logging
import random
import time
from collections import Counter, defaultdict
from pprint import pformat
from typing import Any, cast
from requests.exceptions import HTTPError

from web3 import Web3
from web3.types import RPCEndpoint, RPCResponse

from eth_defi.event_reader.fast_json_rpc import get_last_headers
from eth_defi.middleware import DEFAULT_RETRYABLE_EXCEPTIONS, DEFAULT_RETRYABLE_HTTP_STATUS_CODES, DEFAULT_RETRYABLE_RPC_ERROR_CODES, ProbablyNodeHasNoBlock, is_retryable_http_exception, SomeCrappyRPCProviderException
from eth_defi.provider.named import BaseNamedProvider, NamedProvider, get_provider_name
from eth_defi.compat import WEB3_PY_V7

logger = logging.getLogger(__name__)


class ExtraValueError(ValueError):
    """A ValueError that is used to signal that the RPC response was not valid.

    Add extra debugging.
    """

    def __init__(self, *args, **kwargs):  # real signature unknown
        self.extra_help = kwargs.pop("extra_help", "")
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"{super().__repr__()}\n{self.extra_help}"


class FallbackStrategy(enum.Enum):
    """Different supported fallback strategies."""

    #: Automatically switch to the next provider on an error
    #:
    cycle_on_error = "cycle_on_error"


def _check_provider_middlewares_compat(provider):
    """Check if provider has conflicting retry middleware with v6/v7 compatibility.

    In v7, retry is handled via ExceptionRetryConfiguration on provider,
    not middleware. This function handles both cases.
    """
    if WEB3_PY_V7:
        # v7: Check for provider-level retry configuration instead of middleware
        if hasattr(provider, "exception_retry_configuration") and provider.exception_retry_configuration is not None:
            msg = f"Provider {get_provider_name(provider)} {type(provider)} has exception_retry_configuration enabled. This may interfere with FallbackProvider's retry logic. Make sure you disable it before using FallbackProvider."
            logger.warning(msg)
            raise AssertionError(msg)

        # v7: Middleware checking is less relevant but we can still check if middlewares exist
        if hasattr(provider, "middlewares"):
            middleware_names = []
            try:
                # Try to get middleware names - structure might be different in v7
                if hasattr(provider.middlewares, "__iter__"):
                    for middleware in provider.middlewares:
                        if hasattr(middleware, "__name__"):
                            middleware_names.append(middleware.__name__)
                        elif hasattr(middleware, "__class__"):
                            middleware_names.append(middleware.__class__.__name__)

                # Check for known problematic middleware names
                problematic_names = ["http_retry_request", "exception_retry", "retry_middleware"]
                for name in problematic_names:
                    if any(name in str(mw_name).lower() for mw_name in middleware_names):
                        msg = f"Provider {get_provider_name(provider)} may have retry middleware '{name}' which could interfere with FallbackProvider. Please make sure the fallback provider is not installed on a provider having existing middleware"
                        logger.warning(msg)

                        # Make sure this cannot happen in newer versions,
                        # and the code is set up properly
                        raise AssertionError(msg)

            except Exception as e:
                # If we can't inspect middlewares, just log and continue
                logger.debug(f"Could not inspect middlewares for provider {get_provider_name(provider)}: {e}")
    else:
        # v6: Original behavior
        if hasattr(provider, "middlewares") and "http_retry_request" in provider.middlewares:
            raise AssertionError("http_retry_request middleware cannot be used with FallbackProvider")


def _is_tac_provider_madness(error_json_payload: dict) -> bool:
    """F**king RPC providers and their shit"""
    # TAC WTF
    if error_json_payload:
        if "reason: call rate limit exhausted" in str(error_json_payload):
            # Uses HTTP 200 to throttle, not behaving sane
            return True

    return False


class FallbackProvider(BaseNamedProvider):
    """Fault-tolerance for JSON-RPC requests with multiple providers.

    Fall back to the next provider on the list if a JSON-RPC request fails.
    Contains build-in retry logic in round-robin manner.
    We will also recover from situations when we suspect the node does not
    have the block data we are asking yet (but should have shorty).

    See also

    - :py:func:`eth_defi.middlware.exception_retry_middleware`

    - :py:func:`eth_defi.middlware.ProbablyNodeHasNoBlock`

    .. note::

        :py:class:`FallbackProvider` does not call any middlewares installed on the providers themselves.
    """

    def __init__(
        self,
        providers: list[NamedProvider],
        strategy=FallbackStrategy.cycle_on_error,
        retryable_exceptions=DEFAULT_RETRYABLE_EXCEPTIONS,
        retryable_status_codes=DEFAULT_RETRYABLE_HTTP_STATUS_CODES,
        retryable_rpc_error_codes=DEFAULT_RETRYABLE_RPC_ERROR_CODES,
        sleep: float = 5.0,
        backoff: float = 1.6,
        retries: int = 6,
        state_missing_switch_over_delay: float = 12.0,
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

        :param state_missing_switch_over_delay:
            If we encounter state missing condition at node, what is the minimum time (seconds) we wait before trying to switch to next node.

            See code comments for details.

        """

        super().__init__()

        self.providers = providers

        # Use compatibility function instead of direct assertion
        for provider in providers:
            _check_provider_middlewares_compat(provider)

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

        # Wait 12 seconds for block missing errors
        self.state_missing_switch_over_delay = 12.0

    def __repr__(self):
        names = [get_provider_name(p) for p in self.providers]
        return f"<Fallback provider {', '.join(names)}>"

    @property
    def endpoint_uri(self):
        """Return the active node URI endpoint.

        For :py:class:`HTTPProvider` compatibility.
        """
        return self.get_active_provider().endpoint_uri

    def has_multiple_providers(self) -> bool:
        """Have we configured multiple providers"""
        return len(self.providers) >= 2

    def reset_switch(self):
        """Reset the provider switch to the first provider.

        - Assume we have main provider and more expensive backup providers
        - Try to switch back to the main provider if we have switched away due to a temporary error
        - Used in batch scan tasks
        """

        provider = self.get_active_provider()
        old_provider_name = get_provider_name(provider)
        self.currently_active_provider = 0
        new_provider_name = get_provider_name(self.get_active_provider())

        if old_provider_name != new_provider_name:
            logger.log(self.switchover_noisiness, "Reset switch toggled for RPC providers %s -> %s\n", old_provider_name, new_provider_name)

    def switch_provider(self, log_level: int = None, randomise=False, cause: str = "<not specified>"):
        """Switch to next available provider.

        :param log_level:
            Logging level to be verbose about the switch over

        :param random:
            If set switch to a random provider instead of cycling.
        """
        provider = self.get_active_provider()
        old_provider_name = get_provider_name(provider)
        if randomise:
            self.currently_active_provider = (self.currently_active_provider + 1) % len(self.providers)
        else:
            self.currently_active_provider = random.randint(0, len(self.providers) - 1)

        new_provider_name = get_provider_name(self.get_active_provider())

        if log_level is None:
            log_level = self.switchover_noisiness

        if old_provider_name != new_provider_name:
            logger.log(log_level, "Switched RPC providers %s -> %s, cause: %s\n", old_provider_name, new_provider_name, cause)
        else:
            logger.log(log_level, "Only 1 RPC provider configured: %s, cannot switch, sleeping and hoping the issue resolves itself", old_provider_name)

    def get_active_provider(self) -> NamedProvider:
        """Get currently active provider.

        If this provider fails, we are automatically recycled to the next one.
        """
        try:
            return self.providers[self.currently_active_provider]
        except IndexError as e:
            raise IndexProvider(f"Currently active provider index {self.currently_active_provider} is out of range for configured providers {len(self.providers)}") from e

    def get_total_api_call_counts(self) -> dict[str, int]:
        """Get API call coubst across all providers"""
        total = Counter()
        for provider, count_dict in self.api_call_counts.items():
            for method, count in count_dict.items():
                total[method] += count
        return total

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        """Make a request.

        - By default use the current active provider

        - If there are errors try cycle through providers and sleep
          between cycles until one provider works

        - Use a special "ignore_error" parameter to skip retries,
          if given in ``eth_call`` payload.
        """

        # The caller has requested not to retry.
        # Set in EncodedCall.call(ignore_error=True)
        ignore_error = silent_error = False
        param_1 = params[0] if isinstance(params, (tuple, list)) and len(params) > 0 else None
        if param_1 and isinstance(param_1, dict):
            ignore_error = param_1.pop("ignore_error", False)
            if ignore_error:
                # Don't pass the flag to RPC
                params = [param_1, *params[1:]]

            silent_error = param_1.pop("silent_error", False)
            if silent_error:
                # Don't pass the flag to RPC
                params = [param_1, *params[1:]]

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
                error_json_payload = resp_data.get("error")

                if _is_tac_provider_madness(error_json_payload):
                    raise SomeCrappyRPCProviderException(str(error_json_payload))

                if error_json_payload:
                    # eth_defi.provider.fallback.ExtraValueError: {'code': -32090, 'message': 'Too many requests, reason: call rate limit exhausted, retry in 10s', 'data': {'trace_id': '442bbec5e0e72f6ead9472c52ee9ddb6'}}

                    # {'jsonrpc': '2.0', 'id': 23, 'error': {'code': -32003, 'message': 'nonce too low'}}
                    # This will trigger exception that will be handled by is_retryable_http_exception().
                    # We add extra error message payload to make the exception more understandable in common error situations,
                    # while still maintaining the compatibility with vanilla ValueError()
                    headers = get_last_headers()

                    raise ExtraValueError(
                        error_json_payload,
                        extra_help=f"Error in JSON-RPC response:\n{resp_data['error']}\nignore_error: {ignore_error}\nMethod: {method}\nParams: {pformat(params)}\nReply headers: {pformat(headers)}",
                    )

                _check_faulty_rpc_response(self, method, params, resp_data)

                # Track succeed API counts,
                # see test_fallback_single_fault
                self.api_call_counts[self.currently_active_provider][method] += 1

                return resp_data

            except Exception as e:
                # dRPC hack, as it is giving out it's custom error messages
                if isinstance(e, HTTPError):
                    if e.response is not None and e.response.status_code == 400:
                        # Likely '{"id":4,"jsonrpc":"2.0","error":{"message":"Can\'t route your request to suitable provider, if you specified certain providers revise the list","code":12}}'
                        # You need to de-select this RPC provider.
                        # We will log this as an error, but the is_retryable_http_exception() should try another provider
                        name = get_provider_name(provider)
                        logger.error("Provider %s: %s", name, e.response.text)

                # Honour eth eth_call() payload data and don't try retry, logging, etc.
                if ignore_error:
                    raise

                if is_retryable_http_exception(
                    e,
                    retryable_rpc_error_codes=self.retryable_rpc_error_codes,
                    retryable_status_codes=self.retryable_status_codes,
                    retryable_exceptions=self.retryable_exceptions,
                    method=method,
                    params=params,
                ):
                    if self.has_multiple_providers():
                        self.switch_provider()

                    if i < self.retries:
                        # Black messes up string new lines here
                        # See https://github.com/psf/black/issues/1837
                        headers = get_last_headers()
                        logger.log(
                            self.switchover_noisiness,
                            "Encountered JSON-RPC retryable error %s\nWhen calling RPC method: %s%s\nHeaders are: %s\nRetrying in %f seconds, retry #%d / %d",
                            e,
                            method,
                            params,
                            pformat(headers),
                            current_sleep,
                            i + 1,
                            self.retries,
                        )
                        time.sleep(current_sleep)
                        current_sleep *= self.backoff
                        self.retry_count += 1
                        self.api_retry_counts[self.currently_active_provider][method] += 1
                        continue
                    else:
                        raise  # Out of retries

                if not silent_error:
                    logger.info("Will not retry, method %s, as not a retryable exception %s: %s, params %s", method, e.__class__, e, params)

                raise  # Not retryable exception

        raise AssertionError("Should never be reached")


def _check_faulty_rpc_response(
    provider: NamedProvider,
    method: str,
    params: list,
    resp_data: dict,
):
    """Raise an exception on certain bad result conditions.

    We cannot raise this exception during the result format phase,
    because we are outside the fallover logic.
    """

    # A special case of eth_call returning empty result.
    # This happens if you call a smart contract for a block number
    # for which the node does not yet have a data or is still processing data.
    # This happens often on low-quality RPC providers (Ankr)
    # that route your call between different nodes between subsequent calls and those nodes
    # see a different state of EVM.
    # Down the line, not in middleware stack, this would lead to BadFunctionCallOutput
    # output. We work around this by detecting this conditino in middleware
    # stack and trigger middleware fallover node switch if the condition is detected.
    #
    if method == "eth_call":
        # WTF? error
        # ***'code': -32000, 'message': 'state transitaion failed: inverted_index(v1-accounts.0-64.ef) at (0000000000000000000000000000000000000000, 5) returned value 0, but it out-of-bounds 100000000-5501010835. it may signal that .ef file is broke - can detect by `erigon seg integrity --check=InvertedIndex`, or re-download files'***
        args, block_identifier = params
        if block_identifier != "latest":
            result = resp_data["result"]
            if result == "0x" or len(result) == 0:
                # eth_call returned empty response,
                # assume node does not have data yet,
                # switch to another node, wait some extra time
                # to ensure it gets blocks
                # current_sleep = max(self.state_missing_switch_over_delay, current_sleep)
                headers = get_last_headers()
                if block_identifier.startswith("0x"):
                    bi_str = int(block_identifier, 16)
                else:
                    bi_str = block_identifier
                name = get_provider_name(provider)

                raise ProbablyNodeHasNoBlock(f"Empty 0x response for a smart contract call on chain. Provider: {name} Node lacked state data when doing eth_call for block {bi_str}?\nLast response headers\n{pformat(headers)}")

    # BlockNotFound exception gets applied only later with the formatters,
    # so we need to trigger fallover here.
    # LlamaNodes.com: web3.exceptions.BlockNotFound: Block with id: '0x2e4d582' not found.
    if method in (
        "eth_getBlockByNumber",
        "eth_getBlockByHash",
    ):
        block_identifier, *other_args = params
        result = resp_data["result"]
        if result in ("0x", None):
            # eth_call returned empty response,
            # assume node does not have data yet,
            # switch to another node, wait some extra time
            # to ensure it gets blocks
            # current_sleep = max(self.state_missing_switch_over_delay, current_sleep)
            headers = get_last_headers()
            name = get_provider_name(provider)
            raise ProbablyNodeHasNoBlock(f"Node did not have data for block {block_identifier} when calling {method}.\nProvider: {name}\nResponse headers are: {pformat(headers)}")


def get_fallback_provider(web3: Web3) -> FallbackProvider:
    """Get the fallback provider of a Wen3 instance.

    Can be nested in :py:class:`eth_defi.provider.mev_block.MEVBlockerProvider`.

    :param web3:
        Web3 instance

    :raise AssertionError:
        If there is no fallback provider available
    """
    provider = web3.provider
    if isinstance(provider, FallbackProvider):
        return cast(FallbackProvider, provider)

    # MEVBlockerProvider
    call_provider = getattr(provider, "call_provider", None)
    if call_provider:
        return cast(FallbackProvider, call_provider)

    raise AssertionError(f"Does not know how fallback provider is configured: {[provider]}")
