"""Fork testing helpers for GMX on Anvil.

This module provides utilities to run mainnet fork tests using Anvil,
mirroring the patterns from the Foundry test suite but in pure Python.

Key features:
- Set balances on forked networks
- Get active keepers from RoleStore
- Impersonate accounts for keeper execution
- Execute orders as keepers
- Mine blocks

Example:
    from tests.gmx.setup_test.fork_helpers import (
        set_eth_balance,
        set_erc20_balance,
        get_active_keeper,
    )

    web3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))

    # Set balances
    set_eth_balance(web3, "0x...", 10 * 10**18)
    set_erc20_balance(web3, usdc_address, wallet_address, 1000 * 10**6)

    # Get keeper for execution
    keeper = get_active_keeper(web3, "arbitrum")
"""

import logging
from typing import Optional
from cchecksum import to_checksum_address
from web3 import Web3
from web3.contract import Contract

from eth_defi.provider.named import get_provider_name
from eth_defi.provider.anvil import is_anvil
from eth_defi.provider.tenderly import is_tenderly
from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract

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
    from eth_utils import keccak
    from eth_abi import encode

    storage_key = keccak(encode(["address", "uint256"], [holder_address, 0]))
    slot = f"0x{storage_key.hex()}"

    # Format the amount as 32-byte hex
    hex_amount = hex(amount)[2:].zfill(64)
    padded_amount = f"0x{hex_amount}"

    if is_tenderly(web3):
        response = web3.provider.make_request("tenderly_setStorageAt", [token_address, slot, padded_amount])
    elif is_anvil(web3):
        response = web3.provider.make_request("anvil_setStorageAt", [token_address, slot, padded_amount])
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
    from eth_defi.gmx.contracts import get_role_store_contract

    role_store = get_role_store_contract(web3, chain)
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


def impersonate_account(web3: Web3, account: str) -> None:
    """Impersonate an account on Anvil fork.

    This allows transactions to be sent from the account without knowing its private key.
    Uses hardhat_impersonateAccount RPC method.

    Args:
        web3: Web3 instance connected to Anvil
        account: Address to impersonate

    Raises:
        NotImplementedError: If not connected to Anvil
    """
    account = to_checksum_address(account)

    if not is_anvil(web3):
        raise NotImplementedError("Account impersonation only works with Anvil")

    response = web3.provider.make_request("hardhat_impersonateAccount", [account])

    if "error" in response:
        raise Exception(f"Error impersonating account: {response['error']}")

    logger.info(f"Impersonating account: {account}")


def stop_impersonating_account(web3: Web3, account: str) -> None:
    """Stop impersonating an account on Anvil fork.

    Args:
        web3: Web3 instance connected to Anvil
        account: Address to stop impersonating
    """
    account = to_checksum_address(account)

    if not is_anvil(web3):
        return

    web3.provider.make_request("hardhat_stopImpersonatingAccount", [account])
    logger.info(f"Stopped impersonating: {account}")


def mine_block(web3: Web3) -> int:
    """Mine a new block on Anvil fork.

    Returns:
        New block number
    """
    if not is_anvil(web3):
        raise NotImplementedError("Mining only works with Anvil")

    response = web3.provider.make_request("anvil_mine", ["1"])

    if "error" in response:
        raise Exception(f"Error mining block: {response['error']}")

    new_block = web3.eth.block_number
    logger.info(f"Mined block {new_block}")
    return new_block


def get_role_store_contract(web3: Web3, chain: str) -> Contract:
    """Get RoleStore contract instance.

    Args:
        web3: Web3 instance
        chain: Blockchain identifier

    Returns:
        RoleStore contract instance
    """
    from eth_defi.abi import get_contract

    addresses = get_contract_addresses(chain)
    role_store_address = addresses.rolestore

    # Load RoleStore ABI
    role_store_abi = get_contract("RoleStore").abi

    return web3.eth.contract(address=role_store_address, abi=role_store_abi)


def get_order_handler_contract(web3: Web3, chain: str) -> Contract:
    """Get OrderHandler contract instance.

    Args:
        web3: Web3 instance
        chain: Blockchain identifier

    Returns:
        OrderHandler contract instance
    """
    from eth_defi.abi import get_contract

    addresses = get_contract_addresses(chain)
    order_handler_address = addresses.orderhandler

    # Load OrderHandler ABI
    order_handler_abi = get_contract("OrderHandler").abi

    return web3.eth.contract(address=order_handler_address, abi=order_handler_abi)


def set_storage_at(web3: Web3, address: str, slot: str, value: int) -> None:
    """Override a storage slot value on Anvil fork.

    Args:
        web3: Web3 instance
        address: Contract address
        slot: Storage slot (as hex string with 0x prefix)
        value: Value to set (as integer)

    Raises:
        NotImplementedError: If RPC provider is not Anvil or Tenderly
    """
    address = to_checksum_address(address)

    # Format the value to a 32-byte hex string
    hex_value = hex(value)[2:].zfill(64)
    padded_value = f"0x{hex_value}"

    # Ensure slot has 0x prefix
    if not slot.startswith("0x"):
        slot = f"0x{slot}"

    if is_tenderly(web3):
        response = web3.provider.make_request("tenderly_setStorageAt", [address, slot, padded_value])
    elif is_anvil(web3):
        response = web3.provider.make_request("anvil_setStorageAt", [address, slot, padded_value])
    else:
        raise NotImplementedError(f"Unsupported RPC backend: {get_provider_name(web3.provider)}")

    if "error" in response:
        raise Exception(f"Error setting storage: {response['error']}")

    logger.info(f"Set storage at {address}[{slot}] to {padded_value}")


def set_bytecode(web3: Web3, address: str, bytecode: str) -> None:
    """Set contract bytecode at an address on Anvil fork (like vm.etch in Foundry).

    Args:
        web3: Web3 instance
        address: Contract address
        bytecode: Bytecode to set (hex string with or without 0x prefix)

    Raises:
        NotImplementedError: If RPC provider is not Anvil
    """
    address = to_checksum_address(address)

    # Ensure bytecode has 0x prefix
    if not bytecode.startswith("0x"):
        bytecode = f"0x{bytecode}"

    if not is_anvil(web3):
        raise NotImplementedError(f"set_bytecode only works with Anvil, not {get_provider_name(web3.provider)}")

    # Anvil's setCode expects parameters as separate args, not bundled
    response = web3.provider.make_request("anvil_setCode", [address, bytecode])

    if "error" in response:
        # Try alternative method using eth_setCode if available
        logger.error(f"anvil_setCode failed: {response['error']}, trying alternative")
        raise Exception(f"Error setting bytecode: {response['error']}")

    logger.info(f"Set bytecode at {address} ({(len(bytecode) - 2) // 2} bytes)")


def grant_router_plugin_role(web3: Web3, exchange_router_address: str, chain: str) -> None:
    """Grant ROUTER_PLUGIN role to ExchangeRouter on fork.

    GMX's SyntheticsRouter checks if the caller has ROUTER_PLUGIN role.
    On forks, this role needs to be granted to the ExchangeRouter for multicall operations.

    Args:
        web3: Web3 instance connected to fork
        exchange_router_address: ExchangeRouter contract address
        chain: Blockchain identifier (e.g., "arbitrum")

    Raises:
        Exception: If role grant fails
    """
    from eth_defi.gmx.contracts import get_role_store_contract

    exchange_router_address = to_checksum_address(exchange_router_address)

    # Get RoleStore contract
    role_store = get_role_store_contract(web3, chain)

    # Compute ROUTER_PLUGIN role key (keccak256("ROUTER_PLUGIN"))
    router_plugin_role = web3.keccak(text="ROUTER_PLUGIN")

    # Grant role to ExchangeRouter
    # RoleStore.grantRole(roleKey, account)
    grant_tx = role_store.functions.grantRole(
        router_plugin_role, exchange_router_address
    ).build_transaction({"from": exchange_router_address})

    # For impersonation, we use hardhat_impersonateAccount
    if is_anvil(web3):
        impersonate_account(web3, exchange_router_address)
        tx_hash = web3.eth.send_transaction(grant_tx)
        web3.eth.wait_for_transaction_receipt(tx_hash)
        stop_impersonating_account(web3, exchange_router_address)
        logger.info(f"Granted ROUTER_PLUGIN role to {exchange_router_address}")
    else:
        raise NotImplementedError(f"grant_router_plugin_role only works with Anvil, not {get_provider_name(web3.provider)}")
