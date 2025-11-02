"""Fork testing helpers for GMX on Anvil.

This module provides utilities to run mainnet fork tests using Anvil,
mirroring the patterns from the Foundry test suite but in pure Python.

Key features:
- Set balances on forked networks
- Set ERC20 token balances via storage manipulation
- Impersonate accounts for keeper execution
- Query RoleStore for active keepers

Example:
    from tests.gmx.setup_test.fork_helpers import (
        set_eth_balance,
        set_erc20_balance,
        get_active_keeper,
        impersonate_account,
    )

    web3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))

    # Set balances
    set_eth_balance(web3, "0x...", 10 * 10**18)
    set_erc20_balance(web3, usdc_address, wallet_address, 1000 * 10**6)

    # Get and impersonate keeper
    keeper = get_active_keeper(web3, "arbitrum")
    impersonate_account(web3, keeper)
"""

import logging
from cchecksum import to_checksum_address
from eth_abi import encode
from eth_utils import keccak
from web3 import Web3
from web3.contract import Contract

from eth_defi.provider.named import get_provider_name
from eth_defi.provider.anvil import is_anvil
from eth_defi.provider.tenderly import is_tenderly
from eth_defi.gmx.contracts import get_contract_addresses
from eth_defi.abi import get_contract

logger = logging.getLogger(__name__)


def set_eth_balance(web3: Web3, address: str, amount_wei: int) -> None:
    """Set ETH balance on an Anvil fork.

    Uses the anvil_setBalance RPC method (or tenderly_setBalance for Tenderly).

    Args:
        web3: Web3 instance connected to fork
        address: Address to fund
        amount_wei: Amount in wei to set

    Raises:
        NotImplementedError: If RPC provider is not Anvil or Tenderly
    """
    address = to_checksum_address(address)

    if is_tenderly(web3):
        response = web3.provider.make_request("tenderly_setBalance", [address, hex(amount_wei)])
    elif is_anvil(web3):
        response = web3.provider.make_request("anvil_setBalance", [address, hex(amount_wei)])
    else:
        raise NotImplementedError(f"Unsupported RPC backend: {get_provider_name(web3.provider)}")

    if "error" in response:
        raise Exception(f"Error setting balance: {response['error']}")

    logger.info(f"Set {address} balance to {amount_wei / 10**18:.4f} ETH")


def set_erc20_balance(web3: Web3, token_address: str, holder_address: str, amount: int) -> None:
    """Set ERC20 token balance on an Anvil fork using storage manipulation.

    This works by finding the storage slot for the balance mapping and setting it directly.
    For standard ERC20 contracts with balance at slot 0, this uses Solidity's mapping
    storage layout: balances[holder] = amount

    Args:
        web3: Web3 instance connected to fork
        token_address: Token contract address
        holder_address: Address to give tokens to
        amount: Amount to set (in token's smallest unit, e.g., wei for 18-decimal tokens)

    Raises:
        NotImplementedError: If RPC provider is not Anvil or Tenderly
    """
    token_address = to_checksum_address(token_address)
    holder_address = to_checksum_address(holder_address)

    # Standard ERC20 storage layout: balances mapping at slot 0
    # Calculate storage key: keccak256(abi.encodePacked(address, uint256(0)))

    storage_key = keccak(encode(["address", "uint256"], [holder_address, 0]))
    slot = f"0x{storage_key.hex()}"

    # Format the amount as 32-byte hex
    hex_amount = hex(amount)[2:].zfill(64)
    padded_amount = f"0x{hex_amount}"

    if is_tenderly(web3):
        response = web3.provider.make_request("tenderly_setStorageAt", [token_address, slot, padded_amount])
    else:
        raise NotImplementedError(f"Unsupported RPC backend: {get_provider_name(web3.provider)}")

    if "error" in response:
        raise Exception(f"Error setting token balance: {response['error']}")

    logger.info(f"Set {holder_address} balance of {token_address} to {amount}")


def get_active_keeper(web3: Web3, chain: str) -> str:
    """Get the first active ORDER_KEEPER from RoleStore.

    This mimics the Foundry helper function. Active keepers are those registered
    in the GMX RoleStore with the ORDER_KEEPER role.

    Args:
        web3: Web3 instance
        chain: Blockchain identifier (e.g., "arbitrum", "avalanche")

    Returns:
        Address of the active keeper

    Raises:
        Exception: If no keepers are found
    """
    addresses = get_contract_addresses(chain)
    role_store_address = to_checksum_address(addresses.rolestore)

    # Get RoleStore ABI
    role_store_abi = get_contract("RoleStore").abi

    # Create RoleStore contract instance
    role_store = web3.eth.contract(address=role_store_address, abi=role_store_abi)

    order_keeper_role = "ORDER_KEEPER"

    # Get role key
    role_key = web3.keccak(text=order_keeper_role)

    # Query RoleStore for keepers
    try:
        keeper_count = role_store.functions.getRoleMemberCount(role_key).call()

        if keeper_count == 0:
            raise Exception("No ORDER_KEEPERs found in RoleStore")

        # Get first keeper
        keepers = role_store.functions.getRoleMembers(role_key, 0, 1).call()
        keeper = keepers[0]

        logger.info(f"Found active keeper: {keeper}")
        return keeper

    except Exception as e:
        logger.error(f"Error getting active keeper: {e}")
        raise


def get_order_handler_contract(web3: Web3, chain: str) -> Contract:
    """Get OrderHandler contract instance.

    Args:
        web3: Web3 instance
        chain: Blockchain identifier

    Returns:
        OrderHandler contract instance
    """
    addresses = get_contract_addresses(chain)
    order_handler_address = to_checksum_address(addresses.orderhandler)

    # Load OrderHandler ABI
    order_handler_abi = get_contract("OrderHandler").abi

    return web3.eth.contract(address=order_handler_address, abi=order_handler_abi)
