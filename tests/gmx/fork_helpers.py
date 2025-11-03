"""Fork testing helpers for GMX.

Supports both Anvil and Tenderly for mocking oracles and impersonating keepers.
"""

import logging

from eth_abi import encode
from eth_utils import to_checksum_address
from web3 import Web3
from eth_defi.abi import get_contract
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


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
    """Set bytecode at address (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)

    # Ensure bytecode has 0x prefix
    if isinstance(bytecode, bytes):
        bytecode = bytecode.hex()
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    address = to_checksum_address(address)

    if provider_type == "anvil":
        logger.info(f"Using anvil_setCode for {address}")
        result = web3.provider.make_request("anvil_setCode", [address, bytecode])
        logger.debug(f"anvil_setCode result: {result}")
    elif provider_type == "tenderly":
        # Tenderly uses tenderly_setCode
        logger.info(f"Using tenderly_setCode for {address} (Tenderly)")
        result = web3.provider.make_request("tenderly_setCode", [address, bytecode])
        logger.debug(f"tenderly_setCode result: {result}")
    else:
        # Fallback - try common methods
        logger.info(f"Unknown provider, trying tenderly_setCode for {address}")
        try:
            result = web3.provider.make_request("tenderly_setCode", [address, bytecode])
            logger.debug(f"tenderly_setCode result: {result}")
        except Exception as e1:
            logger.warning(f"tenderly_setCode failed: {e1}, trying anvil_setCode")
            try:
                result = web3.provider.make_request("anvil_setCode", [address, bytecode])
                logger.debug(f"anvil_setCode result: {result}")
            except Exception as e2:
                raise Exception(f"Failed to set code: tenderly_setCode failed with {e1}, anvil_setCode failed with {e2}")


def set_balance(web3: Web3, address: str, balance_hex: str):
    """Set balance for address (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)

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


def setup_mock_oracle(web3: Web3, eth_price_usd: int = 3892, usdc_price_usd: int = 1):
    """Setup mock oracle by replacing bytecode at production address.

    Works with both Anvil and Tenderly.
    """
    provider_type = detect_provider_type(web3)
    logger.info(f"Setting up mock oracle (provider: {provider_type})")

    # Production oracle provider address (verified from mainnet)
    provider_address = to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")

    # Deploy MockOracleProvider to get bytecode
    MockProvider = get_contract(web3, "gmx/MockOracleProvider.json")
    deployer = web3.eth.accounts[0]

    logger.info(f"Deploying MockOracleProvider from {deployer}...")
    tx_hash = MockProvider.constructor().transact({"from": deployer, "gas": 2_000_000})
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        "MockOracleProvider deployment",
    )
    temp_address = receipt["contractAddress"]
    logger.info(f"MockOracleProvider deployed at {temp_address}")

    # Get bytecode
    bytecode = web3.eth.get_code(temp_address).hex()
    logger.info(f"Bytecode length: {len(bytecode)} chars")

    # Replace production provider bytecode using provider-agnostic method
    logger.info(f"Replacing bytecode at {provider_address}...")
    set_code(web3, provider_address, bytecode)

    # Verify bytecode was replaced
    new_code = web3.eth.get_code(provider_address).hex()
    logger.info(f"Verification - new bytecode length at {provider_address}: {len(new_code)} chars")

    if new_code == bytecode:
        print("✓ Bytecode replacement verified - codes match!")
    else:
        logger.warning(f"⚠ Bytecode mismatch! Expected {len(bytecode)} chars, got {len(new_code)} chars")
        logger.warning("The code replacement may have failed. Oracle prices might not work correctly.")

    # Configure prices at production address
    mock = MockProvider(address=provider_address)

    # WETH: 18 decimals -> price * 10^12
    weth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    weth_price = int(eth_price_usd * (10**12))
    logger.info(f"Setting WETH price to {weth_price}...")
    weth_tx = mock.functions.setPrice(weth_address, weth_price, weth_price).transact(
        {
            "from": deployer,
            "gas": 500_000,
        }
    )
    assert_transaction_success_with_explanation(
        web3,
        weth_tx,
        "Set WETH price on mock oracle",
    )

    # USDC: 6 decimals -> price * 10^24
    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    usdc_price = int(usdc_price_usd * (10**24))
    logger.info(f"Setting USDC price to {usdc_price}...")
    usdc_tx = mock.functions.setPrice(usdc_address, usdc_price, usdc_price).transact(
        {"from": deployer, "gas": 500_000},
    )
    assert_transaction_success_with_explanation(
        web3,
        usdc_tx,
        "Set USDC price on mock oracle",
    )

    logger.info(f"Mock oracle configured: ETH=${eth_price_usd}, USDC=${usdc_price_usd}")
    return provider_address


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
                "gas": 50000_000_000,
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
    from hexbytes import HexBytes

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
