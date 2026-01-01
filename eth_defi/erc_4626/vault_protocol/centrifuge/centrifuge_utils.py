"""Centrifuge utility functions.

Helper functions for interacting with Centrifuge liquidity pool contracts.
"""

import logging

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.abi import get_deployed_contract

logger = logging.getLogger(__name__)


def fetch_pool_id(
    web3: Web3,
    vault_address: str,
    block_identifier: BlockIdentifier = "latest",
) -> int:
    """Fetch the Centrifuge pool ID for a given vault.

    Each Centrifuge vault belongs to a pool identified by a uint64 poolId.

    :param web3:
        Web3 instance

    :param vault_address:
        The address of the Centrifuge LiquidityPool vault contract

    :param block_identifier:
        Block number or 'latest'

    :return:
        The pool ID as an integer
    """
    contract = get_deployed_contract(
        web3,
        "centrifuge/LiquidityPool.json",
        vault_address,
    )
    return contract.functions.poolId().call(block_identifier=block_identifier)


def fetch_tranche_id(
    web3: Web3,
    vault_address: str,
    block_identifier: BlockIdentifier = "latest",
) -> bytes:
    """Fetch the Centrifuge tranche ID for a given vault.

    Each Centrifuge vault belongs to a specific tranche within a pool,
    identified by a bytes16 trancheId.

    :param web3:
        Web3 instance

    :param vault_address:
        The address of the Centrifuge LiquidityPool vault contract

    :param block_identifier:
        Block number or 'latest'

    :return:
        The tranche ID as bytes
    """
    contract = get_deployed_contract(
        web3,
        "centrifuge/LiquidityPool.json",
        vault_address,
    )
    return contract.functions.trancheId().call(block_identifier=block_identifier)
