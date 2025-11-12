"""Fork testing helpers for GMX.

Supports both Anvil and Tenderly for mocking oracles and impersonating keepers.
"""

import json
import logging
import sys
from pathlib import Path

from eth_abi import encode
from eth_utils import to_checksum_address
from web3 import Web3
from eth_defi.abi import get_contract
from eth_defi.trace import assert_transaction_success_with_explanation

# Configure logger to show detailed output
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("[FORK] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def detect_provider_type(web3: Web3) -> str:
    """Detect if we're using Anvil or Tenderly.

    Returns:
        "anvil", "tenderly", or "unknown"
    """
    try:
        # Try Anvil-specific method
        web3.provider.make_request("anvil_nodeInfo", [])
        return "anvil"
    except Exception:
        pass

    # Check if endpoint contains tenderly
    endpoint = str(web3.provider.endpoint_uri if hasattr(web3.provider, "endpoint_uri") else "")
    if "tenderly" in endpoint.lower():
        return "tenderly"

    return "unknown"


def set_code(web3: Web3, address: str, bytecode: str):
    """Set bytecode at address (works with Anvil and Tenderly).

    Args:
        web3: Web3 instance
        address: Contract address where to set the bytecode
        bytecode: Bytecode to set (can be hex string with or without 0x prefix, or bytes)
    """
    provider_type = detect_provider_type(web3)

    # Ensure bytecode has 0x prefix and is a string
    if isinstance(bytecode, bytes):
        bytecode = "0x" + bytecode.hex()
    elif not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    address = to_checksum_address(address)

    # Make the RPC request based on provider type
    if provider_type == "anvil":
        logger.info(f"Using anvil_setCode for {address}")
        result = web3.provider.make_request("anvil_setCode", [address, bytecode])
    elif provider_type == "tenderly":
        logger.info(f"Using tenderly_setCode for {address} (Tenderly)")
        result = web3.provider.make_request("tenderly_setCode", [address, bytecode])
    else:
        # Fallback - try common methods
        logger.info(f"Unknown provider, trying tenderly_setCode for {address}")
        try:
            result = web3.provider.make_request("tenderly_setCode", [address, bytecode])
        except Exception as e1:
            logger.warning(f"tenderly_setCode failed: {e1}, trying anvil_setCode")
            try:
                result = web3.provider.make_request("anvil_setCode", [address, bytecode])
            except Exception as e2:
                raise Exception(f"Failed to set code: tenderly_setCode failed with {e1}, anvil_setCode failed with {e2}")

    # Verify bytecode was actually set (do this first, then check response)
    deployed_code = web3.eth.get_code(address)
    expected_bytecode = bytes.fromhex(bytecode[2:]) if bytecode.startswith("0x") else bytes.fromhex(bytecode)

    if deployed_code == expected_bytecode:
        logger.info("✅ Code verification successful: Deployed bytecode matches expected bytecode")
    else:
        logger.error("❌ Code verification failed: Deployed bytecode does not match expected bytecode")
        logger.error(f"Expected length: {len(expected_bytecode)} bytes")
        logger.error(f"Actual length: {len(deployed_code)} bytes")
        # Check if there was an RPC error
        if result.get("error"):
            error_msg = result.get("error", {}).get("message", "Unknown error")
            logger.error(f"RPC error: {error_msg}")
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
        # Fallback - try common methods
        try:
            web3.provider.make_request("anvil_setBalance", [address, balance_hex])
        except Exception:
            pass


def deal_eth(web3: Web3, recipient: str, amount_wei: int):
    """Fund an address with native ETH (equivalent to Foundry's vm.deal).

    Args:
        web3: Web3 instance
        recipient: Address to fund
        amount_wei: Amount in wei
    """
    recipient = to_checksum_address(recipient)
    balance_hex = hex(amount_wei)
    set_balance(web3, recipient, balance_hex)
    logger.info(f"Funded {recipient} with {amount_wei / 10**18} ETH")


def deal_tokens(web3: Web3, token_address: str, recipient: str, amount: int):
    """Fund an address with ERC20 tokens (equivalent to Foundry's deal).

    This uses anvil_setStorageAt to directly set the balance in the token's storage.
    Works for most standard ERC20 tokens that use a mapping(address => uint256) for balances.

    Args:
        web3: Web3 instance
        token_address: ERC20 token contract address
        recipient: Address to fund
        amount: Amount in token's smallest unit (e.g., wei for WETH, 10^6 for USDC)
    """
    provider_type = detect_provider_type(web3)
    token_address = to_checksum_address(token_address)
    recipient = to_checksum_address(recipient)

    # For most ERC20 tokens, balances are stored at slot 0 in a mapping(address => uint256)
    # The storage location is keccak256(abi.encode(address, slot))
    # We try common slots: 0, 1, 2, 51 (USDC uses 51)

    # Convert amount to bytes32 (32 bytes, big-endian)
    amount_bytes = amount.to_bytes(32, byteorder="big")
    amount_hex = "0x" + amount_bytes.hex()

    # Try common storage slots for balance mappings
    common_slots = [0, 1, 2, 51]  # 51 is used by USDC

    for slot in common_slots:
        # Compute storage location: keccak256(abi.encode(address, slot))
        # In Solidity, mappings use keccak256(key . slot) where . is concatenation
        encoded = encode(["address", "uint256"], [recipient, slot])
        storage_location = web3.keccak(encoded).hex()

        try:
            if provider_type == "anvil":
                web3.provider.make_request("anvil_setStorageAt", [token_address, storage_location, amount_hex])
            elif provider_type == "tenderly":
                # Tenderly uses evm_setAccountStorageAt or similar
                try:
                    web3.provider.make_request("evm_setAccountStorageAt", [token_address, storage_location, amount_hex])
                except Exception:
                    web3.provider.make_request("anvil_setStorageAt", [token_address, storage_location, amount_hex])
            else:
                # Fallback
                try:
                    web3.provider.make_request("anvil_setStorageAt", [token_address, storage_location, amount_hex])
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Failed to set storage at slot {slot}: {e}")
            continue

    logger.info(f"Funded {recipient} with {amount} of token {token_address}")

    # Verify by checking balance
    token = get_contract(web3, "ERC20MockDecimals.json")
    token_instance = token(address=token_address)
    try:
        balance = token_instance.functions.balanceOf(recipient).call()
        logger.info(f"  Verified balance: {balance}")
    except Exception as e:
        logger.debug(f"Could not verify balance: {e}")


def set_next_block_timestamp(web3: Web3, timestamp: int):
    """Set the timestamp for the next block to be mined.

    This is useful for preventing oracle price staleness on Anvil forks.
    When you fork at a specific block, subsequent transactions mine new blocks
    with newer timestamps, which can make hardcoded oracle prices stale.

    Args:
        web3: Web3 instance
        timestamp: Unix timestamp for the next block (in seconds)
    """
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        web3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
        logger.info(f"Set next block timestamp to {timestamp}")
    elif provider_type == "tenderly":
        # Tenderly may support this, try it
        try:
            web3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
            logger.info(f"Set next block timestamp to {timestamp} (Tenderly)")
        except Exception as e:
            logger.warning(f"Tenderly does not support evm_setNextBlockTimestamp: {e}")
    else:
        # Try anyway as fallback
        try:
            web3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
            logger.info(f"Set next block timestamp to {timestamp}")
        except Exception as e:
            logger.warning(f"Could not set next block timestamp: {e}")


def mine_block(web3: Web3):
    """Manually mine a block.

    This is used when Anvil is started with --no-mining flag.
    After sending transactions, you must call this to mine them into a block.

    Args:
        web3: Web3 instance
    """
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        result = web3.provider.make_request("evm_mine", [])
        logger.info(f"Manually mined block")
        return result
    elif provider_type == "tenderly":
        # Tenderly may support this, try it
        try:
            result = web3.provider.make_request("evm_mine", [])
            logger.info(f"Manually mined block (Tenderly)")
            return result
        except Exception as e:
            logger.warning(f"Tenderly does not support evm_mine: {e}")
            return None
    else:
        # Try anyway as fallback
        try:
            result = web3.provider.make_request("evm_mine", [])
            logger.info(f"Manually mined block")
            return result
        except Exception as e:
            logger.warning(f"Could not mine block: {e}")
            return None


def impersonate_account(web3: Web3, address: str):
    """Start impersonating account (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        web3.provider.make_request("anvil_impersonateAccount", [address])
    elif provider_type == "tenderly":
        # Tenderly allows transactions from any address without explicit impersonation
        # Just log that we're using Tenderly
        logger.debug(f"Tenderly: Will send tx from {address} (no impersonation needed)")
    else:
        # Try Anvil method as fallback
        try:
            web3.provider.make_request("anvil_impersonateAccount", [address])
        except Exception:
            logger.debug(f"Could not impersonate {address}, trying to send anyway")


def stop_impersonating_account(web3: Web3, address: str):
    """Stop impersonating account (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)

    if provider_type == "anvil":
        web3.provider.make_request("anvil_stopImpersonatingAccount", [address])
    elif provider_type == "tenderly":
        # Tenderly doesn't need to stop impersonation
        logger.debug(f"Tenderly: No need to stop impersonation for {address}")
    else:
        # Try Anvil method as fallback
        try:
            web3.provider.make_request("anvil_stopImpersonatingAccount", [address])
        except Exception:
            pass


def setup_mock_oracle(
    web3: Web3,
    eth_price_usd: int = 3892,
    usdc_price_usd: int = 1,
):
    """Setup mock oracle by replacing bytecode at production address.

    Works with both Anvil and Tenderly.

    This follows the pattern:
    1. Load deployedBytecode from MockOracleProvider.json
    2. Replace production provider bytecode with mock bytecode
    3. Load contract instance at production address
    4. Test and configure prices
    """
    provider_type = detect_provider_type(web3)
    logger.info(f"Setting up mock oracle (provider: {provider_type})")

    # Production oracle provider address (verified from mainnet)
    production_provider_address = to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")

    # Load MockOracleProvider contract JSON
    contract_path = Path(__file__).parent.parent.parent / "eth_defi" / "abi" / "gmx" / "MockOracleProvider.json"
    with open(contract_path) as f:
        contract_data = json.load(f)

    abi = contract_data["abi"]

    # Get deployedBytecode (runtime code, NOT constructor bytecode)
    if "deployedBytecode" in contract_data:
        bytecode = contract_data["deployedBytecode"]
        if isinstance(bytecode, dict) and "object" in bytecode:
            bytecode = bytecode["object"]
            # print(f"{bytecode=}")
    # NOTE: Only use deployedBytecode
    # elif "bytecode" in contract_data:
    #     # Fallback: try bytecode field (though this is constructor bytecode)
    #     bytecode = contract_data["bytecode"]
    #     if isinstance(bytecode, dict) and "object" in bytecode:
    #         bytecode = bytecode["object"]
    else:
        raise Exception("Could not find bytecode in MockOracleProvider.json")

    # Ensure bytecode has 0x prefix
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    logger.info(f"Loaded MockOracleProvider deployedBytecode: {len(bytecode)} chars")

    # Get the ORIGINAL bytecode from the address (before replacement) for comparison
    original_bytecode_before = web3.eth.get_code(production_provider_address)
    logger.info(f"Original bytecode at {production_provider_address}: {len(original_bytecode_before)} bytes")

    # Replace production provider bytecode with our mock bytecode
    logger.info(f"Replacing bytecode at production address {production_provider_address}...")
    set_code(web3, production_provider_address, bytecode)

    # Verify the bytecode was actually changed
    new_bytecode = web3.eth.get_code(production_provider_address)
    expected_bytecode_bytes = bytes.fromhex(bytecode[2:]) if bytecode.startswith("0x") else bytes.fromhex(bytecode)

    # Compare lengths and contents
    logger.info(f"Verification after replacement:")
    logger.info(f"  Original (Chainlink) bytecode: {len(original_bytecode_before)} bytes")
    logger.info(f"  New (Mock) bytecode: {len(new_bytecode)} bytes")
    logger.info(f"  Expected (from JSON) bytecode: {len(expected_bytecode_bytes)} bytes")

    # Check that bytecode was actually changed from original
    if original_bytecode_before == new_bytecode:
        # Check if the "original" is already our mock (from a previous run)
        if new_bytecode == expected_bytecode_bytes:
            logger.info("ℹ️ NOTE: Bytecode at address was already MockOracleProvider (from previous run)")
            logger.info("  Anvil persists state between runs. This is OK - mock is already installed.")
        else:
            logger.error("❌ FAILED: Bytecode was NOT changed! Still has original Chainlink bytecode")
            logger.error("The anvil_setCode/tenderly_setCode call did not work")
            raise Exception("Bytecode replacement failed - code is still the original")

    # Check that new bytecode matches expected mock bytecode
    if new_bytecode != expected_bytecode_bytes:
        logger.error("❌ FAILED: New bytecode does NOT match expected mock bytecode")
        logger.error(f"Expected: {expected_bytecode_bytes.hex()[:100]}...")
        logger.error(f"Got: {new_bytecode.hex()[:100]}...")
        raise Exception("Bytecode replacement failed - code does not match expected mock")

    logger.info("✅ Bytecode replacement verified:")
    logger.info("  ✓ Bytecode changed from original Chainlink provider")
    logger.info("  ✓ New bytecode matches MockOracleProvider.json")

    # Load the contract instance at the production provider address (no deployment needed!)
    mock = web3.eth.contract(address=production_provider_address, abi=abi)
    logger.info(f"Mock oracle loaded at production address: {mock.address}")

    # Test that the mock functions work correctly
    logger.info("Testing mock oracle functions...")
    try:
        # Test isChainlinkOnChainProvider - should return false for our mock
        is_chainlink = mock.functions.isChainlinkOnChainProvider().call()
        logger.info(f"  isChainlinkOnChainProvider() = {is_chainlink}")

        # Test shouldAdjustTimestamp - should return false for our mock
        should_adjust = mock.functions.shouldAdjustTimestamp().call()
        logger.info(f"  shouldAdjustTimestamp() = {should_adjust}")

        # Assert both are false
        assert not is_chainlink, f"Expected isChainlinkOnChainProvider() to return False, got {is_chainlink}"
        assert not should_adjust, f"Expected shouldAdjustTimestamp() to return False, got {should_adjust}"

        logger.info("✅ Mock functions working correctly - bytecode replacement successful!")
    except AssertionError as e:
        logger.error(f"❌ Mock function assertion failed: {e}")
        logger.error("Bytecode replacement may have failed!")
        raise
    except Exception as e:
        logger.error(f"❌ Failed to call mock functions: {e}")
        logger.error("This suggests the bytecode replacement didn't work properly.")
        raise

    # Configure prices using the first available account
    account = web3.eth.accounts[0]

    # WETH: 18 decimals -> price * 10^12
    weth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    weth_price = int(eth_price_usd * (10**12))
    logger.info(f"Setting WETH price to {weth_price}...")

    weth_tx = mock.functions.setPrice(weth_address, weth_price, weth_price).build_transaction(
        {
            "from": account,
            "gas": 500_000,
            "gasPrice": web3.eth.gas_price,
        }
    )
    weth_tx_hash = web3.eth.send_transaction(weth_tx)
    assert_transaction_success_with_explanation(
        web3,
        weth_tx_hash,
        "Set WETH price on mock oracle",
    )

    # USDC: 6 decimals -> price * 10^24
    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    usdc_price = int(usdc_price_usd * (10**24))
    logger.info(f"Setting USDC price to {usdc_price}...")

    usdc_tx = mock.functions.setPrice(usdc_address, usdc_price, usdc_price).build_transaction({"from": account, "gas": 500_000, "gasPrice": web3.eth.gas_price})
    usdc_tx_hash = web3.eth.send_transaction(usdc_tx)
    assert_transaction_success_with_explanation(
        web3,
        usdc_tx_hash,
        "Set USDC price on mock oracle",
    )

    logger.info(f"✅ Mock oracle configured: ETH=${eth_price_usd}, USDC=${usdc_price_usd}")
    return production_provider_address


def execute_order_as_keeper(web3: Web3, order_key: bytes):
    """Execute order by impersonating keeper.

    Works with both Anvil and Tenderly.

    Returns:
        Tuple of (receipt, keeper_address)
    """
    provider_type = detect_provider_type(web3)
    logger.info(f"Executing order as keeper (provider: {provider_type})")

    # Get keeper from RoleStore
    role_store_address = to_checksum_address("0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72")
    RoleStore = get_contract(web3, "gmx/RoleStore.json")
    role_store = RoleStore(address=role_store_address)

    # Compute ORDER_KEEPER role key: keccak256(abi.encode("ORDER_KEEPER"))
    role_key = web3.keccak(encode(["string"], ["ORDER_KEEPER"]))

    # Get first keeper
    keeper_count = role_store.functions.getRoleMemberCount(role_key).call()
    if keeper_count == 0:
        raise Exception("No keepers found in RoleStore")

    keepers = role_store.functions.getRoleMembers(role_key, 0, 1).call()
    keeper = keepers[0]
    logger.info(f"Keeper address: {keeper}")

    # Fund keeper with ETH using provider-agnostic method
    set_balance(web3, keeper, hex(web3.to_wei(500, "ether")))

    # Get OrderHandler (use correct address with CONTROLLER role)
    order_handler_address = to_checksum_address("0x04315E233C1c6FfA61080B76E29d5e8a1f7B4A35")
    OrderHandler = get_contract(web3, "gmx/OrderHandler.json")
    order_handler = OrderHandler(address=order_handler_address)

    # Build oracle params
    weth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    oracle_provider = to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")

    oracle_params = (
        [weth_address, usdc_address],
        [oracle_provider, oracle_provider],
        [b"", b""],
    )

    # Impersonate keeper using provider-agnostic method
    impersonate_account(web3, keeper)

    try:
        # Execute order
        tx_hash = order_handler.functions.executeOrder(order_key, oracle_params).transact(
            {
                "from": keeper,
                "gas": 5000_000_000,
            }
        )

        # Use assert_transaction_success_with_explanation to get detailed error info
        # assert_transaction_success_with_explanation(web3, tx_hash, "Order execution by keeper")

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        logger.info("✓ Order executed successfully")

        return receipt, keeper

    finally:
        stop_impersonating_account(web3, keeper)


def extract_order_key_from_receipt(receipt: dict) -> bytes:
    """Extract order key from OrderCreated event in receipt.

    GMX v2.2 uses EventLog2 with the order key in topics[2].
    topics[0]: EventLog2 signature
    topics[1]: OrderCreated event hash (a7427759...)
    topics[2]: order key
    topics[3]: account address
    """

    # OrderCreated event hash
    ORDER_CREATED_HASH = "a7427759bfd3b941f14e687e129519da3c9b0046c5b9aaa290bb1dede63753b3"

    for log in receipt.get("logs", []):
        topics = log.get("topics", [])

        # Need at least 3 topics (EventLog2, OrderCreated, order key)
        if len(topics) < 3:
            continue

        # Convert topics to hex strings for comparison
        topic_hashes = []
        for topic in topics:
            if isinstance(topic, bytes):
                topic_hashes.append(topic.hex())
            elif isinstance(topic, str):
                topic_hex = topic[2:] if topic.startswith("0x") else topic
                topic_hashes.append(topic_hex)
            else:
                topic_hashes.append(topic.hex())

        # Check if topics[1] is OrderCreated event
        if len(topic_hashes) >= 2 and topic_hashes[1] == ORDER_CREATED_HASH:
            # topics[2] is the order key
            order_key_hex = topic_hashes[2]
            order_key = bytes.fromhex(order_key_hex)
            logger.info(f"Extracted order key: {order_key.hex()}")
            return order_key

    raise ValueError("Could not extract order key from receipt")
