"""safe-eth-py RPC compatibility layer."""
from web3 import Web3

from safe_eth.eth import EthereumClient

def create_safe_ethereum_client(web3: Web3) -> EthereumClient:
    """Safe library wants to use its own funny client.

    - Translate Web3 endpoints to EthereumClient

    """
    # TODO: Handle MEVProvider
    provider = web3.provider
    url = provider.endpoint_uri
    return EthereumClient(url)