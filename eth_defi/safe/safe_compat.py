"""safe-eth-py RPC compatibility layer."""

from web3 import Web3

from safe_eth.eth import EthereumClient

from eth_defi.provider.mev_blocker import MEVBlockerProvider


def create_safe_ethereum_client(web3: Web3) -> EthereumClient:
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
    return EthereumClient(url)
