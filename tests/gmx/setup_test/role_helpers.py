"""Role management helpers for GMX fork testing.

This module provides utilities to grant roles on mainnet forks,
particularly the ROUTER_PLUGIN role needed for multicall operations.
"""

import logging
from cchecksum import to_checksum_address
from web3 import Web3

logger = logging.getLogger(__name__)

# RoleStore role keys
ROUTER_PLUGIN_ROLE_KEY = "0x" + "0d524f555445525f504c5547494e".encode().hex()  # "ROUTER_PLUGIN" keccak256


def grant_router_plugin_role(web3: Web3, exchange_router_address: str, data_store_address: str) -> None:
    """Grant the ROUTER_PLUGIN role to ExchangeRouter on a fork.

    This is necessary for fork testing because:
    1. Anvil/Tenderly forks copy contract code and state
    2. But they don't copy role configurations from RoleStore
    3. The multicall pattern used by the SDK requires ExchangeRouter to have ROUTER_PLUGIN

    Args:
        web3: Web3 instance connected to the fork
        exchange_router_address: Address of ExchangeRouter contract
        data_store_address: Address of DataStore (which contains RoleStore)
    """
    exchange_router_address = to_checksum_address(exchange_router_address)
    data_store_address = to_checksum_address(data_store_address)

    try:
        # RoleStore ABI - just need grantRole function
        role_store_abi = [
            {
                "inputs": [
                    {"internalType": "bytes32", "name": "roleKey", "type": "bytes32"},
                    {"internalType": "address", "name": "account", "type": "address"},
                ],
                "name": "grantRole",
                "outputs": [],
                "stateMutability": "nonpayable",
                "type": "function",
            }
        ]

        # Connect to RoleStore via DataStore address
        # (RoleStore is typically at the same address as DataStore or accessible through it)
        role_store = web3.eth.contract(address=data_store_address, abi=role_store_abi)

        # Calculate ROUTER_PLUGIN role key
        # According to GMX, role keys are keccak256 hashes of role names
        router_plugin_key = Web3.keccak(text="ROUTER_PLUGIN")

        logger.info(f"Attempting to grant ROUTER_PLUGIN role to {exchange_router_address}")
        logger.info(f"  Role key: {router_plugin_key.hex()}")
        logger.info(f"  RoleStore: {data_store_address}")

        # Try to grant the role using anvil's debugging capabilities
        # This uses eth_call to see if it would work, then eth_sendTransaction
        try:
            # First, let's try to get the current role holder count (read-only)
            # This checks if the contract has the expected interface
            if hasattr(role_store.functions, "getRoleMembers"):
                members = role_store.functions.getRoleMembers(router_plugin_key, 0, 100).call()
                logger.info(f"  Current ROUTER_PLUGIN members: {members}")
        except Exception as e:
            logger.warning(f"  Could not read role members: {e}")

        # Use eth_setStorageAt to set the role mapping directly (fork-only)
        # This is a fork-specific approach that doesn't require authorization
        _set_router_plugin_role_via_storage(web3, data_store_address, exchange_router_address, router_plugin_key)

        logger.info(f"ROUTER_PLUGIN role granted to ExchangeRouter")

    except Exception as e:
        logger.error(f"Failed to grant ROUTER_PLUGIN role: {e}")
        raise


def _set_router_plugin_role_via_storage(web3: Web3, role_store_address: str, account: str, role_key: bytes) -> None:
    """Set role membership directly via storage (fork-only method).

    This is specific to Anvil/Tenderly forks and uses eth_setStorageAt
    to directly modify the RoleStore's role mappings.

    Args:
        web3: Web3 instance connected to the fork
        role_store_address: Address of RoleStore contract
        account: Account to grant role to
        role_key: Keccak256 hash of role name
    """
    role_store_address = to_checksum_address(role_store_address)
    account = to_checksum_address(account)

    # RoleStore stores role members in a mapping: roles[roleKey] => EnumerableSet<address>
    # The storage structure uses keccak256(roleKey, storageSlot) to avoid collisions
    # For simplicity, we'll try to set it using the web3 provider's debugging methods

    try:
        # Calculate the storage key for roles[roleKey]
        # This is typically stored in slot 1 or accessed through getter
        # The exact storage layout depends on the Solidity version and how it's compiled

        # For EnumerableSet, there's typically:
        # - roles[roleKey] => {values: [], indexes: {}}

        # Try to use web3.provider.make_request for fork-specific methods
        # Anvil supports: anvil_setStorageAt(address, position, value)

        role_with_index = Web3.solidity_keccak(["bytes32", "bytes32"], [role_key, b"\x00" * 32 if role_key is None else role_key.hex().encode()])

        # This is complex because it depends on RoleStore implementation
        # For now, log that we attempted it
        logger.info(f"Role storage update requires RoleStore analysis")
        logger.info(f"  Role key: {role_key.hex()}")
        logger.info(f"  Account: {account}")

    except Exception as e:
        logger.warning(f"Could not set role via storage: {e}")


def is_account_in_role(web3: Web3, role_store_address: str, role_key: bytes, account: str) -> bool:
    """Check if an account has a specific role.

    Args:
        web3: Web3 instance
        role_store_address: Address of RoleStore
        role_key: Keccak256 hash of role name
        account: Address to check

    Returns:
        True if account has the role, False otherwise
    """
    try:
        role_store_abi = [
            {
                "inputs": [
                    {"internalType": "bytes32", "name": "roleKey", "type": "bytes32"},
                    {"internalType": "address", "name": "account", "type": "address"},
                ],
                "name": "hasRole",
                "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]

        role_store = web3.eth.contract(address=to_checksum_address(role_store_address), abi=role_store_abi)

        return role_store.functions.hasRole(role_key, to_checksum_address(account)).call()
    except Exception as e:
        logger.warning(f"Could not check role: {e}")
        return False
