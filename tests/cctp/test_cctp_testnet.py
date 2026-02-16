"""CCTP V2 testnet integration tests.

Bridges USDC from Base Sepolia to Arbitrum Sepolia using real CCTP V2
contracts and Circle's sandbox attestation service.

Environment variables:

- ``ARBITRUM_CCTP_TEST_PRIVATE_KEY``: Private key of the funded testnet account
- ``JSON_RPC_ARBITRUM_SEPOLIA``: RPC endpoint for Arbitrum Sepolia (private RPC recommended)
- ``JSON_RPC_BASE_SEPOLIA``: RPC endpoint for Base Sepolia (private RPC recommended)

The test account must be pre-funded with:

- **ETH on Base Sepolia** (source chain gas) — use
  https://www.alchemy.com/faucets/base-sepolia
- **ETH on Arbitrum Sepolia** (destination chain gas for ``receiveMessage()``) — use
  https://learnweb3.io/faucets/arbitrum_sepolia/
- **Testnet USDC on Base Sepolia** (source chain, amount to bridge) — use
  https://faucet.circle.com/

.. note::

    Circle's sandbox attestation service (``iris-api-sandbox.circle.com``)
    can take 10+ minutes to produce attestations on Sepolia testnets.
    The burn-only test verifies on-chain CCTP integration without waiting
    for attestation. The full e2e test requires ``CCTP_FULL_E2E=true`` and
    a 30-minute timeout.
"""

import logging
import os
import time

import pytest
from eth_account import Account
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.cctp.attestation import fetch_attestation
from eth_defi.cctp.constants import (
    CCTP_DOMAIN_BASE,
    FINALITY_THRESHOLD_FAST,
    IRIS_API_SANDBOX_URL,
    TESTNET_CHAIN_IDS,
)
from eth_defi.cctp.receive import prepare_receive_message
from eth_defi.cctp.transfer import (
    prepare_approve_for_burn,
    prepare_deposit_for_burn,
    resolve_token_messenger_address,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

ARBITRUM_CCTP_TEST_PRIVATE_KEY = os.environ.get("ARBITRUM_CCTP_TEST_PRIVATE_KEY")
JSON_RPC_ARBITRUM_SEPOLIA = os.environ.get("JSON_RPC_ARBITRUM_SEPOLIA")
JSON_RPC_BASE_SEPOLIA = os.environ.get("JSON_RPC_BASE_SEPOLIA")

#: Set to "true" to run the full e2e test including attestation + receiveMessage.
#: This can take 10-30 minutes due to Circle's sandbox attestation service.
CCTP_FULL_E2E = os.environ.get("CCTP_FULL_E2E", "").lower() == "true"

#: Amount to bridge: 1 USDC (6 decimals)
BRIDGE_AMOUNT = 1 * 10**6

#: Maximum seconds to wait for state to propagate on the RPC node
#: after a transaction is confirmed. Private RPCs should not need this,
#: but public RPCs can have significant lag.
RPC_STATE_PROPAGATION_TIMEOUT = 30

pytestmark = pytest.mark.skipif(
    not all([ARBITRUM_CCTP_TEST_PRIVATE_KEY, JSON_RPC_ARBITRUM_SEPOLIA, JSON_RPC_BASE_SEPOLIA]),
    reason="ARBITRUM_CCTP_TEST_PRIVATE_KEY, JSON_RPC_ARBITRUM_SEPOLIA, and JSON_RPC_BASE_SEPOLIA must all be set",
)


def _wait_for_state(fn, expected, description: str, timeout: float = RPC_STATE_PROPAGATION_TIMEOUT):
    """Wait for an RPC read call to return the expected value.

    Accounts for state propagation lag on RPC nodes after a transaction is confirmed.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value == expected:
            return value
        time.sleep(2)
    value = fn()
    assert value == expected, f"{description}: expected {expected}, got {value}"
    return value


@pytest.fixture()
def account():
    """Load the test account from the private key."""
    return Account.from_key(ARBITRUM_CCTP_TEST_PRIVATE_KEY)


@pytest.fixture()
def web3_arbitrum_sepolia(account) -> Web3:
    """Web3 connected to Arbitrum Sepolia with signing middleware."""
    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM_SEPOLIA, add_signing_middleware=account)
    assert web3.eth.chain_id == 421614, f"Expected Arbitrum Sepolia (421614), got chain {web3.eth.chain_id}"  # noqa: PLR2004
    return web3


@pytest.fixture()
def web3_base_sepolia(account) -> Web3:
    """Web3 connected to Base Sepolia with signing middleware."""
    web3 = create_multi_provider_web3(JSON_RPC_BASE_SEPOLIA, add_signing_middleware=account)
    assert web3.eth.chain_id == 84532, f"Expected Base Sepolia (84532), got chain {web3.eth.chain_id}"  # noqa: PLR2004
    return web3


def test_cctp_burn_base_sepolia(
    web3_base_sepolia: Web3,
    web3_arbitrum_sepolia: Web3,
    account,
):
    """Test CCTP V2 depositForBurn on Base Sepolia.

    Verifies the on-chain CCTP integration by:
    1. Approving USDC on Base Sepolia
    2. Calling depositForBurn targeting Arbitrum Sepolia
    3. Verifying USDC was burned (balance decreased)

    Does NOT wait for Circle attestation (can take 10+ min on testnet).
    """
    sender = account.address

    assert web3_base_sepolia.eth.chain_id in TESTNET_CHAIN_IDS
    assert web3_arbitrum_sepolia.eth.chain_id in TESTNET_CHAIN_IDS

    # Check USDC balance on Base Sepolia (source chain)
    base_usdc_address = USDC_NATIVE_TOKEN[84532]
    base_usdc = get_deployed_contract(web3_base_sepolia, "ERC20MockDecimals.json", base_usdc_address)
    base_balance = base_usdc.functions.balanceOf(sender).call()
    logger.info("Base Sepolia USDC balance: %s (raw: %d)", base_balance / 10**6, base_balance)
    assert base_balance >= BRIDGE_AMOUNT, f"Insufficient USDC on Base Sepolia: {base_balance / 10**6} USDC. Get testnet USDC from https://faucet.circle.com/"

    # Check ETH for gas
    assert web3_base_sepolia.eth.get_balance(sender) > 0, "No ETH on Base Sepolia for gas"

    # Step 1: Approve USDC to TokenMessengerV2
    approve_fn = prepare_approve_for_burn(web3_base_sepolia, amount=BRIDGE_AMOUNT)
    tx_hash = approve_fn.transact({"from": sender})
    assert_transaction_success_with_explanation(web3_base_sepolia, tx_hash)
    logger.info("Approve tx: 0x%s", tx_hash.hex())

    # Wait for allowance to propagate (accounts for RPC state lag)
    messenger_address = Web3.to_checksum_address(resolve_token_messenger_address(84532))
    _wait_for_state(
        lambda: base_usdc.functions.allowance(sender, messenger_address).call() >= BRIDGE_AMOUNT,
        True,
        "USDC allowance after approve",
    )

    # Step 2: depositForBurn targeting Arbitrum Sepolia
    burn_fn = prepare_deposit_for_burn(
        web3_base_sepolia,
        amount=BRIDGE_AMOUNT,
        destination_chain_id=421614,
        mint_recipient=sender,
        min_finality_threshold=FINALITY_THRESHOLD_FAST,
    )
    tx_hash = burn_fn.transact({"from": sender})
    assert_transaction_success_with_explanation(web3_base_sepolia, tx_hash)
    logger.info("depositForBurn tx: 0x%s", tx_hash.hex())

    # Step 3: Verify USDC was burned (wait for state propagation)
    expected_balance = base_balance - BRIDGE_AMOUNT
    _wait_for_state(
        lambda: base_usdc.functions.balanceOf(sender).call(),
        expected_balance,
        "USDC balance after burn",
    )


@pytest.mark.skipif(not CCTP_FULL_E2E, reason="Set CCTP_FULL_E2E=true to run full e2e (takes 10-30 min)")
def test_cctp_bridge_base_to_arbitrum_e2e(
    web3_base_sepolia: Web3,
    web3_arbitrum_sepolia: Web3,
    account,
):
    """Full end-to-end CCTP V2 bridge: Base Sepolia -> Arbitrum Sepolia.

    1. Approve + depositForBurn on Base Sepolia
    2. Wait for Circle sandbox attestation (10-30 min)
    3. Call receiveMessage on Arbitrum Sepolia
    4. Verify USDC arrived

    .. warning::

        Circle's sandbox attestation service can take 10+ minutes.
        Run with a 30-minute pytest timeout.
    """
    sender = account.address

    assert web3_base_sepolia.eth.chain_id in TESTNET_CHAIN_IDS
    assert web3_arbitrum_sepolia.eth.chain_id in TESTNET_CHAIN_IDS

    # Check balances
    base_usdc = get_deployed_contract(web3_base_sepolia, "ERC20MockDecimals.json", USDC_NATIVE_TOKEN[84532])
    arb_usdc = get_deployed_contract(web3_arbitrum_sepolia, "ERC20MockDecimals.json", USDC_NATIVE_TOKEN[421614])

    base_balance = base_usdc.functions.balanceOf(sender).call()
    assert base_balance >= BRIDGE_AMOUNT, f"Insufficient USDC on Base Sepolia: {base_balance / 10**6}"
    assert web3_base_sepolia.eth.get_balance(sender) > 0, "No ETH on Base Sepolia for gas"
    assert web3_arbitrum_sepolia.eth.get_balance(sender) > 0, "No ETH on Arbitrum Sepolia for gas"

    arb_balance_before = arb_usdc.functions.balanceOf(sender).call()

    # Approve + burn
    approve_fn = prepare_approve_for_burn(web3_base_sepolia, amount=BRIDGE_AMOUNT)
    tx_hash = approve_fn.transact({"from": sender})
    assert_transaction_success_with_explanation(web3_base_sepolia, tx_hash)

    messenger_address = Web3.to_checksum_address(resolve_token_messenger_address(84532))
    _wait_for_state(
        lambda: base_usdc.functions.allowance(sender, messenger_address).call() >= BRIDGE_AMOUNT,
        True,
        "USDC allowance after approve",
    )

    burn_fn = prepare_deposit_for_burn(
        web3_base_sepolia,
        amount=BRIDGE_AMOUNT,
        destination_chain_id=421614,
        mint_recipient=sender,
        min_finality_threshold=FINALITY_THRESHOLD_FAST,
    )
    tx_hash = burn_fn.transact({"from": sender})
    assert_transaction_success_with_explanation(web3_base_sepolia, tx_hash)
    logger.info("depositForBurn tx: 0x%s", tx_hash.hex())

    # Wait for attestation (can take 10-30 min on testnet)
    logger.info("Polling sandbox Iris API for attestation (this may take 10-30 minutes)...")
    attestation = fetch_attestation(
        source_domain=CCTP_DOMAIN_BASE,
        transaction_hash=tx_hash.hex(),
        timeout=1800.0,  # 30 minutes
        poll_interval=10.0,
        api_base_url=IRIS_API_SANDBOX_URL,
    )
    assert attestation.status == "complete"
    assert len(attestation.message) > 0
    assert len(attestation.attestation) > 0

    # Relay on Arbitrum Sepolia
    receive_fn = prepare_receive_message(
        web3_arbitrum_sepolia,
        message=attestation.message,
        attestation=attestation.attestation,
    )
    tx_hash = receive_fn.transact({"from": sender})
    assert_transaction_success_with_explanation(web3_arbitrum_sepolia, tx_hash)
    logger.info("receiveMessage tx: 0x%s", tx_hash.hex())

    # Verify USDC arrived
    arb_balance_after = arb_usdc.functions.balanceOf(sender).call()
    assert arb_balance_after == arb_balance_before + BRIDGE_AMOUNT
