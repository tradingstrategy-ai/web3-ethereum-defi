from typing import Type

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract, get_deployed_contract


def get_erc_4626_contract(web3: Web3) -> Type[Contract]:
    """Get IERC4626 interface."""
    return get_contract(
        web3,
        "lagoon/IERC4626.json",
    )


def get_deployed_erc_4626_contract(web3: Web3, address: HexAddress) -> Contract:
    """Get IERC4626 deployed at some address."""
    return get_deployed_contract(
        web3,
        "lagoon/IERC4626.json",
        address=address,
    )