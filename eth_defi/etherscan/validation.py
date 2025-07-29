"""Etherscan configuration validation."""

import logging

import requests

from web3 import Web3

from eth_defi.etherscan.config import get_etherscan_url


logger = logging.getLogger(__name__)


class EtherscanConfigurationError(Exception):
    """Custom exception for Etherscan configuration errors."""


def check_etherscan_api_key(
    web3: Web3,
    api_key: str,
):
    """Check if Etherscan API key should work.

    - Check using Etherscan v2 multichain support

    :raise EtherscanConfigurationError: if the API key is not vali or chain mismatch.
    """

    if not api_key:
        raise EtherscanConfigurationError("Etherscan API key is empty")

    chain_id = web3.eth.chain_id

    # Now multichain
    etherscan_url = "https://api.etherscan.io/v2/"

    if etherscan_url is None:
        raise EtherscanConfigurationError(f"No Etherscan URL configured for chain ID {chain_id}")

    logger.info("Checking Etherscan API key for chain ID %s at %s...", chain_id, etherscan_url[0:25])

    # Perform a check
    # https://docs.etherscan.io/etherscan-v2/api-endpoints/stats-1
    url = f"https://api.etherscan.io/v2/api?chainid={chain_id}&module=getapilimit&action=getapilimit&apikey={api_key}"
    resp = requests.get(url)

    if resp.status_code != 200:
        raise EtherscanConfigurationError(f"Failed to validate Etherscan API key for chain ID {chain_id}: {resp.status_code} - {resp.text}")

    status = resp.json().get("status")
    assert status == "1", f"Invalid Etherscan API key for chain ID {chain_id}: {resp.json()}"
