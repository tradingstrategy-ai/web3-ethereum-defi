"""Tenderly RPC specific methods and helpers.

- `Tenderly <https://tenderly.co>`__ is software-as-a-service debugger for EVM chains
"""

from web3 import Web3


def is_tenderly(web3: Web3) -> bool:
    """Check if the given web3 instance is connected to Tenderly RPC.

    :param web3: Web3 instance to check
    :return: True if the web3 instance is connected to Tenderly RPC, False otherwise
    """
    return "tenderly" in web3.provider.endpoint_uri.lower()
