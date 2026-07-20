"""Configuring and managing multiple JSON-RPC provider connections.

See :ref:`multi rpc` tutorial for details.
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from eth_account.signers.local import LocalAccount
from requests.adapters import HTTPAdapter
from urllib3.util import Retry, Url, parse_url
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.compat import clear_middleware, create_http_provider
from eth_defi.event_reader.fast_json_rpc import patch_provider, patch_web3
from eth_defi.middleware import static_call_cache_middleware
from eth_defi.provider.anvil import _get_anvil_launch_metadata, is_anvil
from eth_defi.provider.broken_provider import set_block_tip_latency
from eth_defi.provider.fallback import ChainIdMismatch, FallbackProvider
from eth_defi.provider.mev_blocker import MEVBlockerProvider
from eth_defi.provider.named import NamedProvider, get_provider_name
from eth_defi.provider.rpcdb import RPCRequestStats, normalise_rpc_error
from eth_defi.utils import get_url_domain

logger = logging.getLogger(__name__)


class MultiProviderConfigurationError(Exception):
    """Could not parse URL soup for configuring web3"""


class MultiProviderWeb3(Web3):
    """A web3 instance that knows about multiple RPC endpoints it is using.

    - Can use :py:class:`eth_defi.provider.mev_blocker.MEVBlockerProvider`
      for making transactions to prevent frontrunning

    - There might be several call (read) providers for reading on-chain data
      with fallbacks using :py:class:`eth_defi.provider.fallback.FallbackProvider`

    See

    - :ref:`multi rpc` for details

    - :py:func:`create_multi_provider_web3`

    """

    def get_active_transact_provider(self) -> NamedProvider:
        """Get active transact provider.

        Can be a call provider if not configured.
        """
        provider = self.provider
        if isinstance(provider, MEVBlockerProvider):
            return provider.transact_provider

        return self.get_active_call_provider()

    def get_configured_transact_provider(self) -> MEVBlockerProvider | None:
        """Get configured transact provider."""
        provider = self.provider
        if isinstance(provider, MEVBlockerProvider):
            return provider

        return None

    def get_active_call_provider(self) -> NamedProvider:
        """Get active call provider."""
        provider = self.provider
        if isinstance(provider, MEVBlockerProvider):
            fallback_provider = provider.call_provider
            assert isinstance(fallback_provider, FallbackProvider), f"Got: {fallback_provider}"
            return fallback_provider.get_active_provider()
        else:
            assert isinstance(provider, FallbackProvider), f"Got: {provider}"
            return provider.get_active_provider()

    def get_fallback_provider(self) -> FallbackProvider:
        """Get the fallback provider multiplexer."""
        provider = self.provider
        if isinstance(provider, MEVBlockerProvider):
            fallback_provider = provider.call_provider
            assert isinstance(fallback_provider, FallbackProvider), f"Got: {fallback_provider}"
            return fallback_provider
        else:
            assert isinstance(provider, FallbackProvider), f"Got: {provider}"
            return provider

    def switch_to_next_call_provider(self):
        """Recycles to the next call provider (if available)."""
        self.get_fallback_provider().switch_provider()

    def get_api_call_counts(self) -> Dict[str, int]:
        """How many times different APIs where called.

        :return:
            RPC endpoint name, call count dict
        """
        return self.get_fallback_provider().get_total_api_call_counts()

    def set_rpc_request_stats(self, stats: RPCRequestStats | None) -> None:
        """Attach request accounting to the fallback call provider.

        :param stats:
            Phase or subprocess-task accumulator, or ``None`` to detach it.
        """

        self.get_fallback_provider().set_rpc_request_stats(stats)


def _apply_anvil_launch_metadata(provider: HTTPProvider) -> None:
    """Attach Anvil launch metadata to a provider when available.

    ``create_multi_provider_web3()`` often receives only the local Anvil
    ``localhost`` URL. The provider itself needs launch metadata for retry logs
    to show the original fork chain id and upstream RPC providers.

    :param provider:
        HTTP provider created for a JSON-RPC endpoint.
    """

    metadata = _get_anvil_launch_metadata(str(provider.endpoint_uri))
    if metadata is None:
        return

    provider.anvil_chain_id = metadata.chain_id  # type: ignore[attr-defined]
    provider.anvil_upstream_rpc_urls = metadata.upstream_rpc_urls  # type: ignore[attr-defined]
    provider.anvil_fork_block_number = metadata.fork_block_number  # type: ignore[attr-defined]
    provider.anvil_effective_fork_url = metadata.effective_fork_url  # type: ignore[attr-defined]


def create_multi_provider_web3(
    configuration_line: str,
    fallback_sleep=5.0,
    fallback_backoff=1.25,
    request_kwargs: Optional[Any] = None,
    session: Optional[Any] = None,
    switchover_noisiness=logging.WARNING,
    default_http_timeout=(3.0, 60.0),
    retries: int = 6,
    hint: Optional[str] = "",
    unit_test=False,
    add_signing_middleware: LocalAccount | None = None,
    skip_verification: bool = False,
    expected_chain_id: int | None = None,
    rpc_request_stats: RPCRequestStats | None = None,
) -> MultiProviderWeb3:
    """Create a Web3 instance with multi-provider support.

    Create a complex Web3 connection manager that

    - Supports fail-overs to different providers

    - Can have a special execution endpoint
      for MEV protection

    - HTTP providers are monkey-patched for faster uJSON reading

    - HTTP providers have middleware cleared and chain middleware installed

    The configuration line is a whitespace separated list of URLs (spaces, newlines, etc.)
    using mini configuration language.

    - If any of the protocols have `mev+` prefix like `mev+https` then this
      endpoint is used for the execution.

    Example:

    .. code-block:: python

        config = "mev+https://rpc.mevblocker.io https://polygon-rpc.com https://bsc-dataseed2.bnbchain.org"
        web3 = create_multi_provider_web3(config)
        assert get_provider_name(web3.get_fallback_provider()) == "polygon-rpc.com"
        assert len(web3.get_fallback_provider().providers) == 2
        assert get_provider_name(web3.get_active_transact_provider()) == "rpc.mevblocker.io"

    See

    - :ref:`multi rpc` for details

    :param configuration_line:
        Configuration line from an environment variable, config file or similar.

    :param fallback_sleep:
        Seconds between JSON-RPC call retries.

    :param fallback_backoff:
        Sleep increase multiplier.

    :param request_kwargs:
        Passed to HTTPProvider, arguments for :py:mod:`requests` library when doing HTTP requests.

        See :py:class:`web3.HTTPProvider` for details.


        Example: ``request_kwargs={"timeout": 10.0}``

    :param default_http_timeout:
        Use this timeout value for HTTP requests library if `request_kwargs` not given.

        Tuple (connect timeout, read timeout)

    :param session:
        Use specific HTTP 1.1 session with :py:mod:`requests`.

        If not given create a default session manager with retry logic.

    :param switchover_noisiness:
        Log level for messages when one RPC provider fails and we try other one.

    :param retries:
        How many retry count we do calling JSON-RPC API if the API response fails.

    :param hint:
        A hint for error logs if something goes wrong.

    :param unit_test:
        Run in unit test mode.

        Have special hooks and environment variable based timeouts for unit tests.

    :param add_signing_middleware:
        If set, install signing middleware for this account.

        This allows ``contract.functions.foo().transact({"from": account.address})``
        to automatically sign transactions with the given private key.

        Pass an :py:class:`eth_account.signers.local.LocalAccount` instance,
        e.g. from ``Account.from_key(private_key)``.

    :param skip_verification:
        Skip the startup ``eth_chainId`` cross-check of all providers
        (:py:meth:`FallbackProvider.verify_providers`).

        That cross-check probes every configured provider with ``eth_chainId``.
        When fanning out to multiprocessing worker pools (e.g.
        :py:class:`~eth_defi.event_reader.multicall_batcher.MultiprocessMulticallReader`),
        each worker rebuilds its own Web3 via :py:class:`MultiProviderWeb3Factory`,
        so this probe runs once per worker and multiplies ``eth_chainId`` load on
        the *primary* provider — which can itself trip rate limits (HTTP 429).
        The parent process has already verified the chain ID before fan-out, so
        pass ``True`` for subprocess factories together with ``expected_chain_id``.

    :param expected_chain_id:
        Pre-seed :py:attr:`FallbackProvider.expected_chain_id` without probing.

        **Required** when ``skip_verification`` is set (asserted). The parent
        process passes the chain ID it already verified so that runtime provider
        switchover in the worker still rejects an endpoint that mis-routes to the
        wrong chain. Without it, a verification-skipped worker would have no
        chain-id baseline and could silently accept a wrong-chain provider on
        failover, so the combination is rejected rather than silently downgraded.

    :return:
        Configured Web3 instance with multiple providers
    """

    assert configuration_line is not None, f"create_multi_provider_web3(): JSON-RPC URL configuration line is missing, hint {hint}"
    assert type(configuration_line) == str, f"create_multi_provider_web3(): JSON-RPC URL configuration line is not a string, got {type(configuration_line)}"

    items = configuration_line.split()

    urls: List[Url] = []
    for parsable in items:
        parsable = parsable.strip()

        try:
            url = parse_url(parsable)
        except Exception as e:
            raise MultiProviderConfigurationError(f"Could not parse JSON-RPC configuration URL: {parsable}. Hint is {hint}.")

        if not url.scheme:
            raise MultiProviderConfigurationError(f"Bad URL: {parsable}. Hint is {hint}.")

        if url in urls:
            raise MultiProviderConfigurationError(f"Entry appears twice: {url}. Hint is {hint}.")

        urls.append(url)

    if len(urls) == 0:
        raise MultiProviderConfigurationError(f"No configured endpoints: The config line is '{configuration_line}'. Hint is {hint}.")

    transact_endpoints = [url.url.replace("mev+", "") for url in urls if url.scheme.startswith("mev+")]
    call_endpoints = [url.url for url in urls if not url.scheme.startswith("mev+")]

    if len(transact_endpoints) > 1:
        raise MultiProviderConfigurationError(f"Only one execution endpoint can be specified, got {transact_endpoints}. Hint is {hint}.")

    if len(call_endpoints) == 0:
        raise MultiProviderConfigurationError(f"At least one call endpoint must be specified, configuration was {configuration_line}")

    if session is None:
        # https://stackoverflow.com/a/47475019/315168
        # TODO: Make these parameters configurable
        session = requests.Session()

        if retries >= 1:
            retry = Retry(connect=3, backoff_factor=0.5)
            adapter = HTTPAdapter(max_retries=retry)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

    if request_kwargs is None:
        request_kwargs = {"timeout": default_http_timeout}

    call_providers = []
    for url in call_endpoints:
        provider = create_http_provider(
            url,
            request_kwargs=request_kwargs,
            session=session,
            exception_retry_configuration=None,
        )
        logger.debug(
            "Created provider %s, timeout %s",
            get_url_domain(url),
            request_kwargs.get("timeout"),
        )
        _apply_anvil_launch_metadata(provider)

        call_providers.append(provider)

    # Do uJSON patching
    for p in call_providers:
        _fix_provider(p)

    fallback_provider = FallbackProvider(
        call_providers,
        sleep=fallback_sleep,
        backoff=fallback_backoff,
        switchover_noisiness=switchover_noisiness,
        retries=retries,
        rpc_request_stats=rpc_request_stats,
    )

    # Verify all call providers report the same chain ID before proceeding.
    # Skipped for subprocess factories: the parent has already verified, and
    # re-probing eth_chainId in every worker can trip provider rate limits.
    # When skipping, the caller must seed the parent-verified chain ID so runtime
    # switchover still rejects an endpoint that mis-routes to the wrong chain;
    # skipping without a baseline would silently disable that safety guard.
    if not skip_verification:
        fallback_provider.verify_providers()
    else:
        assert expected_chain_id is not None, f"skip_verification=True requires expected_chain_id to preserve runtime switchover chain-id safety. Hint is {hint}."
        fallback_provider.expected_chain_id = expected_chain_id

    transact_provider = None
    if len(transact_endpoints) > 0:
        transact_endpoint = transact_endpoints[0]
        transact_provider = HTTPProvider(transact_endpoint, request_kwargs=request_kwargs, session=session)
        _apply_anvil_launch_metadata(transact_provider)

        _fix_provider(transact_provider)

        # Verify transact provider is on the same chain as call providers
        if fallback_provider.expected_chain_id is not None:
            transact_domain = get_url_domain(str(transact_provider.endpoint_uri))
            if rpc_request_stats is not None:
                rpc_request_stats.record_call(transact_domain, "eth_chainId")
            try:
                resp = transact_provider.make_request("eth_chainId", [])
                if rpc_request_stats is not None and resp.get("error"):
                    error_code, error_message = normalise_rpc_error(resp["error"])
                    rpc_request_stats.record_error(transact_domain, error_code, error_message)
                result = resp.get("result")
                if result:
                    transact_chain_id = int(result, 16)
                    if transact_chain_id != fallback_provider.expected_chain_id:
                        transact_name = get_provider_name(transact_provider)
                        raise ChainIdMismatch(f"Transact provider {transact_name} returned chain ID {transact_chain_id}, but call providers are on chain {fallback_provider.expected_chain_id}. All providers must be on the same network.")
            except ChainIdMismatch:
                raise
            except Exception as e:
                if rpc_request_stats is not None:
                    error_code, error_message = normalise_rpc_error(e)
                    rpc_request_stats.record_error(transact_domain, error_code, error_message)
                transact_name = get_provider_name(transact_provider)
                raise ChainIdMismatch(f"Could not call eth_chainId on {transact_name} provider. Is it a valid JSON-RPC provider? As this is often the first call, you might be also out of API credits. Hint is {e}") from e

        provider = MEVBlockerProvider(
            call_provider=fallback_provider,
            transact_provider=transact_provider,
        )
    else:
        provider = fallback_provider

    logger.debug(
        "Configuring MultiProviderWeb3. Call providers: %s, transact providers %s",
        [get_provider_name(c) for c in call_providers],
        get_provider_name(transact_provider) if transact_provider else "-",
    )

    web3 = MultiProviderWeb3(provider)

    patch_web3(web3)

    from eth_defi.compat import add_middleware

    clear_middleware(web3)

    try:
        add_middleware(web3, static_call_cache_middleware, layer=0)
    except Exception as e:
        logger.warning("Skipping static_call_cache_middleware due to compatibility issue: %s", e)

    install_chain_middleware(web3, hint=hint)

    if add_signing_middleware is not None:
        from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil

        add_middleware(web3, construct_sign_and_send_raw_middleware_anvil(add_signing_middleware))

    if is_anvil(web3):
        # When running against local testing,
        # we need to disable block tip latency hacks
        set_block_tip_latency(web3, 0)

    return web3


def _fix_provider(provider: HTTPProvider):
    """Clear provider middlewares with v6/v7 compatibility."""
    clear_middleware(provider)
    patch_provider(provider)


class MultiProviderWeb3Factory:
    """Needed to pass RPC URL as :py:data:`Web3Factory`

    - Allows creating web3 connections from a config line in multiprocessing worker pools
    """

    def __init__(self, rpc_url: str, retries=6, hint: str | None = "", skip_verification: bool = False, expected_chain_id: int | None = None, rpc_request_stats: RPCRequestStats | None = None):
        self.rpc_url = rpc_url
        self.retries = retries
        self.hint = hint
        #: Skip the per-worker ``eth_chainId`` provider cross-check.
        #:
        #: Defaults to ``False`` so a stand-alone factory behaves like
        #: :py:func:`create_multi_provider_web3`. Set to ``True`` when this
        #: factory is handed to a multiprocessing worker pool: the parent
        #: process has already verified the chain ID, and re-verifying in every
        #: worker multiplies ``eth_chainId`` load on the primary provider and
        #: can itself trigger HTTP 429 rate limiting.
        self.skip_verification = skip_verification
        #: Parent-verified chain ID to seed into each worker when
        #: :py:attr:`skip_verification` is set, preserving the runtime
        #: switchover chain-id safety check.
        self.expected_chain_id = expected_chain_id
        #: Optional accumulator shared by parent-process worker threads.
        self.rpc_request_stats = rpc_request_stats

    def __call__(self, context: Optional[Any] = None, rpc_request_stats: RPCRequestStats | None = None) -> Web3:
        """CAlled by the subprocess.

        :param context:
            Legacy argument, not used.
        """
        return create_multi_provider_web3(
            self.rpc_url,
            retries=self.retries,
            hint=self.hint,
            skip_verification=self.skip_verification,
            expected_chain_id=self.expected_chain_id,
            rpc_request_stats=rpc_request_stats if rpc_request_stats is not None else self.rpc_request_stats,
        )
