"""Integration tests for LagoonGMXTradingWallet with real GMX contracts on Arbitrum fork.

Tests run against actual GMX V2 contracts using an Anvil fork of Arbitrum mainnet.
The LagoonGMXTradingWallet wraps all transactions through TradingStrategyModuleV0.performCall().
"""

import logging
import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from flaky import flaky

from eth_defi.gmx.order import OrderResult
from eth_defi.gmx.order.pending_orders import fetch_pending_orders
from eth_defi.gmx.testing import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.testing.gmx_lagoon import (
    GMX_EXCHANGE_ROUTER,
    GMX_SYNTHETICS_ROUTER,
    USDC_ARBITRUM,
    USDC_WHALE,
    WETH_ARBITRUM,
    WETH_WHALE,
    LagoonGMXForkEnv,
    create_lagoon_gmx_fork_env,
    isolated_lagoon_gmx_fork_env,
)
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

# Skip entire module if JSON_RPC_ARBITRUM not set
pytestmark = [
    pytest.mark.skipif(not os.environ.get("JSON_RPC_ARBITRUM"), reason="JSON_RPC_ARBITRUM environment variable not set"),
    pytest.mark.xdist_group("fork:arbitrum:gmx-lagoon"),
]


@pytest.fixture(scope="module")
def _gmx_deployment_baselines() -> dict[tuple[int, float], LagoonGMXForkEnv]:
    """Cache deployments by the actual fork process generation.

    Keep the mutable deployment cache local to this module and xdist worker.

    :return:
        Empty cache populated by the trading fixture.
    """
    return {}


@pytest.fixture()
def lagoon_gmx_deployment_env(anvil_chain_fork: AnvilLaunch) -> LagoonGMXForkEnv:
    """Keep deployment assertions independent of the shared trading baseline.

    :param anvil_chain_fork:
        Fresh fixed-block process for the deployment regression.
    :return:
        Newly deployed and funded environment.
    """
    return create_lagoon_gmx_fork_env(anvil_chain_fork)


@pytest.fixture()
def lagoon_gmx_fork_env(
    chain_name: str,
    anvil_fork_pool: AnvilForkPool,
    _gmx_deployment_baselines: dict[tuple[int, float], LagoonGMXForkEnv],
) -> Iterator[LagoonGMXForkEnv]:
    """Reuse deployment state while isolating EVM mutations and Python caches.

    Deployment assertions keep a fresh process. Trading checks share a baseline
    on their assigned worker and rebuild it if the pooled fork was recycled.
    Each test gets new contract adapters and a synchronised wallet nonce.

    :param chain_name:
        Explicit dependency so pytest collects the chain parametrisation.
    :param anvil_fork_pool:
        Session pool which probes liveness on every fixture request.
    :param _gmx_deployment_baselines:
        Module cache keyed by PID and process creation time.
    :return:
        Isolated environment, reverted to the deployed baseline on teardown.
    """
    assert chain_name == "arbitrum"
    launch = anvil_fork_pool.get_launch(
        os.environ["JSON_RPC_ARBITRUM"],
        ARBITRUM_MIDNIGHT_BLOCK,
        isolation_group="fork:arbitrum:gmx-lagoon",
        unlocked_addresses=[USDC_WHALE, WETH_WHALE],
        test_request_timeout=100,
        launch_wait_seconds=60,
    )
    with isolated_lagoon_gmx_fork_env(launch, _gmx_deployment_baselines) as env:
        yield env


@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_open_long_position(lagoon_gmx_fork_env: LagoonGMXForkEnv) -> None:
    """Test opening a long ETH position through LagoonGMXTradingWallet.

    Flow:
    1. Create order via GMXTrading
    2. Sign with LagoonGMXTradingWallet (wraps in performCall)
    3. Submit transaction
    4. Execute as keeper
    5. Verify position owned by Safe

    :param lagoon_gmx_fork_env:
        Shared deployment with isolated EVM state and fresh Python adapters.

    :return:
        None; assertions validate the behaviour.
    """
    env = lagoon_gmx_fork_env
    safe_address = env.vault.safe_address

    # Record initial state
    initial_positions = env.positions.get_data(safe_address)
    assert initial_positions == {}, "Previous test positions leaked across the baseline"
    initial_position_count = len(initial_positions)

    # Sync nonce
    env.lagoon_wallet.sync_nonce(env.web3)

    # === Step 1: Create order ===
    logger.info("Creating long ETH position order...")
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,  # $10 position
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=30,
    )

    assert isinstance(order_result, OrderResult)
    assert order_result.execution_fee > 0

    # === Step 2: Sign with LagoonGMXTradingWallet (wraps in performCall) ===
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    # Log the original transaction target
    logger.info("Original transaction target: %s", transaction.get("to"))
    logger.info("Expected ExchangeRouter: %s", GMX_EXCHANGE_ROUTER)
    logger.info("Target matches ExchangeRouter: %s", transaction.get("to") == GMX_EXCHANGE_ROUTER)

    logger.info("Signing transaction with LagoonGMXTradingWallet...")
    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)

    # === Step 3: Submit transaction ===
    logger.info("Submitting transaction...")
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(env.web3, tx_hash)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    logger.info("Order submitted: %s", tx_hash.hex())

    # Extract order key
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    # === Step 4: Execute as keeper ===
    logger.info("Executing order as keeper...")
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # === Step 5: Verify position owned by Safe ===
    logger.info("Verifying position...")
    final_positions = env.positions.get_data(safe_address)
    final_position_count = len(final_positions)

    assert final_position_count == initial_position_count + 1, "Should have 1 more position"

    # Get the new position
    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is True, "Position should be long"
    assert position["position_size"] > 0, "Position size should be > 0"

    logger.info("Position opened: %s %s", position["market_symbol"], "Long" if position["is_long"] else "Short")
    logger.info("Position size: $%.2f", position["position_size"])


@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_open_short_position(lagoon_gmx_fork_env: LagoonGMXForkEnv) -> None:
    """Test opening a short ETH position with USDC collateral through LagoonGMXTradingWallet.

    Create and execute a short order through the Safe, retaining position and
    token-balance assertions after the shared baseline is restored.

    :param lagoon_gmx_fork_env:
        Shared deployment with isolated EVM state and fresh Python adapters.

    :return:
        None; assertions validate the behaviour.
    """
    env = lagoon_gmx_fork_env
    safe_address = env.vault.safe_address

    # Record initial state
    initial_positions = env.positions.get_data(safe_address)
    assert initial_positions == {}, "Previous test positions leaked across the baseline"
    initial_position_count = len(initial_positions)

    # Sync nonce
    env.lagoon_wallet.sync_nonce(env.web3)

    # Create short position with USDC collateral
    logger.info("Creating short ETH position order...")
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=10,  # $10 position
        leverage=2.0,
        slippage_percent=0.005,
        execution_buffer=30,
    )

    assert isinstance(order_result, OrderResult)

    # Sign with LagoonGMXTradingWallet
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)

    # Submit
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1

    # Execute as keeper
    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1

    # Verify position
    final_positions = env.positions.get_data(safe_address)
    assert len(final_positions) == initial_position_count + 1

    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is False, "Position should be short"

    logger.info("Short position opened: %s", position["market_symbol"])


@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_cancel_limit_order(lagoon_gmx_fork_env: LagoonGMXForkEnv) -> None:
    """Open a GMX limit order through the vault, then cancel it through the Guard.

    Regression test for `issue #1050
    <https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1050>`__:
    ``TradingStrategyModuleV0`` previously rejected ``cancelOrder`` with
    ``"GMX: Unknown function in multicall"``, so a Lagoon vault could open a GMX
    order but never cancel a pending/timed-out one.

    Flow:

    1. Open a SHORT limit order with the trigger 10% above spot so it stays
       pending (a short triggers only when price rises to the trigger, and no
       keeper is run here).
    2. Extract the order key and confirm the order is pending.
    3. Build ``cancelOrder`` via :meth:`GMXTrading.cancel_order` and sign it
       through :class:`LagoonGMXTradingWallet`, which wraps it in
       ``performCall`` so it passes through the on-chain Guard.
    4. Submit and assert the cancel transaction succeeds — before the fix this
       reverted at guard validation.
    5. Confirm the order is no longer pending.

    :param lagoon_gmx_fork_env:
        Shared deployment with isolated EVM state and fresh Python adapters.

    :return:
        None; assertions validate the behaviour.
    """
    env = lagoon_gmx_fork_env
    safe_address = env.vault.safe_address
    assert env.positions.get_data(safe_address) == {}, "Previous positions leaked across the baseline"
    assert list(fetch_pending_orders(env.web3, "arbitrum", safe_address)) == []

    env.lagoon_wallet.sync_nonce(env.web3)

    # Trigger 10% above spot keeps a short limit order pending (no keeper run).
    eth_oracle_price, _ = fetch_on_chain_oracle_prices(env.web3)
    trigger_price = eth_oracle_price * 1.10

    # === Step 1: open a pending short limit order with USDC collateral ===
    logger.info("Creating short ETH limit order (trigger $%.2f)...", trigger_price)
    order_result = env.trading.open_limit_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=10,
        leverage=2.0,
        trigger_price=trigger_price,
        slippage_percent=0.005,
        execution_buffer=30,
        auto_cancel=False,
    )
    assert isinstance(order_result, OrderResult)

    transaction = order_result.transaction.copy()
    transaction.pop("nonce", None)
    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(env.web3, tx_hash)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    # === Step 2: confirm the order is pending ===
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    pending_before = list(fetch_pending_orders(env.web3, "arbitrum", safe_address))
    assert any(o.order_key == order_key for o in pending_before), "Limit order should be pending before cancel"

    # === Step 3 + 4: cancel through the Guard ===
    env.lagoon_wallet.sync_nonce(env.web3)
    cancel_result = env.trading.cancel_order(order_key)
    cancel_tx = cancel_result.transaction.copy()
    cancel_tx.pop("nonce", None)
    signed_cancel = env.lagoon_wallet.sign_transaction_with_new_nonce(cancel_tx)
    cancel_hash = env.web3.eth.send_raw_transaction(signed_cancel.rawTransaction)
    # Regression assertion: this reverted with "GMX: Unknown function in
    # multicall" before cancelOrder was whitelisted in GmxLib.
    assert_transaction_success_with_explanation(env.web3, cancel_hash)

    # === Step 5: order is gone ===
    pending_after = list(fetch_pending_orders(env.web3, "arbitrum", safe_address))
    assert not any(o.order_key == order_key for o in pending_after), "Order should be cancelled"

    logger.info("Limit order %s cancelled through the guard", order_key.hex())


@pytest.fixture()
def lagoon_gmx_forward_eth_env(
    anvil_chain_fork: AnvilLaunch,
) -> LagoonGMXForkEnv:
    """Initialise forward-ETH Lagoon state on an isolated fixed-block fork.

    The Safe starts with 0 ETH — the asset manager's hot wallet funds
    execution fees via forward_eth=True on LagoonGMXTradingWallet.

    :param anvil_chain_fork:
        Independent fixed-block Anvil process supplied by the GMX fixtures.

    :return:
        Newly deployed fee-forwarding environment.
    """
    return create_lagoon_gmx_fork_env(anvil_chain_fork, forward_eth=True)


@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_forward_eth_open_short(lagoon_gmx_forward_eth_env: LagoonGMXForkEnv) -> None:
    """Test that the asset manager can forward ETH for keeper fees.

    The Safe starts with 0 ETH. The asset manager sends ETH with
    the performCall transaction, which the module forwards to the Safe.
    Verifies that a GMX short position (ERC-20 collateral) succeeds
    despite the Safe having no pre-funded ETH.

    :param lagoon_gmx_forward_eth_env:
        Independent deployment whose manager supplies execution fees.

    :return:
        None; assertions validate the behaviour.
    """
    env = lagoon_gmx_forward_eth_env
    safe_address = env.vault.safe_address

    # Verify Safe starts with 0 ETH
    safe_eth_before = env.web3.eth.get_balance(safe_address)
    assert safe_eth_before == 0, f"Safe should start with 0 ETH, got {safe_eth_before}"

    env.lagoon_wallet.sync_nonce(env.web3)

    # Open a short position (ERC-20 collateral — USDC)
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=30,
    )

    assert isinstance(order_result, OrderResult)
    assert order_result.execution_fee > 0

    # Sign with forward_eth wallet — asset manager's ETH pays the keeper fee
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)

    # Submit transaction
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(env.web3, tx_hash)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    # Safe should now have ETH (forwarded from asset manager, minus what GMX took)
    safe_eth_after = env.web3.eth.get_balance(safe_address)
    logger.info("Safe ETH after order submission: %s wei", safe_eth_after)

    # Extract and execute the order
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # Verify position was created
    final_positions = env.positions.get_data(safe_address)
    assert len(final_positions) >= 1, "Should have at least 1 position"

    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is False

    logger.info("Forward-ETH test: short position opened successfully with 0 Safe ETH pre-funding")


@flaky(max_runs=3, min_passes=1)
def test_gmx_collateral_auto_approved_during_deployment(lagoon_gmx_deployment_env: LagoonGMXForkEnv) -> None:
    """Verify deploy_automated_lagoon_vault() auto-approves GMX collateral tokens.

    Regression test: production deployment failed with "Approve address not allowed"
    because the Guard didn't have SyntheticsRouter as an allowed approval destination.
    The deployment now automatically approves the underlying token and any extra
    tokens from gmx_deployment.tokens for the SyntheticsRouter.

    Check wallet identity and native balance against this same deployment to
    avoid deploying two additional vaults just for wallet accessors.

    :param lagoon_gmx_deployment_env:
        Fresh deployment used to verify automatic token approvals.

    :return:
        None; assertions validate the behaviour.
    """
    env = lagoon_gmx_deployment_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    module = env.deploy_info.trading_strategy_module

    # Wallet accessors must refer to the deployed Safe rather than its signer.
    assert env.lagoon_wallet.address == safe_address
    assert env.lagoon_wallet.address != env.asset_manager_wallet.get_main_address()
    balance = env.lagoon_wallet.get_native_currency_balance(web3)
    safe_balance_wei = web3.eth.get_balance(safe_address)
    expected_balance = Decimal(safe_balance_wei) / Decimal(10**18)
    assert balance == expected_balance
    assert balance > 0, "Safe should have ETH balance"

    # Verify guard-level whitelisting from whitelistGMX()
    assert module.functions.isAllowedApprovalDestination(GMX_SYNTHETICS_ROUTER).call(), "SyntheticsRouter not whitelisted as approval destination"
    assert module.functions.isAllowedTarget(GMX_EXCHANGE_ROUTER).call(), "ExchangeRouter not whitelisted as call target"

    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    weth = fetch_erc20_details(web3, WETH_ARBITRUM)

    # Both USDC (underlying) and WETH (extra token) should already be approved
    # for SyntheticsRouter by deploy_automated_lagoon_vault()
    usdc_allowance = usdc.contract.functions.allowance(safe_address, GMX_SYNTHETICS_ROUTER).call()
    assert usdc_allowance == 2**256 - 1, f"USDC not auto-approved: allowance={usdc_allowance}"

    weth_allowance = weth.contract.functions.allowance(safe_address, GMX_SYNTHETICS_ROUTER).call()
    assert weth_allowance == 2**256 - 1, f"WETH not auto-approved: allowance={weth_allowance}"
