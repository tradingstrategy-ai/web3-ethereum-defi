"""Keeper order execution utilities for GMX on Anvil forks.

This module provides functions to simulate keeper order execution on forked networks,
allowing orders created by the Python SDK to be executed without real keeper infrastructure.

The keeper is responsible for:
1. Fetching oracle prices
2. Executing the order with OrderHandler.executeOrder()
3. Validating the execution was successful

This mimics the off-chain keeper behavior but runs entirely on-chain via Anvil.

Example:
    from tests.gmx.setup_test.keeper_executor import execute_order_as_keeper
    from tests.gmx.setup_test.event_parser import extract_order_key_from_receipt

    # After creating order with GMXTrading
    order_receipt = web3.eth.wait_for_transaction_receipt(order_tx_hash)
    order_key = extract_order_key_from_receipt(order_receipt)

    # Execute order as keeper
    exec_receipt = execute_order_as_keeper(
        web3=web3,
        order_key=order_key,
        chain="arbitrum",
        eth_price_usd=3892,
        usdc_price_usd=1,
    )

    # Get position key from execution
    from tests.gmx.setup_test.event_parser import extract_position_key_from_receipt
    position_key = extract_position_key_from_receipt(exec_receipt)
"""

import logging
from typing import Optional, Dict
from eth_account import Account
from eth_utils import to_checksum_address
from web3 import Web3
from hexbytes import HexBytes

from tests.gmx.setup_test.fork_helpers import (
    get_active_keeper,
    set_eth_balance,
    impersonate_account,
    stop_impersonating_account,
    get_order_handler_contract,
)
from eth_defi.gmx.contracts import (
    get_contract_addresses,
    get_datastore_contract,
    NETWORK_TOKENS,
)

logger = logging.getLogger(__name__)


def execute_order_as_keeper(
    web3: Web3,
    order_key: bytes,
    chain: str = "arbitrum",
    eth_price_usd: Optional[int] = None,
    usdc_price_usd: Optional[int] = None,
    token_prices: Optional[Dict[str, int]] = None,
) -> dict:
    """Execute a pending GMX order as the keeper would.

    This function:
    1. Gets the active keeper from RoleStore
    2. Sets up oracle prices (mocked)
    3. Calls OrderHandler.executeOrder() as the keeper
    4. Returns the transaction receipt

    The oracle prices should be in GMX format: price * 10^30 / 10^tokenDecimals
    For simplicity, you can pass raw USD prices and they'll be converted.

    Args:
        web3: Web3 instance connected to Anvil fork
        order_key: Order key to execute (bytes)
        chain: Blockchain identifier (default: "arbitrum")
        eth_price_usd: ETH price in USD (will be converted to GMX format)
        usdc_price_usd: USDC price in USD (will be converted to GMX format)
        token_prices: Alternative dict of token_address -> price_usd for custom tokens

    Returns:
        Transaction receipt from the execution

    Raises:
        Exception: If order execution fails
    """
    # Ensure order_key is bytes
    if isinstance(order_key, str):
        if order_key.startswith("0x"):
            order_key = bytes.fromhex(order_key[2:])
        else:
            order_key = bytes.fromhex(order_key)

    # Get contracts and addresses
    addresses = get_contract_addresses(chain)
    datastore = get_datastore_contract(web3, chain)
    order_handler = get_order_handler_contract(web3, chain)

    # Get keeper address
    keeper = get_active_keeper(web3, chain)
    logger.info(f"Using keeper: {keeper}")

    # Set keeper balance for gas
    try:
        current_balance = web3.eth.get_balance(keeper)
        if current_balance < web3.to_wei(1, "ether"):
            set_eth_balance(web3, keeper, web3.to_wei(10, "ether"))
            logger.info(f"Funded keeper with 10 ETH")
    except Exception as e:
        logger.warning(f"Could not check/set keeper balance: {e}")

    # Prepare token prices
    if token_prices is None:
        token_prices = _prepare_default_prices(chain, eth_price_usd, usdc_price_usd)

    logger.info(f"Using prices: {token_prices}")

    # Build oracle params
    tokens = list(token_prices.keys())
    prices = list(token_prices.values())

    oracle_params = {
        "tokens": tokens,
        "providers": [to_checksum_address(addresses.chainlinkdatastreamprovider)] * len(tokens),
        "data": [b""] * len(tokens),  # No data needed for mocked oracle
    }

    logger.info(f"Oracle params: tokens={tokens}")

    # Impersonate keeper and execute order
    try:
        impersonate_account(web3, keeper)

        # Build transaction
        tx = order_handler.functions.executeOrder(
            order_key,
            oracle_params,
        ).build_transaction(
            {
                "from": keeper,
                "gas": 5_000_000,  # High gas for safety
            }
        )

        # Send as impersonated account
        tx_hash = web3.eth.send_transaction(tx)
        logger.info(f"Submitted order execution tx: {tx_hash.hex()}")

        # Wait for receipt
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt["status"] == 1:
            logger.info(f"Order executed successfully")
        else:
            logger.error(f"Order execution failed")
            raise Exception(f"Order execution reverted in tx {tx_hash.hex()}")

        return receipt

    finally:
        # Stop impersonating
        stop_impersonating_account(web3, keeper)


def _prepare_default_prices(
    chain: str,
    eth_price_usd: Optional[int] = None,
    usdc_price_usd: Optional[int] = None,
) -> Dict[str, int]:
    """Prepare default token prices in GMX format.

    Converts USD prices to GMX oracle format: price * 10^30 / 10^tokenDecimals

    Args:
        chain: Blockchain identifier
        eth_price_usd: ETH price in USD
        usdc_price_usd: USDC price in USD

    Returns:
        Dict mapping token addresses to prices in GMX format
    """
    prices = {}

    if chain == "arbitrum":
        network_tokens = NETWORK_TOKENS.get("arbitrum", {})

        # ETH
        if eth_price_usd is not None:
            weth_addr = to_checksum_address(network_tokens.get("WETH"))
            # WETH: 18 decimals, so: price * 10^30 / 10^18 = price * 10^12
            gmx_price = int(eth_price_usd * (10**12))
            prices[weth_addr] = gmx_price
            logger.info(f"WETH price: ${eth_price_usd} -> {gmx_price} (GMX format)")

        # USDC
        if usdc_price_usd is not None:
            usdc_addr = to_checksum_address(network_tokens.get("USDC"))
            # USDC: 6 decimals, so: price * 10^30 / 10^6 = price * 10^24
            gmx_price = int(usdc_price_usd * (10**24))
            prices[usdc_addr] = gmx_price
            logger.info(f"USDC price: ${usdc_price_usd} -> {gmx_price} (GMX format)")

    elif chain == "avalanche":
        network_tokens = NETWORK_TOKENS.get("avalanche", {})

        if eth_price_usd is not None:
            weth_addr = to_checksum_address(network_tokens.get("WETH"))
            gmx_price = int(eth_price_usd * (10**12))
            prices[weth_addr] = gmx_price

        if usdc_price_usd is not None:
            usdc_addr = to_checksum_address(network_tokens.get("USDC"))
            gmx_price = int(usdc_price_usd * (10**24))
            prices[usdc_addr] = gmx_price

    return prices


def execute_multiple_orders(
    web3: Web3,
    order_keys: list,
    chain: str = "arbitrum",
    eth_price_usd: Optional[int] = None,
    usdc_price_usd: Optional[int] = None,
) -> list:
    """Execute multiple orders sequentially as keeper.

    Args:
        web3: Web3 instance
        order_keys: List of order keys to execute
        chain: Blockchain identifier
        eth_price_usd: ETH price in USD
        usdc_price_usd: USDC price in USD

    Returns:
        List of transaction receipts
    """
    receipts = []

    for i, order_key in enumerate(order_keys):
        logger.info(f"Executing order {i + 1}/{len(order_keys)}: {order_key.hex()}")

        try:
            receipt = execute_order_as_keeper(
                web3,
                order_key,
                chain=chain,
                eth_price_usd=eth_price_usd,
                usdc_price_usd=usdc_price_usd,
            )
            receipts.append(receipt)
            logger.info(f"Order {i + 1} executed")

        except Exception as e:
            logger.error(f"Order {i + 1} execution failed: {e}")
            raise

    return receipts
