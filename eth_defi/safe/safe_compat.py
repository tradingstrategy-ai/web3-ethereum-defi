"""safe-eth-py RPC compatibility layer."""

import logging
import time

from web3 import Web3

from safe_eth.eth import EthereumClient

from eth_defi.provider.mev_blocker import MEVBlockerProvider


logger = logging.getLogger(__name__)


def create_safe_ethereum_client(web3: Web3, retry_count=10, creation_problem_sleep=10) -> EthereumClient:
    """Safe library wants to use its own funny client.

    - Translate Web3 endpoints to EthereumClient

    """
    provider = web3.provider

    if isinstance(provider, MEVBlockerProvider):
        # EthereumClient() does not understand about Base sequencer,
        # MEVBlocker, etc.
        url = provider.call_endpoint_uri
    else:
        url = provider.endpoint_uri

    for attempt in range(retry_count, 0, -1):
        try:
            # this bastard fails inside the constructor sometimes,
            # so we just try to brute force our way around the problem here
            client = EthereumClient(url, retry_count=retry_count)
            break
        except Exception as e:
            if attempt == 0:
                raise
            # web3.exceptions.Web3RPCError: {'code': -32090, 'message': 'Too many requests, reason: call rate limit exhausted, retry in 10m0s', 'data': {'trace_id': 'c589c8fec713e6153a6df5d44ff5ab42'}}
            logger.warning(f"Failed to connect to Safe RPC {url}, attempts left {attempt - 1}, error: {e}")
            time.sleep(creation_problem_sleep)

    # Force Safe library to use our better middlewares
    # TODO: Breaks something - see test_lagoon_swap_exec_module
    # client.w3 = web3
    # client.slow_w3 = web3
    return client
