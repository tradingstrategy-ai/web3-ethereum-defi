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
8. Print guard configuration report

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


Testnet mode
------------

Set ``NETWORK=testnet`` to deploy on Arbitrum Sepolia with real testnet transactions.
Unlike simulation mode, GMX keepers operate on Arbitrum Sepolia so the full
trading flow (open/close positions) works on testnet.

Unlike mainnet deployment, we do not use factory contracts — both Gnosis Safe
and all Lagoon contracts are deployed from scratch using Forge.

In testnet mode:

- Deploys on Arbitrum Sepolia (chain ID 421614)
- Uses GMX testnet tokens (different from Circle faucet USDC)
- No Chainlink price feeds (USD cost estimates are unavailable)
- Simulation is **not supported** on testnet because Lagoon factory contracts
  are not deployed on Sepolia chains

To get testnet ETH funding, use `LearnWeb3 faucet for Arbitrum Sepolia
<https://learnweb3.io/faucets/arbitrum_sepolia/>`__.
GMX test tokens are ``MintableToken`` contracts — call
``mint(your_address, amount)`` directly on Arbiscan Sepolia
(Write Contract → ``mint``).  GMX markets on Arbitrum Sepolia use
**USDC.SG** (``0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773``) as their
stablecoin, not the regular USDC.  Mint USDC.SG on `Arbiscan Sepolia
<https://sepolia.arbiscan.io/address/0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773#writeContract>`__.
For example, to mint 999 USDC.SG pass ``account`` = your wallet address
and ``amount`` = ``999000000`` (6 decimals, so 999 × 10⁶).

Example:

.. code-block:: shell

    NETWORK=testnet \
    JSON_RPC_ARBITRUM_SEPOLIA="https://sepolia-rollup.arbitrum.io/rpc" \
    GMX_PRIVATE_KEY="0x..." \
    python scripts/lagoon/lagoon-gmx-example.py

Environment variables
---------------------

``NETWORK``
    ``mainnet`` (default) or ``testnet``.  Selects the target chain
    (Arbitrum mainnet or Arbitrum Sepolia).

``SIMULATE``
    Set to ``true`` to run using an Anvil mainnet fork.  Only compatible
    with ``NETWORK=mainnet`` (the default).

``JSON_RPC_ARBITRUM``
    Arbitrum mainnet RPC endpoint.  Required when ``NETWORK=mainnet``.

``JSON_RPC_ARBITRUM_SEPOLIA``
    Arbitrum Sepolia RPC endpoint.  Required when ``NETWORK=testnet``.

``GMX_PRIVATE_KEY``
    Private key of a funded wallet.  Required in non-simulate modes.

``ETHERSCAN_API_KEY``
    Optional.  API key for contract verification on Arbiscan.

GMX minimum amounts
-------------------

GMX V2 has the following minimum requirements (per gmx-synthetics config):

- **Minimum collateral**: $1 USD
- **Minimum position size**: $1 USD

This script uses a $5 deposit and $5 position size with 1.1x leverage,
providing ~$4.55 collateral (above the $2 minimum).  GMX orders also
require ETH execution fees (~0.0001-0.001 ETH per order, paid to keepers).

The script uses ``forward_eth=True`` on ``LagoonGMXTradingWallet`` so
the asset manager's hot wallet pays keeper fees directly with each order.
The module forwards ``msg.value`` to the Safe, which then sends it to
the GMX ExchangeRouter.  The Safe does not need to hold ETH.

Source: https://github.com/gmx-io/gmx-synthetics/blob/main/config/general.ts


API documentation
-----------------

- GMX CCXT adapter: :py:mod:`eth_defi.gmx.ccxt`
- LagoonGMXTradingWallet: :py:mod:`eth_defi.gmx.lagoon.wallet`
- LagoonVault: :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.vault`
- Guard contract: contracts/guard/src/GuardV0Base.sol
"""

import dataclasses
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.chain import get_chain_name
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner import build_multichain_guard_config, fetch_guard_config_events, format_guard_config_report
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.testing import fund_lagoon_vault, redeem_vault_shares
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gas import apply_gas, estimate_gas_price
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.contracts import NETWORK_TOKENS, get_contract_addresses
from eth_defi.gmx.lagoon.approvals import UNLIMITED, approve_gmx_collateral_via_vault
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.gmx.whitelist import GMX_POPULAR_MARKETS, GMXDeployment, fetch_all_gmx_markets, resolve_gmx_market_labels
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, WRAPPED_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging

# ============================================================================
# Network configuration
# ============================================================================


@dataclass(slots=True)
class NetworkConfig:
    """Network-specific configuration resolved at runtime.

    Created once in :func:`main` based on the ``NETWORK`` environment variable
    and threaded through all helper functions.
    """

    #: Chain identifier (e.g. 42161 for Arbitrum, 421614 for Arbitrum Sepolia)
    chain_id: int

    #: GMX chain name for API calls (e.g. ``"arbitrum"`` or ``"arbitrum_sepolia"``)
    gmx_chain_name: str

    #: Name of the RPC environment variable to read
    rpc_env_var: str

    #: USDC token address (vault underlying and GMX collateral).
    #: On testnet this is USDC.SG — the stablecoin used by GMX markets.
    usdc_address: HexAddress

    #: GMX collateral symbol for CCXT order parameters.
    #: ``"USDC"`` on mainnet, ``"USDC.SG"`` on testnet (matches market tokens).
    gmx_collateral_symbol: str

    #: WETH token address
    weth_address: HexAddress

    #: GMX ExchangeRouter address
    gmx_exchange_router: HexAddress

    #: GMX SyntheticsRouter address
    gmx_synthetics_router: HexAddress

    #: GMX OrderVault address
    gmx_order_vault: HexAddress

    #: GMX ETH/USD market address.
    #: ``None`` on testnet — resolved dynamically after connecting to the chain.
    gmx_eth_usd_market: HexAddress | None

    #: USDC whale for simulation mode.
    #: ``None`` on testnet (simulation not supported).
    usdc_whale: HexAddress | None

    #: Chainlink ETH/USD aggregator address.
    #: ``None`` on testnet (no Chainlink feeds on Sepolia).
    chainlink_eth_usd: HexAddress | None

    #: Whether ``SIMULATE=true`` is supported on this network
    simulate_supported: bool

    #: Whether deployment must create contracts from scratch (no factory)
    from_the_scratch: bool

    #: Block explorer base URL (e.g. ``https://arbiscan.io``)
    explorer_url: str


def _create_mainnet_config() -> NetworkConfig:
    """Create configuration for Arbitrum mainnet."""
    chain_id = 42161
    addresses = get_contract_addresses("arbitrum")
    return NetworkConfig(
        chain_id=chain_id,
        gmx_chain_name="arbitrum",
        rpc_env_var="JSON_RPC_ARBITRUM",
        usdc_address=USDC_NATIVE_TOKEN[chain_id],
        gmx_collateral_symbol="USDC",
        weth_address=WRAPPED_NATIVE_TOKEN[chain_id],
        gmx_exchange_router=addresses.exchangerouter,
        gmx_synthetics_router=addresses.syntheticsrouter,
        gmx_order_vault=addresses.ordervault,
        gmx_eth_usd_market=GMX_POPULAR_MARKETS["ETH/USD"],
        usdc_whale=USDC_WHALE[chain_id],
        chainlink_eth_usd="0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",
        simulate_supported=True,
        from_the_scratch=False,
        explorer_url="https://arbiscan.io",
    )


def _create_testnet_config() -> NetworkConfig:
    """Create configuration for Arbitrum Sepolia testnet.

    GMX testnet uses its own test tokens (different from Circle faucet USDC).
    Test tokens are ``MintableToken`` contracts — call ``mint()`` directly on Arbiscan.

    .. note::

        GMX markets on Arbitrum Sepolia use **USDC.SG** (``0x3253...``) as
        their stablecoin, not the regular USDC (``0x3321...``).  The vault
        denomination must match the market collateral token, so we use
        USDC.SG everywhere on testnet.
    """
    addresses = get_contract_addresses("arbitrum_sepolia")
    gmx_tokens = NETWORK_TOKENS["arbitrum_sepolia"]
    return NetworkConfig(
        chain_id=421614,
        gmx_chain_name="arbitrum_sepolia",
        rpc_env_var="JSON_RPC_ARBITRUM_SEPOLIA",
        usdc_address=gmx_tokens["USDC.SG"],
        gmx_collateral_symbol="USDC.SG",
        weth_address=gmx_tokens["WETH"],
        gmx_exchange_router=addresses.exchangerouter,
        gmx_synthetics_router=addresses.syntheticsrouter,
        gmx_order_vault=addresses.ordervault,
        gmx_eth_usd_market=None,  # Resolved dynamically after connecting
        usdc_whale=None,
        chainlink_eth_usd=None,
        simulate_supported=False,
        from_the_scratch=True,
        explorer_url="https://sepolia.arbiscan.io",
    )


def _fetch_eth_usd_market(web3: Web3) -> HexAddress:
    """Dynamically fetch the ETH/USD market address from on-chain data.

    Used on testnet where :data:`GMX_POPULAR_MARKETS` (mainnet only) is
    not available.

    :param web3: Web3 instance connected to the target chain
    :return: ETH/USD market address
    :raises ValueError: If no ETH/USD market found
    """
    markets = fetch_all_gmx_markets(web3)
    for address, info in markets.items():
        if info.market_symbol == "ETH":
            return address
    available = ", ".join(str(info.market_symbol) for info in markets.values())
    raise ValueError(f"Could not find ETH/USD market on this chain. Available markets: {available}")


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
    cost_usd: Decimal | None = None
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


def get_eth_price_usd(web3: Web3, chainlink_eth_usd: HexAddress | None) -> Decimal:
    """Fetch current ETH/USD price from Chainlink.

    Uses the Chainlink ETH/USD price feed.
    See: https://docs.chain.link/data-feeds/price-feeds/addresses

    :param web3: Web3 instance
    :param chainlink_eth_usd:
        Chainlink aggregator address, or ``None`` if unavailable (testnet).
    :return: ETH price in USD
    :raises ValueError: If no Chainlink feed is available on this network
    """
    if chainlink_eth_usd is None:
        raise ValueError("No Chainlink ETH/USD feed available on this network")

    from eth_defi.abi import get_deployed_contract
    from eth_defi.chainlink.round_data import ChainLinkLatestRoundData

    aggregator = get_deployed_contract(
        web3,
        "ChainlinkAggregatorV2V3Interface.json",
        chainlink_eth_usd,
    )
    data = aggregator.functions.latestRoundData().call()
    round_data = ChainLinkLatestRoundData(aggregator, *data)
    return round_data.price


def broadcast_tx(
    web3: Web3,
    hot_wallet: HotWallet,
    bound_func: ContractFunction,
    description: str,
    config: NetworkConfig,
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
    :param config: Network configuration (for Chainlink price feed)
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
        eth_price = get_eth_price_usd(web3, config.chainlink_eth_usd)
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
    config: NetworkConfig,
    etherscan_api_key: str | None,
) -> LagoonAutomatedDeployment:
    """Deploy a Lagoon vault configured for GMX trading.

    The vault is deployed with:
    - TradingStrategyModuleV0 for trading automation
    - GMX ExchangeRouter, SyntheticsRouter, and OrderVault whitelisted
    - USDC and WETH whitelisted as tradeable assets
    - ETH/USDC market whitelisted for perpetuals trading
    - Safe address whitelisted as receiver for order proceeds

    For deployment details, see:
    :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault`

    :param web3: Web3 instance connected to the target chain
    :param hot_wallet: Deployer wallet (will become asset manager)
    :param config: Network configuration
    :param etherscan_api_key: API key for contract verification
    :return: Deployment result containing vault, Safe address, and block number
    """

    # Configure vault parameters
    # Using USDC as the base asset for the vault
    parameters = LagoonDeploymentParameters(
        underlying=config.usdc_address,
        name="GMX Trading Vault Tutorial",
        symbol="GMX-VAULT",
    )

    # Whitelist assets that can be held/traded
    assets = [
        config.usdc_address,  # Quote currency for positions
        config.weth_address,  # Can be used as collateral for longs
    ]

    # Single-owner Safe for simplicity (in production, use multiple owners)
    multisig_owners = [hot_wallet.address]

    # Configure GMX integration using GMXDeployment
    # This whitelists the GMX routers, order vault, and specified markets
    gmx_deployment = GMXDeployment(
        exchange_router=config.gmx_exchange_router,
        synthetics_router=config.gmx_synthetics_router,
        order_vault=config.gmx_order_vault,
        markets=[
            config.gmx_eth_usd_market,  # ETH/USD perpetuals market
        ],
        tokens=[
            config.usdc_address,  # Collateral token for GMX orders
            config.weth_address,  # Alternative collateral
        ],
    )

    print("\nDeploying Lagoon vault with GMX integration...")
    print(f"  Deployer/Asset Manager: {hot_wallet.address}")
    print(f"  Base asset: USDC ({config.usdc_address})")
    print(f"  GMX ExchangeRouter: {config.gmx_exchange_router}")
    print(f"  GMX Market: {config.gmx_eth_usd_market}")

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
        from_the_scratch=config.from_the_scratch,
        use_forge=True,
        assets=assets,
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=15.0,
    )

    vault = deploy_info.vault
    print(f"\nVault deployed successfully!")
    print(f"  Vault address: {vault.address}")
    print(f"  Safe address: {vault.safe_address}")
    print(f"  Trading module: {vault.trading_strategy_module_address}")

    print("GMX integration configured!")

    return deploy_info


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

    Uses :func:`~eth_defi.erc_4626.vault_protocol.lagoon.testing.fund_lagoon_vault`
    to handle the full ERC-7540 async deposit flow (request → settle → finalise).

    :param web3: Web3 instance
    :param hot_wallet: Depositor wallet
    :param vault: LagoonVault to deposit into
    :param usdc_amount: Amount of USDC to deposit (human-readable)
    """
    print(f"\nDepositing {usdc_amount} USDC to vault...")

    fund_lagoon_vault(
        web3,
        vault_address=vault.address,
        asset_manager=hot_wallet.address,
        test_account_with_balance=hot_wallet.address,
        trading_strategy_module_address=vault.trading_strategy_module_address,
        amount=usdc_amount,
        hot_wallet=hot_wallet,
    )

    usdc = fetch_erc20_details(web3, vault.underlying_token.address)
    final_balance = usdc.fetch_balance_of(vault.safe_address)
    share_balance = vault.share_token.fetch_balance_of(hot_wallet.address)
    print(f"\nDeposit complete!")
    print(f"  Safe USDC balance: {final_balance}")
    print(f"  Depositor share balance: {share_balance}")


# ============================================================================
# Step 3: Setup GMX trading
# ============================================================================


def setup_gmx_trading(
    web3: Web3,
    vault: LagoonVault,
    asset_manager: HotWallet,
    config: NetworkConfig,
    json_rpc_url: str,
) -> GMX:
    """Set up GMX trading through the Lagoon vault.

    Creates the LagoonGMXTradingWallet and GMX adapter, and approves USDC for trading.
    Returns a configured GMX instance that can be reused for multiple orders.

    :param web3: Web3 instance
    :param vault: LagoonVault holding the collateral
    :param asset_manager: Hot wallet of the asset manager
    :param config: Network configuration
    :param json_rpc_url: JSON-RPC URL for the GMX adapter
    :return: Configured GMX CCXT adapter instance
    """
    print("\nSetting up GMX trading...")

    # Create LagoonGMXTradingWallet to wrap transactions through the vault
    # This implements the BaseWallet interface expected by GMX
    lagoon_wallet = LagoonGMXTradingWallet(
        vault=vault,
        asset_manager=asset_manager,
        gas_buffer=500_000,  # Extra gas for performCall overhead
        forward_eth=True,  # Asset manager pays GMX keeper fees directly
    )
    lagoon_wallet.sync_nonce(web3)

    # Approve USDC for GMX SyntheticsRouter (one-time approval)
    # This approval comes FROM the Safe, so it's wrapped through performCall
    usdc = fetch_erc20_details(web3, config.usdc_address)
    approve_gmx_collateral_via_vault(
        vault=vault,
        asset_manager=asset_manager,
        collateral_token=usdc,
        amount=UNLIMITED,
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
    config: NetworkConfig,
    size_usd: Decimal,
    leverage: float,
    is_long: bool = True,
) -> dict:
    """Open a leveraged GMX position through the Lagoon vault.

    :param gmx: Configured GMX CCXT adapter instance
    :param config: Network configuration
    :param size_usd: Position size in USD
    :param leverage: Leverage multiplier (e.g., 2.0 for 2x)
    :param is_long: True for long, False for short
    :return: CCXT-style order result dict
    """
    collateral = config.gmx_collateral_symbol
    print(f"\nOpening {'LONG' if is_long else 'SHORT'} ETH position...")
    print(f"  Size: ${size_usd}")
    print(f"  Leverage: {leverage}x")
    print(f"  Collateral: ${float(size_usd) / leverage:.2f} {collateral}")

    # Create the order using CCXT-style interface
    symbol = f"ETH/{collateral}:{collateral}"
    order = gmx.create_order(
        symbol=symbol,
        type="market",
        side="buy" if is_long else "sell",
        amount=0,  # Ignored when size_usd is provided
        params={
            "size_usd": float(size_usd),
            "leverage": leverage,
            "collateral_symbol": collateral,
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
    config: NetworkConfig,
    size_usd: Decimal,
    is_long: bool = True,
) -> dict:
    """Close an existing GMX position through the Lagoon vault.

    :param gmx: Configured GMX CCXT adapter instance
    :param config: Network configuration
    :param size_usd: Position size to close (in USD)
    :param is_long: True if closing a long, False if closing a short
    :return: CCXT-style order result dict
    """
    collateral = config.gmx_collateral_symbol
    print(f"\nClosing {'LONG' if is_long else 'SHORT'} ETH position...")

    # Close the position (reduceOnly=True)
    symbol = f"ETH/{collateral}:{collateral}"
    order = gmx.create_order(
        symbol=symbol,
        type="market",
        side="sell" if is_long else "buy",
        amount=0,
        params={
            "size_usd": float(size_usd),
            "leverage": 1.0,  # Not relevant for closes
            "collateral_symbol": collateral,
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
    config: NetworkConfig,
) -> Decimal:
    """Withdraw all USDC from the Lagoon vault.

    Uses :func:`~eth_defi.erc_4626.vault_protocol.lagoon.testing.redeem_vault_shares`
    for the request phase, then settles and finalises manually.

    :param web3: Web3 instance
    :param hot_wallet: Wallet to receive the USDC
    :param vault: LagoonVault to withdraw from
    :param config: Network configuration
    :return: Amount of USDC withdrawn
    """
    usdc = fetch_erc20_details(web3, config.usdc_address)

    share_token = vault.share_token
    shares = share_token.fetch_balance_of(hot_wallet.address)

    print(f"\nWithdrawing from vault...")
    print(f"  Shares to redeem: {shares}")

    if shares == 0:
        print("  No shares to redeem")
        return Decimal("0")

    # Phase 1: Request redemption (approve + requestRedeem)
    redeem_vault_shares(
        web3,
        vault_address=vault.address,
        redeemer=hot_wallet.address,
        hot_wallet=hot_wallet,
    )

    # Phase 2: Settle the vault
    safe_usdc_balance = usdc.fetch_balance_of(vault.safe_address)

    broadcast_tx(
        web3,
        hot_wallet,
        vault.post_new_valuation(safe_usdc_balance),
        "Post vault valuation for withdrawal",
        config,
    )

    broadcast_tx(
        web3,
        hot_wallet,
        vault.settle_via_trading_strategy_module(safe_usdc_balance),
        "Settle vault for withdrawal",
        config,
    )

    # Phase 3: Finalise redemption (transfer USDC from silo to user)
    broadcast_tx(
        web3,
        hot_wallet,
        vault.finalise_redeem(hot_wallet.address),
        "Finalise redemption (claim USDC)",
        config,
    )

    # Check final balance
    final_usdc = usdc.fetch_balance_of(hot_wallet.address)
    print(f"\nWithdrawal complete! USDC balance: {final_usdc}")

    return final_usdc


# ============================================================================
# Simulation mode setup
# ============================================================================


def setup_simulation_environment(
    json_rpc_url: str,
    config: NetworkConfig,
) -> tuple[Web3, HotWallet, "AnvilLaunch"]:
    """Set up an Anvil fork environment for simulation.

    Creates:
    - An Anvil fork of Arbitrum mainnet
    - A test wallet funded with ETH and USDC

    :param json_rpc_url: Arbitrum RPC URL to fork from
    :param config: Network configuration
    :return: Tuple of (web3, hot_wallet, anvil_launch)
    """
    print("\nStarting Anvil fork of Arbitrum...")

    # Fork Arbitrum with whale account unlocked for funding
    # launch_anvil handles mev+ prefixed URLs automatically
    anvil_launch = fork_network_anvil(
        json_rpc_url,
        unlocked_addresses=[config.usdc_whale],
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
    usdc = fetch_erc20_details(web3, config.usdc_address)
    tx_hash = usdc.contract.functions.transfer(
        hot_wallet.address,
        100 * 10**6,  # 100 USDC
    ).transact({"from": config.usdc_whale, "gas": 100_000})
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

    # Parse network and mode
    network = os.environ.get("NETWORK", "mainnet").lower()
    if network not in ("mainnet", "testnet"):
        raise ValueError(f"NETWORK must be 'mainnet' or 'testnet', got '{network}'")

    simulate = os.environ.get("SIMULATE", "").lower() in ("true", "1", "yes")

    # Create network-specific configuration
    if network == "testnet":
        config = _create_testnet_config()
    else:
        config = _create_mainnet_config()

    # Validate testnet + simulate combination
    if simulate and not config.simulate_supported:
        raise ValueError("Testnet simulation (NETWORK=testnet SIMULATE=true) is not supported because Lagoon factory contracts are not deployed on Sepolia chains. Use mainnet simulation (SIMULATE=true) for local testing.")

    # Load configuration from environment
    json_rpc_url = os.environ.get(config.rpc_env_var)
    if not json_rpc_url:
        raise ValueError(f"{config.rpc_env_var} environment variable required. Set it to an RPC endpoint.")

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

            web3, hot_wallet, anvil_launch = setup_simulation_environment(json_rpc_url, config)
            # Don't verify contracts in simulation mode
            etherscan_api_key = None
        else:
            # Production / testnet mode: use real wallet and RPC
            private_key = os.environ.get("GMX_PRIVATE_KEY")
            if not private_key:
                raise ValueError("GMX_PRIVATE_KEY environment variable required. Set it to the private key of a funded wallet.")

            web3 = create_multi_provider_web3(json_rpc_url)

            mode_label = "TESTNET" if network == "testnet" else ""
            print("=" * 80)
            print(f"LAGOON-GMX TRADING TUTORIAL {mode_label}".strip())
            print("=" * 80)

            hot_wallet = HotWallet.from_private_key(private_key)
            hot_wallet.sync_nonce(web3)

        chain_id = web3.eth.chain_id
        chain_name = get_chain_name(chain_id)

        # Resolve GMX ETH/USD market dynamically on testnet
        if config.gmx_eth_usd_market is None:
            print("\nFetching ETH/USD market address from on-chain data...")
            config = dataclasses.replace(
                config,
                gmx_eth_usd_market=_fetch_eth_usd_market(web3),
            )
            print(f"  ETH/USD market: {config.gmx_eth_usd_market}")

        print(f"\nConnected to {chain_name} (chain ID: {chain_id})")
        print(f"Latest block: {web3.eth.block_number:,}")

        eth_balance = web3.eth.get_balance(hot_wallet.address)
        print(f"\nWallet: {hot_wallet.address}")
        print(f"ETH balance: {web3.from_wei(eth_balance, 'ether')} ETH")

        # Check USDC balance
        usdc = fetch_erc20_details(web3, config.usdc_address)
        usdc_balance = usdc.fetch_balance_of(hot_wallet.address)
        print(f"USDC balance: {usdc_balance}")

        if usdc_balance < deposit_amount:
            raise ValueError(f"Insufficient USDC. Need {deposit_amount}, have {usdc_balance}.")

        # Get current ETH price for cost calculations (not available on testnet)
        if config.chainlink_eth_usd is not None:
            try:
                eth_price = get_eth_price_usd(web3, config.chainlink_eth_usd)
                print(f"\nCurrent ETH price: ${eth_price:.2f}")
            except Exception:
                print("\nCould not fetch ETH price from Chainlink")
        else:
            print("\nETH price: N/A (no Chainlink feed on testnet)")

        # =========================================================================
        # Step 1: Deploy vault
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 1: Deploy Lagoon vault with GMX integration")
        print("=" * 80)

        logger.setLevel(logging.WARNING)  # Reduce noise during deployment
        deploy_from_block = web3.eth.block_number
        deploy_info = deploy_lagoon_vault(web3, hot_wallet, config, etherscan_api_key)
        vault = deploy_info.vault
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

            gmx = setup_gmx_trading(web3, vault, hot_wallet, config, json_rpc_url)

            # =========================================================================
            # Step 4: Open position
            # =========================================================================
            print("\n" + "=" * 80)
            print("STEP 4: Open leveraged ETH long position")
            print("=" * 80)

            open_order = open_gmx_position(
                gmx,
                config,
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
                config,
                size_usd=position_size,
                is_long=True,
            )

            # Wait for keeper
            print("\nWaiting for GMX keeper execution...")
            time.sleep(30)

        # =========================================================================
        # Step 6: Withdraw
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 6: Withdraw collateral from vault")
        print("=" * 80)

        hot_wallet.sync_nonce(web3)
        final_usdc = withdraw_from_vault(web3, hot_wallet, vault, config)

        # Calculate realised PnL
        _summary.realised_pnl = final_usdc - deposit_amount

        # =========================================================================
        # Step 7: Print summary
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 7: Trading summary")
        print("=" * 80)

        _summary.print_summary()

        # =========================================================================
        # Step 8: Print guard configuration report
        # =========================================================================
        print("\n" + "=" * 80)
        print("STEP 8: Guard configuration report")
        print("=" * 80)

        readback_chain_web3 = {chain_id: web3}

        events, module_addresses = fetch_guard_config_events(
            safe_address=vault.safe_address,
            web3=web3,
            chain_web3=readback_chain_web3,
            follow_cctp=False,
            from_block=deploy_from_block,
        )

        # Resolve GMX market names dynamically from chain
        gmx_market_labels = resolve_gmx_market_labels(web3)

        guard_config = build_multichain_guard_config(events, vault.safe_address, module_addresses)
        report = format_guard_config_report(
            config=guard_config,
            events=events,
            chain_web3=readback_chain_web3,
            known_labels=gmx_market_labels,
        )
        print(report)

        print("\nTutorial complete!")
        print(f"\nVault address: {vault.address}")
        if not simulate:
            print(f"View on explorer: {config.explorer_url}/address/{vault.address}")
        else:
            print("(Simulation mode - vault exists only in Anvil fork)")

    finally:
        # Clean up Anvil process if running
        if anvil_launch is not None:
            print("\nShutting down Anvil fork...")
            anvil_launch.close()


if __name__ == "__main__":
    main()
