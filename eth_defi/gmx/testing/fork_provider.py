"""Fork provider detection and RPC helpers for GMX testing.

Supports both Anvil and Tenderly for impersonation, balance manipulation,
and other fork-specific operations.
"""

import logging

from eth_abi import encode
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.abi import get_contract

logger = logging.getLogger(__name__)


def detect_provider_type(web3: Web3) -> str:
    """Detect if we're using Anvil or Tenderly.

    :return:
        ``"anvil"``, ``"tenderly"``, or ``"unknown"``
    """
    endpoint = str(web3.provider.endpoint_uri if hasattr(web3.provider, "endpoint_uri") else "")
    if "tenderly" in endpoint.lower():
        return "tenderly"

    try:
        result = web3.provider.make_request("anvil_nodeInfo", [])
        if result and not result.get("error"):
            return "anvil"
    except Exception:
        pass

    return "unknown"


def set_code(web3: Web3, address: str, bytecode: str):
    """Set bytecode at address (works with Anvil and Tenderly).

    :param web3: Web3 instance
    :param address: Contract address where to set the bytecode
    :param bytecode: Bytecode to set (hex string with or without 0x prefix, or bytes)
    """
    provider_type = detect_provider_type(web3)

    if isinstance(bytecode, bytes):
        bytecode = "0x" + bytecode.hex()
    elif not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    address = to_checksum_address(address)

    if provider_type == "anvil":
        logger.info("Using anvil_setCode for %s", address)
        result = web3.provider.make_request("anvil_setCode", [address, bytecode])
    elif provider_type == "tenderly":
        logger.info("Using tenderly_setCode for %s (Tenderly)", address)
        result = web3.provider.make_request("tenderly_setCode", [address, bytecode])
    else:
        logger.info("Unknown provider, trying tenderly_setCode for %s", address)
        try:
            result = web3.provider.make_request("tenderly_setCode", [address, bytecode])
        except Exception as e1:
            logger.warning("tenderly_setCode failed: %s, trying anvil_setCode", e1)
            try:
                result = web3.provider.make_request("anvil_setCode", [address, bytecode])
            except Exception as e2:
                raise Exception(f"Failed to set code: tenderly_setCode failed with {e1}, anvil_setCode failed with {e2}")

    deployed_code = web3.eth.get_code(address)
    expected_bytecode = bytes.fromhex(bytecode[2:]) if bytecode.startswith("0x") else bytes.fromhex(bytecode)

    if deployed_code == expected_bytecode:
        logger.info("Code verification successful: Deployed bytecode matches expected bytecode")
    else:
        logger.error("Code verification failed: Deployed bytecode does not match expected bytecode")
        logger.error("Expected length: %d bytes", len(expected_bytecode))
        logger.error("Actual length: %d bytes", len(deployed_code))
        if result.get("error"):
            error_msg = result.get("error", {}).get("message", "Unknown error")
            logger.error("RPC error: %s", error_msg)
        raise Exception("Bytecode verification failed - code was not set correctly")


def set_balance(web3: Web3, address: str, balance_hex: str):
    """Set balance for address (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)
    address = to_checksum_address(address)

    if provider_type == "anvil":
        web3.provider.make_request("anvil_setBalance", [address, balance_hex])
    elif provider_type == "tenderly":
        try:
            web3.provider.make_request("tenderly_setBalance", [address, balance_hex])
        except Exception:
            web3.provider.make_request("anvil_setBalance", [address, balance_hex])
    else:
        try:
            web3.provider.make_request("anvil_setBalance", [address, balance_hex])
        except Exception:
            pass


def deal_eth(web3: Web3, recipient: str, amount_wei: int):
    """Fund an address with native ETH (equivalent to Foundry's ``vm.deal``).

    :param web3: Web3 instance
    :param recipient: Address to fund
    :param amount_wei: Amount in wei
    """
    recipient = to_checksum_address(recipient)
    balance_hex = hex(amount_wei)
    set_balance(web3, recipient, balance_hex)
    logger.info("Funded %s with %s ETH", recipient, amount_wei / 10**18)


def deal_tokens(web3: Web3, token_address: str, recipient: str, amount: int):
    """Fund an address with ERC20 tokens (equivalent to Foundry's ``deal``).

    Uses ``anvil_setStorageAt`` to directly set the balance in the token's storage.
    Works for most standard ERC20 tokens that use a ``mapping(address => uint256)``
    for balances.

    :param web3: Web3 instance
    :param token_address: ERC20 token contract address
    :param recipient: Address to fund
    :param amount: Amount in token's smallest unit (e.g. wei for WETH, 10^6 for USDC)
    """
    provider_type = detect_provider_type(web3)
    token_address = to_checksum_address(token_address)
    recipient = to_checksum_address(recipient)

    amount_bytes = amount.to_bytes(32, byteorder="big")
    amount_hex = "0x" + amount_bytes.hex()

    # Try common storage slots for balance mappings
    common_slots = [0, 1, 2, 51]  # 51 is used by USDC

    for slot in common_slots:
        encoded = encode(["address", "uint256"], [recipient, slot])
        storage_location = web3.keccak(encoded).hex()

        try:
            if provider_type == "anvil":
                web3.provider.make_request("anvil_setStorageAt", [token_address, storage_location, amount_hex])
            elif provider_type == "tenderly":
                try:
                    web3.provider.make_request("evm_setAccountStorageAt", [token_address, storage_location, amount_hex])
                except Exception:
                    web3.provider.make_request("anvil_setStorageAt", [token_address, storage_location, amount_hex])
            else:
                try:
                    web3.provider.make_request("anvil_setStorageAt", [token_address, storage_location, amount_hex])
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Failed to set storage at slot %d: %s", slot, e)
            continue

    logger.info("Funded %s with %d of token %s", recipient, amount, token_address)

    token = get_contract(web3, "ERC20MockDecimals.json")
    token_instance = token(address=token_address)
    try:
        balance = token_instance.functions.balanceOf(recipient).call()
        logger.info("  Verified balance: %d", balance)
    except Exception as e:
        logger.debug("Could not verify balance: %s", e)


def set_next_block_timestamp(web3: Web3, timestamp: int):
    """Set the timestamp for the next block to be mined.

    Useful for preventing oracle price staleness on Anvil forks.

    :param web3: Web3 instance
    :param timestamp: Unix timestamp for the next block (in seconds)
    """
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        web3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
        logger.info("Set next block timestamp to %d", timestamp)
    elif provider_type == "tenderly":
        try:
            web3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
            logger.info("Set next block timestamp to %d (Tenderly)", timestamp)
        except Exception as e:
            logger.warning("Tenderly does not support evm_setNextBlockTimestamp: %s", e)
    else:
        try:
            web3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
            logger.info("Set next block timestamp to %d", timestamp)
        except Exception as e:
            logger.warning("Could not set next block timestamp: %s", e)


def mine_block(web3: Web3):
    """Manually mine a block.

    Used when Anvil is started with ``--no-mining`` flag.
    After sending transactions, call this to mine them into a block.

    :param web3: Web3 instance
    """
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        result = web3.provider.make_request("evm_mine", [])
        logger.info("Manually mined block")
        return result
    elif provider_type == "tenderly":
        try:
            result = web3.provider.make_request("evm_mine", [])
            logger.info("Manually mined block (Tenderly)")
            return result
        except Exception as e:
            logger.warning("Tenderly does not support evm_mine: %s", e)
            return None
    else:
        try:
            result = web3.provider.make_request("evm_mine", [])
            logger.info("Manually mined block")
            return result
        except Exception as e:
            logger.warning("Could not mine block: %s", e)
            return None


def impersonate_account(web3: Web3, address: str):
    """Start impersonating account (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        web3.provider.make_request("anvil_impersonateAccount", [address])
    elif provider_type == "tenderly":
        logger.debug("Tenderly: Will send tx from %s (no impersonation needed)", address)
    else:
        try:
            web3.provider.make_request("anvil_impersonateAccount", [address])
        except Exception:
            logger.debug("Could not impersonate %s, trying to send anyway", address)


def stop_impersonating_account(web3: Web3, address: str):
    """Stop impersonating account (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        web3.provider.make_request("anvil_stopImpersonatingAccount", [address])
    elif provider_type == "tenderly":
        logger.debug("Tenderly: No need to stop impersonation for %s", address)
    else:
        try:
            web3.provider.make_request("anvil_stopImpersonatingAccount", [address])
        except Exception:
            pass
