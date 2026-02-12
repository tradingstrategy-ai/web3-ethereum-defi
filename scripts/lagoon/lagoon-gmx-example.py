"""Tutorial: Trading GMX perpetuals through a Lagoon vault.

This script demonstrates the complete lifecycle of trading GMX V2 perpetuals
through a Lagoon vault using the CCXT-compatible GMX adapter:

1. Deploy a Lagoon vault with GMX integration enabled
2. Deposit collateral (USDC) into the vault
3. Setup GMX trading (approve tokens, create adapter)
4. Open a leveraged ETH long position via GMX
5. Close the position and realise PnL
6. Withdraw collateral from the vault
7. Display summary of all transactions and costs

Simulation mode
---------------

Set ``SIMULATE=true`` to run the script using an Anvil mainnet fork of Arbitrum.
This allows testing vault deployment, deposit, and withdrawal flows
without needing real funds or a private key.

In simulation mode:
- An Anvil fork is spawned from JSON_RPC_ARBITRUM
- A test wallet is created and funded with ETH and USDC from whale accounts
- Trading steps (open/close position) are skipped because GMX keepers
  cannot be simulated in a local fork
- The vault deployment, deposit, and withdrawal flows are fully tested
- No real money is spent

Example:

.. code-block:: shell

    SIMULATE=true JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" python scripts/lagoon/lagoon-gmx-example.py

Architecture overview
---------------------

The Lagoon vault uses a Gnosis Safe multisig to hold assets securely.
Trading is performed through the TradingStrategyModuleV0, which wraps
all transactions via `performCall()`. This allows the asset manager's
hot wallet to execute trades while the Safe retains custody of funds.

::

    Asset Manager (Hot Wallet)
        │
        ▼
    TradingStrategyModuleV0.performCall()
        │
        ▼
    Gnosis Safe (Holds assets)
        │
        ▼
    GMX ExchangeRouter.multicall([sendWnt, sendTokens, createOrder])
        │
        ▼
    GMX Keeper (Executes order on-chain)

The Guard contract validates all GMX calls to ensure:
- Funds can only be sent to the GMX OrderVault (not arbitrary addresses)
- Order receivers are whitelisted (Safe address only)
- Only approved markets and collateral tokens can be used

For security details, see: README-GMX-Lagoon.md

ERC-7540 deposit/redeem flow
----------------------------

Lagoon vaults implement ERC-7540 (async redemption extension to ERC-4626).
Deposits and redemptions are asynchronous with a silo holding assets/shares
between request and finalisation.

Deposit flow::

    User                    Vault                   Silo                    Safe
      │                       │                       │                       │
      │── requestDeposit() ──▶│                       │                       │
      │   (transfer USDC)     │── hold USDC ─────────▶│                       │
      │                       │                       │                       │
      │                       │◀── settleDeposit() ───│                       │
      │                       │   (asset manager)     │── transfer USDC ─────▶│
      │                       │                       │                       │
      │                       │── mint shares ───────▶│                       │
      │                       │                       │                       │
      │◀── finaliseDeposit() ─│◀── transfer shares ───│                       │
      │   (claim shares)      │                       │                       │
      │                       │                       │                       │

Redeem flow::

    User                    Vault                   Silo                    Safe
      │                       │                       │                       │
      │── requestRedeem() ───▶│                       │                       │
      │   (transfer shares)   │── hold shares ───────▶│                       │
      │                       │                       │                       │
      │                       │◀── settleRedeem() ────│◀── transfer USDC ─────│
      │                       │   (asset manager)     │   (burn shares)       │
      │                       │                       │                       │
      │◀── finaliseRedeem() ──│◀── transfer USDC ─────│                       │
      │   (claim USDC)        │                       │                       │
      │                       │                       │                       │

GMX minimum amounts
-------------------

GMX V2 has the following minimum requirements (per gmx-synthetics config):

- **Minimum collateral**: $1 USD
- **Minimum position size**: $1 USD

This script uses $1.1 deposit and $1.1 position size to demonstrate
trading at near-minimum values. The Safe also needs ETH for execution
fees (~0.0001-0.001 ETH per order, paid to GMX keepers). The script
sends 0.001 ETH to the Safe before trading and recovers it afterwards.

Note: ``TradingStrategyModuleV0.performCall()`` is not ``payable``, so
execution fee ETH must come from the Safe's own balance — not from the
hot wallet's ``msg.value``.

Source: https://github.com/gmx-io/gmx-synthetics/blob/main/config/general.ts

Prerequisites
-------------

You need:
- An Arbitrum wallet funded with at least 0.01 ETH for gas + execution fees
- Some USDC on Arbitrum for trading collateral (~$50-100 recommended)
- JSON_RPC_ARBITRUM environment variable pointing to an Arbitrum RPC
- GMX_PRIVATE_KEY environment variable with your wallet private key
- ETHERSCAN_API_KEY for contract verification (optional but recommended)

API documentation
-----------------

- GMX CCXT adapter: :py:mod:`eth_defi.gmx.ccxt`
- LagoonWallet: :py:mod:`eth_defi.gmx.lagoon.wallet`
- LagoonVault: :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.vault`
- Guard contract: contracts/guard/src/GuardV0Base.sol
"""

import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from safe_eth.safe.safe import Safe

from eth_defi.chain import get_chain_name
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonDeploymentParameters, deploy_automated_lagoon_vault)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.contracts import get_contract_addresses
from eth_defi.gmx.lagoon.wallet import LagoonWallet
from eth_defi.gmx.whitelist import GMX_POPULAR_MARKETS, GMXDeployment
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, WRAPPED_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging

# ============================================================================
# Configuration constants
# ============================================================================

# Arbitrum chain ID
ARBITRUM_CHAIN_ID = 42161

# Token addresses on Arbitrum mainnet (from eth_defi.token)
USDC_ARBITRUM = USDC_NATIVE_TOKEN[ARBITRUM_CHAIN_ID]
WETH_ARBITRUM = WRAPPED_NATIVE_TOKEN[ARBITRUM_CHAIN_ID]

# GMX contract addresses - fetched dynamically from eth_defi.gmx.contracts
# These include ExchangeRouter, SyntheticsRouter, OrderVault, etc.
_GMX_ADDRESSES = get_contract_addresses("arbitrum")
GMX_EXCHANGE_ROUTER = _GMX_ADDRESSES.exchangerouter
GMX_SYNTHETICS_ROUTER = _GMX_ADDRESSES.syntheticsrouter
GMX_ORDER_VAULT = _GMX_ADDRESSES.ordervault

# GMX ETH/USD market on Arbitrum (from eth_defi.gmx.whitelist)
GMX_ETH_USDC_MARKET = GMX_POPULAR_MARKETS["ETH/USD"]

# Whale address for simulation mode (from eth_defi.token)
USDC_WHALE_ARBITRUM = USDC_WHALE[ARBITRUM_CHAIN_ID]


# ============================================================================
# Data structures for tracking transactions
# ============================================================================


@dataclass
class TransactionRecord:
    """Record of a single transaction for cost tracking."""

    description: str
    tx_hash: str
    gas_used: int
    gas_price_gwei: float
    cost_eth: Decimal
    cost_usd: Optional[Decimal] = None
    block_number: int = 0


@dataclass
class TradingSummary:
    """Summary of all trading activity and costs."""

    transactions: list[TransactionRecord] = field(default_factory=list)
    position_size_usd: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    exit_price: Decimal = Decimal("0")
    realised_pnl: Decimal = Decimal("0")
    total_gas_eth: Decimal = Decimal("0")
    total_gas_usd: Decimal = Decimal("0")
    gmx_execution_fees_eth: Decimal = Decimal("0")

    def add_transaction(self, record: TransactionRecord):
        """Add a transaction record and update totals."""
        self.transactions.append(record)
        self.total_gas_eth += record.cost_eth
        if record.cost_usd:
            self.total_gas_usd += record.cost_usd

    def print_summary(self):
        """Print a formatted summary of all trading activity."""
        print("\n" + "=" * 80)
        print("TRADING SUMMARY")
        print("=" * 80)

        print("\nTransactions:")
        print("-" * 80)
        for i, tx in enumerate(self.transactions, 1):
            cost_str = f"{tx.cost_eth:.6f} ETH"
            if tx.cost_usd:
                cost_str += f" (${tx.cost_usd:.2f})"
            print(f"  {i}. {tx.description}")
            print(f"     TX: {tx.tx_hash}")
            print(f"     Gas: {tx.gas_used:,} @ {tx.gas_price_gwei:.2f} gwei = {cost_str}")
            print()

        print("-" * 80)
        print("Position details:")
        print(f"  Size:        ${self.position_size_usd:.2f}")
        print(f"  Entry price: ${self.entry_price:.2f}")
        print(f"  Exit price:  ${self.exit_price:.2f}")
        print(f"  Realised PnL: ${self.realised_pnl:.2f}")

        print("\nCosts:")
        print(f"  Total gas:           {self.total_gas_eth:.6f} ETH (${self.total_gas_usd:.2f})")
        print(f"  GMX execution fees:  {self.gmx_execution_fees_eth:.6f} ETH")
        print(f"  Total costs:         {self.total_gas_eth + self.gmx_execution_fees_eth:.6f} ETH")

        print("\nNet result:")
        net_pnl = self.realised_pnl - self.total_gas_usd
        print(f"  PnL after costs: ${net_pnl:.2f}")
        print("=" * 80)


# ============================================================================
# Global state for transaction tracking
# ============================================================================

_tx_count = 0
_summary = TradingSummary()


def get_eth_price_usd(web3: Web3) -> Decimal:
    """Fetch current ETH/USD price from Chainlink on Arbitrum.

    Uses the Chainlink ETH/USD price feed on Arbitrum.
    See: https://docs.chain.link/data-feeds/price-feeds/addresses

    :param web3: Web3 instance connected to Arbitrum
    :return: ETH price in USD
    """
    from eth_defi.abi import get_deployed_contract
    from eth_defi.chainlink.round_data import ChainLinkLatestRoundData

    # Chainlink ETH/USD feed on Arbitrum
    aggregator_address = "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612"

    aggregator = get_deployed_contract(
        web3,
        "ChainlinkAggregatorV2V3Interface.json",
        aggregator_address,
    )
    data = aggregator.functions.latestRoundData().call()
    round_data = ChainLinkLatestRoundData(aggregator, *data)
    return round_data.price


def broadcast_tx(
    web3: Web3,
    hot_wallet: HotWallet,
    bound_func: ContractFunction,
    description: str,
    value: int | None = None,
    tx_params: dict | None = None,
    default_gas_limit: int = 1_000_000,
) -> tuple[SignedTransactionWithNonce, TransactionRecord]:
    """Broadcast a transaction and record its costs.

    This helper function:
    1. Estimates gas price
    2. Signs the transaction with the hot wallet
    3. Broadcasts and waits for confirmation
    4. Records the transaction details for the summary

    :param web3: Web3 instance
    :param hot_wallet: Wallet to sign with
    :param bound_func: Contract function to call
    :param description: Human-readable description for the summary
    :param value: ETH value to send (in wei)
    :param tx_params: Additional transaction parameters
    :param default_gas_limit: Default gas limit if not specified
    :return: Tuple of (signed transaction, transaction record)
    """
    global _tx_count, _summary

    _tx_count += 1

    # Estimate gas price
    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas(tx_params or {}, gas_price_suggestion)

    if "gas" not in tx_params:
        tx_params["gas"] = default_gas_limit

    # Sign and broadcast
    tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func, value=value, tx_params=tx_params)
    print(f"\nBroadcasting tx #{_tx_count}: {description}")
    print(f"  TX hash: {tx.hash.hex()}")

    broadcast_and_wait_transactions_to_complete(web3, [tx])

    # Get receipt for gas used
    receipt = web3.eth.get_transaction_receipt(tx.hash)
    gas_used = receipt["gasUsed"]
    gas_price = receipt.get("effectiveGasPrice", tx_params.get("maxFeePerGas", web3.eth.gas_price))
    gas_price_gwei = gas_price / 1e9
    cost_eth = Decimal(gas_used * gas_price) / Decimal(10**18)

    # Try to get USD cost
    try:
        eth_price = get_eth_price_usd(web3)
        cost_usd = cost_eth * eth_price
    except Exception:
        cost_usd = None

    record = TransactionRecord(
        description=description,
        tx_hash=tx.hash.hex(),
        gas_used=gas_used,
        gas_price_gwei=gas_price_gwei,
        cost_eth=cost_eth,
        cost_usd=cost_usd,
        block_number=receipt["blockNumber"],
    )

    _summary.add_transaction(record)
    print(f"  Gas used: {gas_used:,} @ {gas_price_gwei:.2f} gwei = {cost_eth:.6f} ETH")

    return tx, record


# ============================================================================
# Step 1: Deploy Lagoon vault with GMX integration
# ============================================================================


def deploy_lagoon_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    etherscan_api_key: str | None,
) -> LagoonVault:
    """Deploy a Lagoon vault configured for GMX trading.

    The vault is deployed with:
    - TradingStrategyModuleV0 for trading automation
    - GMX ExchangeRouter, SyntheticsRouter, and OrderVault whitelisted
    - USDC and WETH whitelisted as tradeable assets
    - ETH/USDC market whitelisted for perpetuals trading
    - Safe address whitelisted as receiver for order proceeds

    For deployment details, see:
    :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault`

    :param web3: Web3 instance connected to Arbitrum
    :param hot_wallet: Deployer wallet (will become asset manager)
    :param etherscan_api_key: API key for contract verification
    :return: Deployed LagoonVault instance
    """

    # Configure vault parameters
    # Using USDC as the base asset for the vault
    parameters = LagoonDeploymentParameters(
        underlying=USDC_ARBITRUM,
        name="GMX Trading Vault Tutorial",
        symbol="GMX-VAULT",
    )

    # Whitelist assets that can be held/traded
    assets = [
        USDC_ARBITRUM,  # Quote currency for positions
        WETH_ARBITRUM,  # Can be used as collateral for longs
    ]

    # Single-owner Safe for simplicity (in production, use multiple owners)
    multisig_owners = [hot_wallet.address]

    # Configure GMX integration using GMXDeployment
    # This whitelists the GMX routers, order vault, and specified markets
    gmx_deployment = GMXDeployment(
        exchange_router=GMX_EXCHANGE_ROUTER,
        synthetics_router=GMX_SYNTHETICS_ROUTER,
        order_vault=GMX_ORDER_VAULT,
        markets=[
            GMX_ETH_USDC_MARKET,  # ETH/USD perpetuals market
        ],
        tokens=[
            USDC_ARBITRUM,  # Collateral token for GMX orders
            WETH_ARBITRUM,  # Alternative collateral
        ],
    )

    print("\nDeploying Lagoon vault with GMX integration...")
    print(f"  Deployer/Asset Manager: {hot_wallet.address}")
    print(f"  Base asset: USDC ({USDC_ARBITRUM})")
    print(f"  GMX ExchangeRouter: {GMX_EXCHANGE_ROUTER}")
    print(f"  GMX Market: {GMX_ETH_USDC_MARKET}")

    # Deploy the vault with all integrations
    # The gmx_deployment parameter handles all GMX whitelisting automatically
    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=hot_wallet,
        asset_manager=hot_wallet.address,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=1,  # Single signature required
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,  # Only whitelisted assets allowed
        gmx_deployment=gmx_deployment,  # GMX integration configuration
        from_the_scratch=False,  # Use pre-deployed factory if available
        use_forge=True,  # Use forge for contract compilation
        assets=assets,
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=15.0,  # Wait between deployments
    )

    vault = deploy_info.vault
    print(f"\nVault deployed successfully!")
    print(f"  Vault address: {vault.address}")
    print(f"  Safe address: {vault.safe_address}")
    print(f"  Trading module: {vault.trading_strategy_module_address}")

    print("GMX integration configured!")

    return vault


# ============================================================================
# Step 2: Deposit collateral into the vault
# ============================================================================


def deposit_to_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    vault: LagoonVault,
    usdc_amount: Decimal,
) -> None:
    """Deposit USDC into the Lagoon vault.

    The deposit flow for Lagoon vaults:
    1. Approve vault to spend USDC
    2. Call requestDeposit() to queue the deposit
    3. Call settle() to process pending deposits (requires valuation)

    After settlement, the USDC is held in the Safe and can be used
    for GMX trading.

    :param web3: Web3 instance
    :param hot_wallet: Depositor wallet
    :param vault: LagoonVault to deposit into
    :param usdc_amount: Amount of USDC to deposit (human-readable)
    """
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    raw_amount = usdc.convert_to_raw(usdc_amount)

    print(f"\nDepositing {usdc_amount} USDC to vault...")

    # Step 1: Approve USDC transfer
    broadcast_tx(
        web3,
        hot_wallet,
        usdc.approve(vault.address, usdc_amount),
        "Approve USDC for vault deposit",
    )

    # Step 2: Request deposit
    broadcast_tx(
        web3,
        hot_wallet,
        vault.request_deposit(hot_wallet.address, raw_amount),
        "Request USDC deposit to vault",
    )

    # Step 3: Settle the vault
    # For initial deposit, valuation is 0 (NAV before this deposit)
    # For subsequent deposits, use current Safe balance
    safe_usdc_balance = usdc.fetch_balance_of(vault.safe_address)
    valuation = safe_usdc_balance  # Current balance BEFORE settlement

    broadcast_tx(
        web3,
        hot_wallet,
        vault.post_new_valuation(valuation),
        "Post vault valuation",
    )

    broadcast_tx(
        web3,
        hot_wallet,
        vault.settle_via_trading_strategy_module(valuation),
        "Settle vault deposits",
    )

    # Step 4: Finalise deposit (transfer shares to depositor's wallet)
    # ERC-7540 vaults hold shares in a silo until finalised
    broadcast_tx(
        web3,
        hot_wallet,
        vault.finalise_deposit(hot_wallet.address),
        "Finalise deposit (claim shares)",
    )

    # Verify deposit
    final_balance = usdc.fetch_balance_of(vault.safe_address)
    share_balance = vault.share_token.fetch_balance_of(hot_wallet.address)
    print(f"\nDeposit complete!")
    print(f"  Safe USDC balance: {final_balance}")
    print(f"  Depositor share balance: {share_balance}")


# ============================================================================
# Step 2b: Fund Safe with ETH for GMX execution fees
# ============================================================================


def fund_safe_with_eth(
    web3: Web3,
    hot_wallet: HotWallet,
    vault: LagoonVault,
    eth_amount: Decimal,
) -> None:
    """Send ETH from the hot wallet to the vault's Safe.

    GMX orders require ETH execution fees paid to keepers. The Safe must hold
    ETH because ``TradingStrategyModuleV0.performCall()`` sends the ``value``
    parameter from the Safe's own balance (the function is not ``payable``).

    The Safe has a ``receive()`` function so it accepts plain ETH transfers.

    :param web3: Web3 instance
    :param hot_wallet: Wallet that holds ETH
    :param vault: LagoonVault whose Safe needs funding
    :param eth_amount: Amount of ETH to send (human-readable)
    """
    global _tx_count, _summary
    _tx_count += 1

    wei_amount = int(eth_amount * Decimal(10**18))

    print(f"\nFunding Safe with {eth_amount} ETH for GMX execution fees...")
    print(f"  Safe address: {vault.safe_address}")

    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas({}, gas_price_suggestion)
    tx_params["chainId"] = web3.eth.chain_id
    tx_params["from"] = hot_wallet.address
    tx_params["to"] = vault.safe_address
    tx_params["value"] = wei_amount
    tx_params["gas"] = 50_000  # ETH transfer (extra buffer for Anvil fork)

    tx = hot_wallet.sign_transaction_with_new_nonce(tx_params)
    broadcast_and_wait_transactions_to_complete(web3, [tx])

    receipt = web3.eth.get_transaction_receipt(tx.hash)
    gas_used = receipt["gasUsed"]
    gas_price = receipt.get("effectiveGasPrice", web3.eth.gas_price)
    cost_eth = Decimal(gas_used * gas_price) / Decimal(10**18)

    try:
        eth_price = get_eth_price_usd(web3)
        cost_usd = cost_eth * eth_price
    except Exception:
        cost_usd = None

    record = TransactionRecord(
        description="Fund Safe with ETH for GMX execution fees",
        tx_hash=tx.hash.hex(),
        gas_used=gas_used,
        gas_price_gwei=gas_price / 1e9,
        cost_eth=cost_eth,
        cost_usd=cost_usd,
        block_number=receipt["blockNumber"],
    )
    _summary.add_transaction(record)

    safe_eth = web3.eth.get_balance(vault.safe_address)
    print(f"  Safe ETH balance: {web3.from_wei(safe_eth, 'ether')} ETH")


def recover_eth_from_safe(
    web3: Web3,
    hot_wallet: HotWallet,
    vault: LagoonVault,
) -> None:
    """Recover remaining ETH from the Safe back to the hot wallet.

    Uses a Safe owner-signed transaction (``execTransaction``) to send ETH
    directly from the Safe. This bypasses the Guard because it goes through
    the Safe's own execution path, not through the TradingStrategyModuleV0.

    :param web3: Web3 instance
    :param hot_wallet: Destination wallet (must be a Safe owner)
    :param vault: LagoonVault whose Safe holds the ETH
    """
    global _tx_count, _summary

    safe_eth_wei = web3.eth.get_balance(vault.safe_address)
    if safe_eth_wei == 0:
        print("\n  No ETH in Safe to recover")
        return

    safe_eth = Decimal(safe_eth_wei) / Decimal(10**18)
    print(f"\nRecovering {safe_eth:.6f} ETH from Safe to hot wallet...")

    ethereum_client = create_safe_ethereum_client(web3)
    safe = Safe(vault.safe_address, ethereum_client)

    # Build Safe owner transaction to send ETH to hot wallet
    safe_tx = safe.build_multisig_tx(hot_wallet.address, safe_eth_wei, b"")
    safe_tx.sign(hot_wallet.private_key.hex())

    gas_estimate = estimate_gas_price(web3)
    _tx_count += 1
    hot_wallet.sync_nonce(web3)
    tx_hash, tx = execute_safe_tx(
        safe_tx,
        tx_sender_private_key=hot_wallet.private_key.hex(),
        tx_gas=100_000,
        tx_nonce=hot_wallet.allocate_nonce(),
        gas_fee=gas_estimate,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    gas_used = receipt["gasUsed"]
    gas_price = receipt.get("effectiveGasPrice", web3.eth.gas_price)
    cost_eth = Decimal(gas_used * gas_price) / Decimal(10**18)

    try:
        eth_price = get_eth_price_usd(web3)
        cost_usd = cost_eth * eth_price
    except Exception:
        cost_usd = None

    record = TransactionRecord(
        description="Recover ETH from Safe to hot wallet",
        tx_hash=tx_hash.hex(),
        gas_used=gas_used,
        gas_price_gwei=gas_price / 1e9,
        cost_eth=cost_eth,
        cost_usd=cost_usd,
        block_number=receipt["blockNumber"],
    )
    _summary.add_transaction(record)
    print(f"  Recovered {safe_eth:.6f} ETH")


# ============================================================================
# Step 3: Setup GMX trading
# ============================================================================


def setup_gmx_trading(
    web3: Web3,
    vault: LagoonVault,
    asset_manager: HotWallet,
    json_rpc_url: str,
) -> GMX:
    """Set up GMX trading through the Lagoon vault.

    Creates the LagoonWallet and GMX adapter, and approves USDC for trading.
    Returns a configured GMX instance that can be reused for multiple orders.

    :param web3: Web3 instance
    :param vault: LagoonVault holding the collateral
    :param asset_manager: Hot wallet of the asset manager
    :return: Configured GMX CCXT adapter instance
    """
    print("\nSetting up GMX trading...")

    # Create LagoonWallet to wrap transactions through the vault
    # This implements the BaseWallet interface expected by GMX
    lagoon_wallet = LagoonWallet(
        vault=vault,
        asset_manager=asset_manager,
        gas_buffer=500_000,  # Extra gas for performCall overhead
    )
    lagoon_wallet.sync_nonce(web3)

    # Approve USDC for GMX SyntheticsRouter (one-time approval)
    # This approval comes FROM the Safe, so we wrap it through performCall
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    approve_call = usdc.contract.functions.approve(GMX_SYNTHETICS_ROUTER, 2**256 - 1)
    wrapped_approve = vault.transact_via_trading_strategy_module(approve_call)

    broadcast_tx(
        web3,
        asset_manager,
        wrapped_approve,
        "Approve USDC for GMX SyntheticsRouter",
        default_gas_limit=500_000,
    )

    # Create GMX CCXT adapter with the vault wallet
    gmx = GMX(
        params={
            "rpcUrl": json_rpc_url,
            "wallet": lagoon_wallet,
            "executionBuffer": 2.5,  # Higher buffer for reliability
            "defaultSlippage": 0.005,  # 0.5% slippage tolerance
        }
    )
    gmx.load_markets()

    print("GMX trading ready!")
    return gmx


# ============================================================================
# Step 4: Open a GMX position
# ============================================================================


def open_gmx_position(
    gmx: GMX,
    size_usd: Decimal,
    leverage: float,
    is_long: bool = True,
) -> dict:
    """Open a leveraged GMX position through the Lagoon vault.

    :param gmx: Configured GMX CCXT adapter instance
    :param size_usd: Position size in USD
    :param leverage: Leverage multiplier (e.g., 2.0 for 2x)
    :param is_long: True for long, False for short
    :return: CCXT-style order result dict
    """
    print(f"\nOpening {'LONG' if is_long else 'SHORT'} ETH position...")
    print(f"  Size: ${size_usd}")
    print(f"  Leverage: {leverage}x")
    print(f"  Collateral: ${float(size_usd) / leverage:.2f} USDC")

    # Create the order using CCXT-style interface
    order = gmx.create_order(
        symbol="ETH/USDC:USDC",
        type="market",
        side="buy" if is_long else "sell",
        amount=0,  # Ignored when size_usd is provided
        params={
            "size_usd": float(size_usd),
            "leverage": leverage,
            "collateral_symbol": "USDC",
            "wait_for_execution": False,
        },
    )

    print(f"\nOrder submitted!")
    print(f"  TX hash: {order.get('id', 'N/A')}")
    print(f"  Status: {order.get('status', 'N/A')}")

    # Record execution fee
    execution_fee = order.get("info", {}).get("execution_fee", 0)
    if execution_fee:
        _summary.gmx_execution_fees_eth += Decimal(execution_fee) / Decimal(10**18)

    # Update summary with position details
    _summary.position_size_usd = size_usd
    if order.get("price"):
        _summary.entry_price = Decimal(str(order["price"]))

    return order


# ============================================================================
# Step 5: Close the GMX position
# ============================================================================


def close_gmx_position(
    gmx: GMX,
    size_usd: Decimal,
    is_long: bool = True,
) -> dict:
    """Close an existing GMX position through the Lagoon vault.

    :param gmx: Configured GMX CCXT adapter instance
    :param size_usd: Position size to close (in USD)
    :param is_long: True if closing a long, False if closing a short
    :return: CCXT-style order result dict
    """
    print(f"\nClosing {'LONG' if is_long else 'SHORT'} ETH position...")

    # Close the position (reduceOnly=True)
    order = gmx.create_order(
        symbol="ETH/USDC:USDC",
        type="market",
        side="sell" if is_long else "buy",
        amount=0,
        params={
            "size_usd": float(size_usd),
            "leverage": 1.0,  # Not relevant for closes
            "collateral_symbol": "USDC",
            "reduceOnly": True,
            "wait_for_execution": False,
        },
    )

    print(f"\nClose order submitted!")
    print(f"  TX hash: {order.get('id', 'N/A')}")
    print(f"  Status: {order.get('status', 'N/A')}")

    # Record execution fee
    execution_fee = order.get("info", {}).get("execution_fee", 0)
    if execution_fee:
        _summary.gmx_execution_fees_eth += Decimal(execution_fee) / Decimal(10**18)

    # Update summary with exit price
    if order.get("price"):
        _summary.exit_price = Decimal(str(order["price"]))

    return order


# ============================================================================
# Step 6: Withdraw from the vault
# ============================================================================


def withdraw_from_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    vault: LagoonVault,
) -> Decimal:
    """Withdraw all USDC from the Lagoon vault.

    The withdrawal flow:
    1. Request redemption of all vault shares
    2. Settle the vault to process redemptions
    3. USDC is transferred to the withdrawer

    :param web3: Web3 instance
    :param hot_wallet: Wallet to receive the USDC
    :param vault: LagoonVault to withdraw from
    :return: Amount of USDC withdrawn
    """
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)

    # Get vault share balance using vault's share token
    share_token = vault.share_token
    shares = share_token.fetch_balance_of(hot_wallet.address)

    print(f"\nWithdrawing from vault...")
    print(f"  Shares to redeem: {shares}")

    if shares == 0:
        print("  No shares to redeem")
        return Decimal("0")

    raw_shares = share_token.convert_to_raw(shares)

    # Request redemption
    broadcast_tx(
        web3,
        hot_wallet,
        vault.request_redeem(hot_wallet.address, raw_shares),
        "Request vault redemption",
    )

    # Settle the vault
    safe_usdc_balance = usdc.fetch_balance_of(vault.safe_address)

    broadcast_tx(
        web3,
        hot_wallet,
        vault.post_new_valuation(safe_usdc_balance),
        "Post vault valuation for withdrawal",
    )

    broadcast_tx(
        web3,
        hot_wallet,
        vault.settle_via_trading_strategy_module(safe_usdc_balance),
        "Settle vault for withdrawal",
    )

    # Finalise redemption (transfer USDC from silo to user)
    # ERC-7540 vaults hold redeemed assets in a silo until finalised
    broadcast_tx(
        web3,
        hot_wallet,
        vault.finalise_redeem(hot_wallet.address),
        "Finalise redemption (claim USDC)",
    )

    # Check final balance
    final_usdc = usdc.fetch_balance_of(hot_wallet.address)
    print(f"\nWithdrawal complete! USDC balance: {final_usdc}")

    return final_usdc


# ============================================================================
# Simulation mode setup
# ============================================================================


def setup_simulation_environment(json_rpc_url: str) -> tuple[Web3, HotWallet, "AnvilLaunch"]:
    """Set up an Anvil fork environment for simulation.

    Creates:
    - An Anvil fork of Arbitrum mainnet
    - A test wallet funded with ETH and USDC

    :param json_rpc_url: Arbitrum RPC URL to fork from
    :return: Tuple of (web3, hot_wallet, anvil_launch)
    """
    print("\nStarting Anvil fork of Arbitrum...")

    # Fork Arbitrum with whale account unlocked for funding
    # launch_anvil handles mev+ prefixed URLs automatically
    anvil_launch = fork_network_anvil(
        json_rpc_url,
        unlocked_addresses=[USDC_WHALE_ARBITRUM],
    )

    web3 = create_multi_provider_web3(
        anvil_launch.json_rpc_url,
        default_http_timeout=(3.0, 180.0),
    )

    print(f"  Anvil fork running at: {anvil_launch.json_rpc_url}")
    print(f"  Forked at block: {web3.eth.block_number:,}")

    # Create a test wallet
    hot_wallet = HotWallet.create_for_testing(web3, test_account_n=0, eth_amount=0)
    hot_wallet.sync_nonce(web3)

    # Fund with ETH (10 ETH for gas)
    web3.provider.make_request("anvil_setBalance", [hot_wallet.address, hex(10 * 10**18)])

    # Fund with USDC from whale (100 USDC for testing)
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    tx_hash = usdc.contract.functions.transfer(
        hot_wallet.address,
        100 * 10**6,  # 100 USDC
    ).transact({"from": USDC_WHALE_ARBITRUM, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    print(f"\nSimulation wallet created: {hot_wallet.address}")
    print(f"  ETH balance: 10 ETH (simulated)")
    print(f"  USDC balance: 100 USDC (from whale)")

    return web3, hot_wallet, anvil_launch


# ============================================================================
# Main tutorial flow
# ============================================================================


def main():
    """Run the complete Lagoon-GMX trading tutorial."""
    global _summary

    # Setup logging
    logger = setup_console_logging()

    # Check for simulation mode
    simulate = os.environ.get("SIMULATE", "").lower() in ("true", "1", "yes")

    # Load configuration from environment
    json_rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not json_rpc_url:
        raise ValueError("JSON_RPC_ARBITRUM environment variable required. Set it to an Arbitrum RPC endpoint.")

    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    anvil_launch = None

    # Trading parameters
    # GMX requires >$2 collateral per position.
    # We deposit $5 to cover collateral plus execution fees buffer.
    # See: https://github.com/gmx-io/gmx-synthetics/blob/main/config/general.ts
    deposit_amount = Decimal("5")  # $5 USDC deposit
    position_size = Decimal("5")  # $5 position size
    leverage = 1.1  # 1.1x leverage (minimum allowed)

    try:
        if simulate:
            # Simulation mode: use Anvil fork with test wallet
            print("=" * 80)
            print("LAGOON-GMX TRADING TUTORIAL (SIMULATION MODE)")
            print("=" * 80)
            print("\nRunning in SIMULATION mode using Anvil fork.")
            print("Trading steps will be skipped (GMX keepers cannot be simulated).")

            web3, hot_wallet, anvil_launch = setup_simulation_environment(json_rpc_url)
            # Don't verify contracts in simulation mode
            etherscan_api_key = None
        else:
            # Production mode: use real wallet and RPC
            private_key = os.environ.get("GMX_PRIVATE_KEY")
            if not private_key:
                raise ValueError("GMX_PRIVATE_KEY environment variable required. Set it to the private key of a funded Arbitrum wallet.")

            # Connect to Arbitrum
            web3 = create_multi_provider_web3(json_rpc_url)

            print("=" * 80)
            print("LAGOON-GMX TRADING TUTORIAL")
            print("=" * 80)

            # Setup wallet
            hot_wallet = HotWallet.from_private_key(private_key)
            hot_wallet.sync_nonce(web3)

        chain_id = web3.eth.chain_id
        chain_name = get_chain_name(chain_id)

        print(f"\nConnected to {chain_name} (chain ID: {chain_id})")
        print(f"Latest block: {web3.eth.block_number:,}")

        eth_balance = web3.eth.get_balance(hot_wallet.address)
        print(f"\nWallet: {hot_wallet.address}")
        print(f"ETH balance: {web3.from_wei(eth_balance, 'ether')} ETH")

        # Check USDC balance
        usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
        usdc_balance = usdc.fetch_balance_of(hot_wallet.address)
        print(f"USDC balance: {usdc_balance}")

        if usdc_balance < deposit_amount:
            raise ValueError(f"Insufficient USDC. Need {deposit_amount}, have {usdc_balance}. Fund your wallet with USDC on Arbitrum.")

        # Get current ETH price for cost calculations
        eth_price = get_eth_price_usd(web3)
        print(f"\nCurrent ETH price: ${eth_price:.2f}")

        # =========================================================================
        # Step 1: Deploy vault
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 1: Deploy Lagoon vault with GMX integration")
        print("=" * 80)

        logger.setLevel(logging.WARNING)  # Reduce noise during deployment
        vault = deploy_lagoon_vault(web3, hot_wallet, etherscan_api_key)
        logger.setLevel(logging.INFO)

        # Re-sync nonce after deployment
        hot_wallet.sync_nonce(web3)

        # =========================================================================
        # Step 2: Deposit collateral
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 2: Deposit USDC collateral to vault")
        print("=" * 80)

        deposit_to_vault(web3, hot_wallet, vault, deposit_amount)

        # =========================================================================
        # Step 2b: Fund Safe with ETH for GMX execution fees
        # =========================================================================
        # The Safe needs ETH because performCall() sends the value parameter
        # from the Safe's own balance (the function is not payable).
        eth_for_safe = Decimal("0.001")
        print("\n" + "-" * 80)
        print("STEP 2b: Fund Safe with ETH for GMX execution fees")
        print("-" * 80)
        fund_safe_with_eth(web3, hot_wallet, vault, eth_for_safe)

        if simulate:
            # In simulation mode, skip trading steps
            print("\n" + "=" * 80)
            print("STEPS 3-5: SKIPPED (Simulation mode)")
            print("=" * 80)
            print("\nGMX trading steps are skipped in simulation mode because")
            print("GMX keepers cannot be simulated in a local Anvil fork.")
            print("The vault deployment and deposit/withdraw flows have been tested.")
        else:
            # =========================================================================
            # Step 3: Setup GMX trading
            # =========================================================================
            print("\n" + "=" * 80)
            print("STEP 3: Setup GMX trading")
            print("=" * 80)

            gmx = setup_gmx_trading(web3, vault, hot_wallet, json_rpc_url)

            # =========================================================================
            # Step 4: Open position
            # =========================================================================
            print("\n" + "=" * 80)
            print("STEP 4: Open leveraged ETH long position")
            print("=" * 80)

            open_order = open_gmx_position(
                gmx,
                size_usd=position_size,
                leverage=leverage,
                is_long=True,
            )

            # Wait for keeper execution
            print("\nWaiting for GMX keeper execution...")
            time.sleep(30)

            # =========================================================================
            # Step 5: Close position
            # =========================================================================
            print("\n" + "=" * 80)
            print("STEP 5: Close the position")
            print("=" * 80)

            close_order = close_gmx_position(
                gmx,
                size_usd=position_size,
                is_long=True,
            )

            # Wait for keeper
            print("\nWaiting for GMX keeper execution...")
            time.sleep(30)

        # =========================================================================
        # Step 5b: Recover ETH from Safe
        # =========================================================================
        # Recover remaining ETH before withdrawing USDC.
        # Uses a Safe owner-signed transaction (bypasses the Guard).
        print("\n" + "-" * 80)
        print("STEP 5b: Recover ETH from Safe")
        print("-" * 80)

        hot_wallet.sync_nonce(web3)
        recover_eth_from_safe(web3, hot_wallet, vault)

        # =========================================================================
        # Step 6: Withdraw
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 6: Withdraw collateral from vault")
        print("=" * 80)

        hot_wallet.sync_nonce(web3)
        final_usdc = withdraw_from_vault(web3, hot_wallet, vault)

        # Calculate realised PnL
        _summary.realised_pnl = final_usdc - deposit_amount

        # =========================================================================
        # Step 7: Print summary
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 7: Trading summary")
        print("=" * 80)

        _summary.print_summary()

        print("\nTutorial complete!")
        print(f"\nVault address: {vault.address}")
        if not simulate:
            print(f"View on Arbiscan: https://arbiscan.io/address/{vault.address}")
        else:
            print("(Simulation mode - vault exists only in Anvil fork)")

    finally:
        # Clean up Anvil process if running
        if anvil_launch is not None:
            print("\nShutting down Anvil fork...")
            anvil_launch.close()


if __name__ == "__main__":
    main()
