"""Keeper impersonation and order execution helpers for GMX fork testing."""

import logging

from eth_abi import encode
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.abi import get_contract
from eth_defi.chain import get_chain_name
from eth_defi.gmx.testing.constants import ARBITRUM_DEFAULTS, resolve_contract_address, resolve_token_address
from eth_defi.gmx.testing.fork_provider import detect_provider_type, impersonate_account, set_balance, stop_impersonating_account

logger = logging.getLogger(__name__)


def execute_order_as_keeper(web3: Web3, order_key: bytes):
    """Execute order by impersonating keeper.

    Works with both Anvil and Tenderly.

    .. warning::

        On Anvil (and Tenderly) forks this function has a side-effect:
        **the test wallet's native ETH balance drops to exactly 0 after the
        keeper transaction is mined.**

        **Why it happens** -- verified via Tenderly transaction debugger:

        When the order is created (``create_market_buy_order`` /
        ``open_position``), the GMX order struct stores the wallet address
        as ``receiver`` with ``shouldUnwrapNativeToken = True``
        (see ``BaseOrder._build_order_params`` in
        ``eth_defi/gmx/order/base_order.py``).  During ``executeOrder``,
        the GMX protocol's internal settlement logic transfers the wallet's
        **entire** native ETH balance to a GMX settlement contract.  This
        happens because the wallet address is in ``unlocked_addresses``
        (i.e. ``anvil_impersonateAccount`` was called for it), which allows
        the fork node to process ETH transfers from the wallet without a
        signed transaction.

        **This is a fork-only artefact.**  In production the wallet is never
        impersonated, so GMX contracts cannot move ETH from it.  The wallet
        nonce and ERC-20 balances are unaffected.

        Callers that need to send further wallet transactions (e.g.
        ``cancel_order``, ``close_position``, creating SL/TP orders) **must
        re-fund the wallet** after calling this function::

            exec_receipt, keeper = execute_order_as_keeper(web3, order_key)

            # Restore wallet ETH so subsequent txs can pay for gas
            web3.provider.make_request(
                "anvil_setBalance",
                [wallet_address, hex(100_000_000 * 10**18)],
            )
            wallet.sync_nonce(web3)

    :return:
        Tuple of ``(receipt, keeper_address)``.
    """
    provider_type = detect_provider_type(web3)
    chain = get_chain_name(web3.eth.chain_id).lower()
    logger.info("Executing order as keeper (provider: %s)", provider_type)

    role_store_address = to_checksum_address(ARBITRUM_DEFAULTS["role_store"])
    RoleStore = get_contract(web3, "gmx/RoleStore.json")
    role_store = RoleStore(address=role_store_address)

    role_key = web3.keccak(encode(["string"], ["ORDER_KEEPER"]))

    keeper_count = role_store.functions.getRoleMemberCount(role_key).call()
    if keeper_count == 0:
        raise Exception("No keepers found in RoleStore")

    keepers = role_store.functions.getRoleMembers(role_key, 0, 1).call()
    keeper = keepers[0]
    logger.info("Keeper address: %s", keeper)

    set_balance(web3, keeper, hex(web3.to_wei(500, "ether")))

    order_handler_address = resolve_contract_address(chain, "orderhandler", ARBITRUM_DEFAULTS["order_handler"])
    OrderHandler = get_contract(web3, "gmx/OrderHandler.json")
    order_handler = OrderHandler(address=order_handler_address)

    weth_address = resolve_token_address(chain, "WETH", ARBITRUM_DEFAULTS["weth"])
    usdc_address = resolve_token_address(chain, "USDC", ARBITRUM_DEFAULTS["usdc"])
    oracle_provider = resolve_contract_address(
        chain,
        ("chainlinkdatastreamprovider", "gmoracleprovider"),
        ARBITRUM_DEFAULTS["chainlink_provider"],
    )

    oracle_params = (
        [weth_address, usdc_address],
        [oracle_provider, oracle_provider],
        [b"", b""],
    )

    impersonate_account(web3, keeper)

    try:
        tx_hash = order_handler.functions.executeOrder(order_key, oracle_params).transact(
            {
                "from": keeper,
                "gas": 5000_000_000,
            }
        )

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        logger.info("Order executed successfully")

        return receipt, keeper

    finally:
        stop_impersonating_account(web3, keeper)


def extract_order_key_from_receipt(receipt: dict, web3: Web3 | None = None) -> bytes:
    """Extract order key from OrderCreated event in receipt.

    GMX v2.2 uses EventLog2 with the order key in ``topics[2]``.

    - ``topics[0]``: EventLog2 signature
    - ``topics[1]``: OrderCreated event hash (``a7427759...``)
    - ``topics[2]``: order key
    - ``topics[3]``: account address

    :param receipt: Transaction receipt
    :param web3:
        Optional Web3 instance. If provided, uses the event module for
        full event decoding. If not provided, falls back to simple
        topic extraction.
    :return:
        The 32-byte order key
    """
    if web3 is not None:
        from eth_defi.gmx.events import extract_order_key_from_receipt as extract_key

        order_key = extract_key(web3, receipt)
        logger.info("Extracted order key: %s", order_key.hex())
        return order_key

    # Fallback to simple topic extraction (no web3 required)
    ORDER_CREATED_HASH = "a7427759bfd3b941f14e687e129519da3c9b0046c5b9aaa290bb1dede63753b3"

    for log in receipt.get("logs", []):
        topics = log.get("topics", [])

        if len(topics) < 3:
            continue

        topic_hashes = []
        for topic in topics:
            if isinstance(topic, bytes):
                topic_hashes.append(topic.hex())
            elif isinstance(topic, str):
                topic_hex = topic[2:] if topic.startswith("0x") else topic
                topic_hashes.append(topic_hex)
            else:
                topic_hashes.append(topic.hex())

        if len(topic_hashes) >= 2 and topic_hashes[1] == ORDER_CREATED_HASH:
            order_key_hex = topic_hashes[2]
            order_key = bytes.fromhex(order_key_hex)
            logger.info("Extracted order key: %s", order_key.hex())
            return order_key

    raise ValueError("Could not extract order key from receipt")


def execute_order_and_get_result(web3: Web3, order_key: bytes):
    """Execute order as keeper and return the parsed execution result.

    Convenience wrapper around :func:`execute_order_as_keeper` that also
    parses the execution events to extract prices, fees, and status.

    :param web3: Web3 instance connected to the fork
    :param order_key: The 32-byte order key from OrderCreated event
    :return:
        Tuple of ``(receipt, keeper_address, execution_result)``
        where ``execution_result`` is an ``OrderExecutionResult`` from
        ``eth_defi.gmx.events``
    """
    from eth_defi.gmx.events import extract_order_execution_result

    receipt, keeper = execute_order_as_keeper(web3, order_key)

    result = extract_order_execution_result(web3, receipt, order_key)

    if result:
        logger.info(
            "Order execution result: status=%s, price=%s, pnl=%s",
            result.status,
            result.execution_price,
            result.pnl_usd,
        )

    return receipt, keeper, result
