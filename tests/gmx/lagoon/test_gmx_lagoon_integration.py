"""Integration tests for LagoonGMXTradingWallet with real GMX contracts on Arbitrum fork.

Tests run against actual GMX V2 contracts using an Anvil fork of Arbitrum mainnet.
The LagoonGMXTradingWallet wraps all transactions through TradingStrategyModuleV0.performCall().
"""

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Generator

import pytest
from eth_account import Account
from eth_typing import HexAddress
from eth_utils import to_checksum_address
from flaky import flaky
from web3 import Web3
from web3.contract import Contract

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.gmx.order import OrderResult
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

from tests.gmx.fork_helpers import (
    execute_order_as_keeper,
    extract_order_key_from_receipt,
    setup_mock_oracle,
)

logger = logging.getLogger(__name__)

# Skip entire module if JSON_RPC_ARBITRUM not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ARBITRUM"),
    reason="JSON_RPC_ARBITRUM environment variable not set",
)

# GMX contract addresses - fetched dynamically to match what GMXTrading uses
# These are loaded at module level to catch address mismatches early
_GMX_ADDRESSES = get_contract_addresses("arbitrum")
GMX_EXCHANGE_ROUTER = _GMX_ADDRESSES.exchangerouter
GMX_SYNTHETICS_ROUTER = _GMX_ADDRESSES.syntheticsrouter
GMX_ORDER_VAULT = _GMX_ADDRESSES.ordervault

# Token addresses on Arbitrum
WETH_ARBITRUM = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
USDC_ARBITRUM = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

# Whale addresses for funding
USDC_WHALE = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
WETH_WHALE = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")


@dataclass
class LagoonGMXForkEnv:
    """All components needed for LagoonGMXTradingWallet + GMX fork testing."""

    web3: Web3
    vault: LagoonVault
    lagoon_wallet: LagoonGMXTradingWallet
    asset_manager_wallet: HotWallet
    gmx_config: GMXConfig
    trading: GMXTrading
    positions: GetOpenPositions
    anvil_launch: Any
    deploy_info: LagoonAutomatedDeployment


def _create_lagoon_gmx_fork_env(rpc_url: str) -> LagoonGMXForkEnv:
    """Create a complete isolated fork environment for LagoonGMXTradingWallet + GMX testing.

    Order of operations (CRITICAL):
    1. Spawn fresh Anvil fork
    2. Setup mock oracle FIRST
    3. Deploy Lagoon vault with TradingStrategyModuleV0
    4. Fund vault's Safe with USDC/WETH
    5. Create LagoonGMXTradingWallet
    6. Create GMXConfig pointing to Safe address
    7. Approve tokens for GMX
    """
    # === Step 1: Spawn fresh Anvil fork ===
    launch = fork_network_anvil(
        rpc_url,
        unlocked_addresses=[USDC_WHALE, WETH_WHALE],
    )

    web3 = create_multi_provider_web3(
        launch.json_rpc_url,
        default_http_timeout=(3.0, 180.0),
    )

    logger.info(f"Forked Arbitrum at block {web3.eth.block_number}")

    # === Step 2: Setup mock oracle FIRST ===
    setup_mock_oracle(web3)
    logger.info("Mock oracle configured")

    # === Step 3: Deploy Lagoon vault ===
    # Use Anvil's default private key for deployer
    deployer_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    deployer_account = Account.from_key(deployer_key)
    deployer_wallet = HotWallet(deployer_account)
    deployer_wallet.sync_nonce(web3)

    # Fund deployer with ETH
    deployer_address = deployer_wallet.get_main_address()
    web3.provider.make_request("anvil_setBalance", [deployer_address, hex(100 * 10**18)])

    # Create asset manager wallet (separate from deployer)
    asset_manager_key = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    asset_manager_account = Account.from_key(asset_manager_key)
    asset_manager_wallet = HotWallet(asset_manager_account)
    asset_manager_wallet.sync_nonce(web3)
    asset_manager_address = asset_manager_wallet.get_main_address()

    # Fund asset manager with ETH
    web3.provider.make_request("anvil_setBalance", [asset_manager_address, hex(100 * 10**18)])

    # Safe owners (use Anvil test accounts)
    safe_owners = [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]

    parameters = LagoonDeploymentParameters(
        underlying=USDC_ARBITRUM,
        name="Test GMX Vault",
        symbol="TGMX",
    )

    logger.info("Deploying Lagoon vault...")
    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_wallet,
        asset_manager=asset_manager_address,
        parameters=parameters,
        safe_owners=safe_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=True,  # Allow any asset for GMX trading
        cowswap=False,
        use_forge=True,
        from_the_scratch=False,
    )

    vault = deploy_info.vault
    safe_address = vault.safe_address
    module = deploy_info.trading_strategy_module
    logger.info(f"Lagoon vault deployed. Safe address: {safe_address}")

    # Fund Safe with ETH first (needed for whitelisting calls below)
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(100 * 10**18)])

    # === Step 3.5: Whitelist GMX contracts ===
    # After deployment, the Safe owns the module. We impersonate the Safe to call whitelisting functions.
    logger.info("Whitelisting GMX contracts...")

    # ETH/USDC market address on Arbitrum
    GMX_ETH_USDC_MARKET = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    # Impersonate the Safe address to call owner-only functions
    web3.provider.make_request("anvil_impersonateAccount", [safe_address])

    # Whitelist core GMX contracts
    tx_hash = module.functions.whitelistGMX(
        GMX_EXCHANGE_ROUTER,
        GMX_SYNTHETICS_ROUTER,
        GMX_ORDER_VAULT,
        "GMX V2 trading",
    ).transact({"from": safe_address, "gas": 500_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist ETH/USDC market
    tx_hash = module.functions.whitelistGMXMarket(
        GMX_ETH_USDC_MARKET,
        "ETH/USDC market",
    ).transact({"from": safe_address, "gas": 200_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist WETH and USDC tokens for trading
    tx_hash = module.functions.whitelistToken(
        WETH_ARBITRUM,
        "WETH collateral",
    ).transact({"from": safe_address, "gas": 200_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = module.functions.whitelistToken(
        USDC_ARBITRUM,
        "USDC collateral",
    ).transact({"from": safe_address, "gas": 200_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist Safe as receiver for GMX order proceeds
    tx_hash = module.functions.allowReceiver(
        safe_address,
        "Safe receives GMX order proceeds",
    ).transact({"from": safe_address, "gas": 200_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Stop impersonating
    web3.provider.make_request("anvil_stopImpersonatingAccount", [safe_address])

    # Verify whitelisting succeeded
    is_exchange_router_allowed = module.functions.isAllowedTarget(GMX_EXCHANGE_ROUTER).call()
    is_synthetics_router_approved = module.functions.isAllowedApprovalDestination(GMX_SYNTHETICS_ROUTER).call()
    logger.info(f"Whitelisting verification - ExchangeRouter allowed: {is_exchange_router_allowed}, SyntheticsRouter approved: {is_synthetics_router_approved}")
    assert is_exchange_router_allowed, f"ExchangeRouter {GMX_EXCHANGE_ROUTER} should be allowed"
    assert is_synthetics_router_approved, f"SyntheticsRouter {GMX_SYNTHETICS_ROUTER} should be approved"

    logger.info("GMX contracts whitelisted")

    # === Step 4: Fund vault's Safe with tokens ===
    # Fund whales with gas
    web3.provider.make_request("anvil_setBalance", [USDC_WHALE, hex(10 * 10**18)])
    web3.provider.make_request("anvil_setBalance", [WETH_WHALE, hex(10 * 10**18)])

    # Transfer USDC to Safe
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    usdc_amount = 100_000 * 10**6  # 100k USDC
    usdc.contract.functions.transfer(safe_address, usdc_amount).transact({"from": USDC_WHALE})

    # Transfer WETH to Safe
    weth = fetch_erc20_details(web3, WETH_ARBITRUM)
    weth_amount = 50 * 10**18  # 50 WETH
    weth.contract.functions.transfer(safe_address, weth_amount).transact({"from": WETH_WHALE})

    # Also fund Safe with native ETH for execution fees
    web3.provider.make_request("anvil_setBalance", [safe_address, hex(100 * 10**18)])

    logger.info(f"Safe funded: {usdc_amount / 10**6} USDC, {weth_amount / 10**18} WETH")

    # === Step 5: Create LagoonGMXTradingWallet ===
    lagoon_wallet = LagoonGMXTradingWallet(
        vault=vault,
        asset_manager=asset_manager_wallet,
        gas_buffer=500_000,  # Extra gas for performCall overhead
    )

    # Sync asset manager nonce
    asset_manager_wallet.sync_nonce(web3)

    # === Step 6: Create GMXConfig pointing to Safe address ===
    gmx_config = GMXConfig(web3, user_wallet_address=safe_address)

    # === Step 7: Approve tokens for GMX SyntheticsRouter ===
    # These approvals must come from the Safe, so we use performCall
    logger.info("Approving tokens for GMX...")

    # Approve USDC
    usdc_approve_call = usdc.contract.functions.approve(GMX_SYNTHETICS_ROUTER, 2**256 - 1)
    wrapped_usdc_approve = vault.transact_via_trading_strategy_module(usdc_approve_call)
    tx_hash = wrapped_usdc_approve.transact({"from": asset_manager_address, "gas": 500_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Approve WETH
    weth_approve_call = weth.contract.functions.approve(GMX_SYNTHETICS_ROUTER, 2**256 - 1)
    wrapped_weth_approve = vault.transact_via_trading_strategy_module(weth_approve_call)
    tx_hash = wrapped_weth_approve.transact({"from": asset_manager_address, "gas": 500_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    logger.info("Token approvals complete")

    # Sync nonce after approvals
    asset_manager_wallet.sync_nonce(web3)

    # Create trading and position instances
    trading = GMXTrading(gmx_config)
    positions = GetOpenPositions(gmx_config)

    return LagoonGMXForkEnv(
        web3=web3,
        vault=vault,
        lagoon_wallet=lagoon_wallet,
        asset_manager_wallet=asset_manager_wallet,
        gmx_config=gmx_config,
        trading=trading,
        positions=positions,
        anvil_launch=launch,
        deploy_info=deploy_info,
    )


@pytest.fixture()
def lagoon_gmx_fork_env() -> Generator[LagoonGMXForkEnv, None, None]:
    """Completely isolated fork environment for LagoonGMXTradingWallet + GMX testing.

    Each test gets its own fresh Anvil instance with:
    - Mock oracle set up FIRST
    - Deployed Lagoon vault with TradingStrategyModuleV0
    - Safe funded with USDC/WETH
    - LagoonGMXTradingWallet wrapping the vault
    - GMXConfig pointing to Safe address
    - Token approvals for GMX
    """
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM environment variable not set")

    env = _create_lagoon_gmx_fork_env(rpc_url)

    try:
        yield env
    finally:
        env.anvil_launch.close(log_level=logging.ERROR)


@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_open_long_position(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Test opening a long ETH position through LagoonGMXTradingWallet.

    Flow:
    1. Create order via GMXTrading
    2. Sign with LagoonGMXTradingWallet (wraps in performCall)
    3. Submit transaction
    4. Execute as keeper
    5. Verify position owned by Safe
    """
    env = lagoon_gmx_fork_env
    safe_address = env.vault.safe_address

    # Record initial state
    initial_positions = env.positions.get_data(safe_address)
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
    logger.info(f"Original transaction target: {transaction.get('to')}")
    logger.info(f"Expected ExchangeRouter: {GMX_EXCHANGE_ROUTER}")
    logger.info(f"Target matches ExchangeRouter: {transaction.get('to') == GMX_EXCHANGE_ROUTER}")

    logger.info("Signing transaction with LagoonGMXTradingWallet...")
    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)

    # === Step 3: Submit transaction ===
    logger.info("Submitting transaction...")
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(env.web3, tx_hash)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    logger.info(f"Order submitted: {tx_hash.hex()}")

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

    logger.info(f"Position opened: {position['market_symbol']} {'Long' if position['is_long'] else 'Short'}")
    logger.info(f"Position size: ${position['position_size']:.2f}")


@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_open_short_position(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Test opening a short ETH position with USDC collateral through LagoonGMXTradingWallet."""
    env = lagoon_gmx_fork_env
    safe_address = env.vault.safe_address

    # Record initial state
    initial_positions = env.positions.get_data(safe_address)
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

    logger.info(f"Short position opened: {position['market_symbol']}")


def test_lagoon_wallet_address_is_safe(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Verify LagoonGMXTradingWallet reports Safe address, not asset manager address."""
    env = lagoon_gmx_fork_env

    # LagoonGMXTradingWallet.address should return the Safe address
    assert env.lagoon_wallet.address == env.vault.safe_address
    assert env.lagoon_wallet.address != env.asset_manager_wallet.get_main_address()


def test_lagoon_wallet_native_balance(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Test that get_native_currency_balance returns Safe's ETH balance."""
    env = lagoon_gmx_fork_env

    balance = env.lagoon_wallet.get_native_currency_balance(env.web3)
    safe_balance_wei = env.web3.eth.get_balance(env.vault.safe_address)

    # balance returns Decimal in ETH, safe_balance_wei is int in wei
    expected_balance = Decimal(safe_balance_wei) / Decimal(10**18)
    assert balance == expected_balance
    assert balance > 0, "Safe should have ETH balance"
