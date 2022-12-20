"""Chain specific configuration.

Many chains like Polygon and BNB Chain may need their own Web3 connection tuning.
In this module, we have helpers.
"""

#: These chains need POA middleware
from web3 import Web3
from web3.middleware import geth_poa_middleware

from eth_defi.middleware import http_retry_request_with_sleep_middleware

POA_MIDDLEWARE_NEEDED_CHAIN_IDS = {
    56,  # BNB Chain
    127,  # Polygon
}


def install_chain_middleware(web3: Web3):
    """Install any chain-specific middleware to Web3 instannce.

    Mainly this is POA middleware for BNB Chain, Polygon, Avalanche C-chain.
    """

    if web3.eth.chain_id in POA_MIDDLEWARE_NEEDED_CHAIN_IDS:
        web3.middleware_onion.inject(geth_poa_middleware, layer=0)


def install_retry_middleware(web3: Web3):
    """Install gracefully HTTP request retry middleware.

    In the case your Internet connection or JSON-RPC node has issues,
    gracefully do exponential backoff retries.
    """
    web3.middleware_onion.inject(http_retry_request_with_sleep_middleware, layer=0)
