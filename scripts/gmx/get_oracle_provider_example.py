"""
Example: How to get oracle provider address from DataStore

This script demonstrates how to call dataStore.getAddress() to retrieve
the oracle provider configured for a specific token.
"""

from web3 import Web3

from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract
from eth_defi.gmx.keys import oracle_provider_for_token_key


def get_oracle_provider_for_token(web3: Web3, chain: str, oracle_address: str, token_address: str) -> str:
    """
    Get the oracle provider address for a specific token from DataStore.

    This is equivalent to:
    ```solidity
    address expectedProvider = dataStore.getAddress(Keys.oracleProviderForTokenKey(oracle, token));
    ```

    :param web3: Web3 instance
    :param chain: Chain name (e.g., "arbitrum")
    :param oracle_address: Oracle contract address (e.g., the main Oracle address)
    :param token_address: Token address to check
    :return: Oracle provider address for this token
    """
    # Get DataStore contract
    datastore = get_datastore_contract(web3, chain)

    # Generate the key using our Python keys module
    # This matches: Keys.oracleProviderForTokenKey(oracle, token)
    key = oracle_provider_for_token_key(oracle_address, token_address)

    # Call getAddress on DataStore
    # This is the equivalent of: dataStore.getAddress(key)
    provider_address = datastore.functions.getAddress(key).call()

    return provider_address


def example_usage():
    """Example usage with Arbitrum mainnet."""
    import os

    # Get RPC URL from environment
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM", "").split()[0]
    if not rpc_url:
        print("ERROR: Set JSON_RPC_ARBITRUM environment variable")
        return

    # Connect to Arbitrum
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    chain = "arbitrum"

    # Get GMX contract addresses (includes Oracle address)
    addresses = get_contract_addresses(chain)
    oracle_address = addresses.oracle

    if not oracle_address:
        print(f"ERROR: Oracle address not found for chain {chain}")
        return

    print(f"GMX Oracle Address: {oracle_address}")
    print()

    # Example: Check WETH oracle provider
    weth_address = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"  # WETH on Arbitrum

    print(f"Querying oracle provider for token: {weth_address}")
    print(f"Using oracle contract: {oracle_address}")
    print()

    provider = get_oracle_provider_for_token(web3, chain, oracle_address, weth_address)

    print(f"Oracle Provider Address: {provider}")
    print()

    # You can check multiple tokens
    usdc_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"  # USDC on Arbitrum
    print(f"Querying oracle provider for USDC: {usdc_address}")
    provider_usdc = get_oracle_provider_for_token(web3, chain, oracle_address, usdc_address)
    print(f"USDC Oracle Provider Address: {provider_usdc}")


if __name__ == "__main__":
    example_usage()
