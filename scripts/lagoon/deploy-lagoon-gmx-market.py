"""Deploy a Lagoon vault configured for one or more GMX perpetuals markets.

This script deploys a Lagoon vault with GMX integration for user-specified
market tokens. It handles the full deployment flow:

1. Resolve market addresses from token symbols (e.g. SOL, BTC, ETH)
2. Deploy a Lagoon vault with TradingStrategyModuleV0
3. Whitelist GMX contracts and all specified markets
4. Approve collateral for GMX trading
5. Print guard configuration report

The vault is ready for trading immediately after deployment.

Simulation mode
---------------

Set ``SIMULATE=true`` to run using an Anvil mainnet fork of Arbitrum.
No real funds are needed. The vault is deployed and configured but
trading steps are skipped (GMX keepers cannot be simulated locally).

Example:

.. code-block:: shell

    # Deploy a vault for SOL/USD trading (simulation)
    SIMULATE=true JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" \\
        python scripts/lagoon/deploy-lagoon-gmx-market.py -t SOL

    # Deploy a multi-market vault for ETH, BTC, and SOL
    JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" \\
        GMX_PRIVATE_KEY="0x..." \\
        python scripts/lagoon/deploy-lagoon-gmx-market.py -t ETH BTC SOL

    # Deploy for ETH/USD on testnet
    NETWORK=testnet JSON_RPC_ARBITRUM_SEPOLIA="https://sepolia-rollup.arbitrum.io/rpc" \\
        GMX_PRIVATE_KEY="0x..." \\
        python scripts/lagoon/deploy-lagoon-gmx-market.py -t ETH

    # List all available markets on Arbitrum
    JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc" \\
        python scripts/lagoon/deploy-lagoon-gmx-market.py --list-markets

Environment variables
---------------------

``NETWORK``
    ``mainnet`` (default) or ``testnet``.

``SIMULATE``
    Set to ``true`` to run using an Anvil mainnet fork.

``JSON_RPC_ARBITRUM``
    Arbitrum mainnet RPC endpoint. Required when ``NETWORK=mainnet``.

``JSON_RPC_ARBITRUM_SEPOLIA``
    Arbitrum Sepolia RPC endpoint. Required when ``NETWORK=testnet``.

``GMX_PRIVATE_KEY``
    Private key of a funded wallet. Required in non-simulate modes.

``ETHERSCAN_API_KEY``
    Optional. API key for contract verification on Arbiscan.

``DEPOSIT_AMOUNT``
    USDC amount to deposit into the vault. Default: 5.

``FORWARD_ETH``
    Set to ``true`` (default) so asset manager pays GMX keeper fees.
    Set to ``false`` to require the Safe to hold its own ETH.
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.config_event_scanner import (
    build_multichain_guard_config,
    fetch_guard_config_events,
    format_guard_config_report,
)
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.erc_4626.vault_protocol.lagoon.testing import fund_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.contracts import NETWORK_TOKENS, get_contract_addresses
from eth_defi.gmx.lagoon.approvals import UNLIMITED, approve_gmx_collateral_via_vault
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.gmx.whitelist import (
    GMX_POPULAR_MARKETS,
    GMXDeployment,
    fetch_all_gmx_markets,
    resolve_gmx_market_labels,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, WRAPPED_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging


logger = logging.getLogger(__name__)


# ============================================================================
# Network configuration
# ============================================================================


@dataclass(slots=True)
class NetworkConfig:
    """Network-specific configuration resolved at runtime."""

    #: Chain identifier (e.g. 42161 for Arbitrum, 421614 for Arbitrum Sepolia)
    chain_id: int

    #: GMX chain name for API calls (e.g. ``"arbitrum"`` or ``"arbitrum_sepolia"``)
    gmx_chain_name: str

    #: Name of the RPC environment variable to read
    rpc_env_var: str

    #: USDC token address (vault underlying and GMX collateral)
    usdc_address: HexAddress

    #: GMX collateral symbol for CCXT order parameters
    gmx_collateral_symbol: str

    #: WETH token address
    weth_address: HexAddress

    #: GMX ExchangeRouter address
    gmx_exchange_router: HexAddress

    #: GMX SyntheticsRouter address
    gmx_synthetics_router: HexAddress

    #: GMX OrderVault address
    gmx_order_vault: HexAddress

    #: USDC whale for simulation mode (``None`` on testnet)
    usdc_whale: HexAddress | None

    #: Whether ``SIMULATE=true`` is supported on this network
    simulate_supported: bool

    #: Whether deployment must create contracts from scratch (no factory)
    from_the_scratch: bool

    #: Block explorer base URL
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
        usdc_whale=USDC_WHALE[chain_id],
        simulate_supported=True,
        from_the_scratch=False,
        explorer_url="https://arbiscan.io",
    )


def _create_testnet_config() -> NetworkConfig:
    """Create configuration for Arbitrum Sepolia testnet."""
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
        usdc_whale=None,
        simulate_supported=False,
        from_the_scratch=True,
        explorer_url="https://sepolia.arbiscan.io",
    )


# ============================================================================
# Market resolution
# ============================================================================


def resolve_market_address(
    token_symbol: str,
    web3: Web3 | None = None,
) -> tuple[HexAddress, str]:
    """Resolve a token symbol to a GMX market address.

    First checks ``GMX_POPULAR_MARKETS`` for a quick lookup.
    Falls back to on-chain ``fetch_all_gmx_markets()`` if not found.

    :param token_symbol:
        Token symbol like "SOL", "BTC", "ETH", "LINK", etc.
        Case-insensitive.

    :param web3:
        Web3 instance for on-chain lookup fallback.
        Required if the symbol is not in ``GMX_POPULAR_MARKETS``.

    :return:
        Tuple of (market_address, canonical_symbol).

    :raises ValueError:
        If the market cannot be found.
    """
    symbol_upper = token_symbol.upper()
    market_key = f"{symbol_upper}/USD"

    # Quick lookup from popular markets
    if market_key in GMX_POPULAR_MARKETS:
        return GMX_POPULAR_MARKETS[market_key], symbol_upper

    # Fallback: fetch all markets on-chain
    if web3 is None:
        available = ", ".join(k.split("/")[0] for k in GMX_POPULAR_MARKETS)
        raise ValueError(f"Token '{symbol_upper}' not found in popular markets ({available}). Connect to a chain to search all available markets.")

    print(f"Token '{symbol_upper}' not in popular markets, fetching all markets on-chain...")
    all_markets = fetch_all_gmx_markets(web3)
    for address, info in all_markets.items():
        if str(info.market_symbol).upper() == symbol_upper:
            return address, symbol_upper

    available = ", ".join(str(info.market_symbol) for info in all_markets.values())
    raise ValueError(f"No GMX market found for '{symbol_upper}'. Available markets: {available}")


def list_all_markets(web3: Web3) -> None:
    """List all available GMX markets on the connected chain.

    :param web3: Web3 instance connected to Arbitrum or GMX-supported chain.
    """
    print("\nFetching all GMX markets from on-chain data...\n")
    all_markets = fetch_all_gmx_markets(web3)

    # Mark which ones are in GMX_POPULAR_MARKETS
    popular_addrs = set(Web3.to_checksum_address(a) for a in GMX_POPULAR_MARKETS.values())

    print(f"{'Symbol':<12} {'Market Address':<44} {'Popular'}")
    print("-" * 65)
    for address, info in sorted(all_markets.items(), key=lambda x: str(x[1].market_symbol)):
        checksummed = Web3.to_checksum_address(address)
        is_popular = "*" if checksummed in popular_addrs else ""
        print(f"{str(info.market_symbol):<12} {checksummed:<44} {is_popular}")

    print(f"\nTotal: {len(all_markets)} markets (* = in GMX_POPULAR_MARKETS)")


# ============================================================================
# Deployment
# ============================================================================


def deploy_market_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    config: NetworkConfig,
    resolved_markets: list[tuple[str, HexAddress]],
    etherscan_api_key: str | None,
    vault_name: str | None = None,
    vault_symbol: str | None = None,
) -> LagoonAutomatedDeployment:
    """Deploy a Lagoon vault configured for one or more GMX markets.

    :param web3: Web3 instance connected to the target chain.
    :param hot_wallet: Deployer wallet (will become asset manager).
    :param config: Network configuration.
    :param resolved_markets:
        List of (symbol, market_address) tuples for each market to whitelist.
    :param etherscan_api_key: API key for contract verification.
    :param vault_name: Custom vault name. Defaults to "{TOKEN1}-{TOKEN2}-... GMX Vault".
    :param vault_symbol: Custom vault symbol. Defaults to "{TOKEN1}-{TOKEN2}-...-GMX".
    :return: Deployment result.
    """
    symbols = [s for s, _ in resolved_markets]
    market_addresses = [a for _, a in resolved_markets]
    symbols_label = "-".join(symbols)

    name = vault_name or f"{symbols_label} GMX Vault"
    symbol = vault_symbol or f"{symbols_label}-GMX"

    parameters = LagoonDeploymentParameters(
        underlying=config.usdc_address,
        name=name,
        symbol=symbol,
    )

    # Whitelist USDC and WETH as tradeable assets
    assets = [
        config.usdc_address,
        config.weth_address,
    ]

    # Single-owner Safe
    multisig_owners = [hot_wallet.address]

    # Configure GMX integration with all specified markets
    gmx_deployment = GMXDeployment(
        exchange_router=config.gmx_exchange_router,
        synthetics_router=config.gmx_synthetics_router,
        order_vault=config.gmx_order_vault,
        markets=market_addresses,
        tokens=[
            config.usdc_address,
            config.weth_address,
        ],
    )

    markets_str = ", ".join(f"{s}/USD ({a})" for s, a in resolved_markets)
    print(f"\nDeploying Lagoon vault for {len(resolved_markets)} market(s)...")
    print(f"  Vault name:     {name}")
    print(f"  Vault symbol:   {symbol}")
    print(f"  Deployer:       {hot_wallet.address}")
    print(f"  Base asset:     {config.gmx_collateral_symbol} ({config.usdc_address})")
    print(f"  GMX Markets:    {markets_str}")
    print(f"  ExchangeRouter: {config.gmx_exchange_router}")

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=hot_wallet,
        asset_manager=hot_wallet.address,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=1,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        gmx_deployment=gmx_deployment,
        from_the_scratch=config.from_the_scratch,
        use_forge=True,
        assets=assets,
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=15.0,
    )

    vault = deploy_info.vault
    print(f"\nVault deployed!")
    print(f"  Vault address:    {vault.address}")
    print(f"  Safe address:     {vault.safe_address}")
    print(f"  Trading module:   {vault.trading_strategy_module_address}")

    return deploy_info


# ============================================================================
# Post-deployment setup
# ============================================================================


def setup_vault_trading(
    web3: Web3,
    vault: LagoonVault,
    asset_manager: HotWallet,
    config: NetworkConfig,
    forward_eth: bool = True,
) -> LagoonGMXTradingWallet:
    """Approve collateral and create the trading wallet.

    :param web3: Web3 instance.
    :param vault: Deployed Lagoon vault.
    :param asset_manager: Hot wallet of the asset manager.
    :param config: Network configuration.
    :param forward_eth: Whether asset manager pays GMX keeper fees.
    :return: Configured LagoonGMXTradingWallet.
    """
    print("\nApproving collateral for GMX trading...")

    usdc = fetch_erc20_details(web3, config.usdc_address)
    approve_gmx_collateral_via_vault(
        vault=vault,
        asset_manager=asset_manager,
        collateral_token=usdc,
        amount=UNLIMITED,
    )

    lagoon_wallet = LagoonGMXTradingWallet(
        vault=vault,
        asset_manager=asset_manager,
        gas_buffer=500_000,
        forward_eth=forward_eth,
    )
    lagoon_wallet.sync_nonce(web3)

    print("  Collateral approved and trading wallet ready.")
    return lagoon_wallet


# ============================================================================
# Simulation environment
# ============================================================================


def setup_simulation_environment(
    json_rpc_url: str,
    config: NetworkConfig,
) -> tuple[Web3, HotWallet, "fork_network_anvil"]:
    """Set up an Anvil fork environment for simulation.

    :param json_rpc_url: Arbitrum RPC URL to fork from.
    :param config: Network configuration.
    :return: Tuple of (web3, hot_wallet, anvil_launch).
    """
    print("\nStarting Anvil fork of Arbitrum...")

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

    hot_wallet = HotWallet.create_for_testing(web3, test_account_n=0, eth_amount=0)
    hot_wallet.sync_nonce(web3)

    # Fund with ETH (10 ETH for gas)
    web3.provider.make_request("anvil_setBalance", [hot_wallet.address, hex(10 * 10**18)])

    # Fund with USDC from whale (100 USDC)
    usdc = fetch_erc20_details(web3, config.usdc_address)
    tx_hash = usdc.contract.functions.transfer(
        hot_wallet.address,
        100 * 10**6,
    ).transact({"from": config.usdc_whale, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    print(f"\nSimulation wallet: {hot_wallet.address}")
    print(f"  ETH balance: 10 ETH (simulated)")
    print(f"  USDC balance: 100 USDC (from whale)")

    return web3, hot_wallet, anvil_launch


# ============================================================================
# Guard config report
# ============================================================================


def print_guard_report(
    web3: Web3,
    vault: LagoonVault,
    from_block: int,
) -> None:
    """Print the guard configuration report for a deployed vault.

    :param web3: Web3 instance.
    :param vault: Deployed vault.
    :param from_block: Block number to scan events from.
    """
    chain_id = web3.eth.chain_id
    readback_chain_web3 = {chain_id: web3}

    events, module_addresses = fetch_guard_config_events(
        safe_address=vault.safe_address,
        web3=web3,
        chain_web3=readback_chain_web3,
        follow_cctp=False,
        from_block=from_block,
    )

    gmx_market_labels = resolve_gmx_market_labels(web3)
    guard_config = build_multichain_guard_config(events, vault.safe_address, module_addresses)
    report = format_guard_config_report(
        config=guard_config,
        events=events,
        chain_web3=readback_chain_web3,
        known_labels=gmx_market_labels,
    )
    print(report)


# ============================================================================
# CLI
# ============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Deploy a Lagoon vault for one or more GMX perpetuals markets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Deploy SOL/USD vault (simulation)
  SIMULATE=true JSON_RPC_ARBITRUM="..." python %(prog)s -t SOL

  # Deploy multi-market vault for ETH, BTC, and SOL
  JSON_RPC_ARBITRUM="..." GMX_PRIVATE_KEY="0x..." python %(prog)s -t ETH BTC SOL

  # List all available markets
  JSON_RPC_ARBITRUM="..." python %(prog)s --list-markets

Popular tokens: ETH, BTC, SOL, LINK, ARB, DOGE, AVAX, NEAR, AAVE
""",
    )
    parser.add_argument(
        "-t",
        "--token",
        type=str,
        nargs="+",
        help="Token symbol(s) to trade (e.g. SOL, BTC, ETH). Multiple tokens create a multi-market vault.",
    )
    parser.add_argument(
        "--list-markets",
        action="store_true",
        help="List all available GMX markets and exit.",
    )
    parser.add_argument(
        "--vault-name",
        type=str,
        default=None,
        help="Custom vault name. Default: '{TOKEN} GMX Vault'.",
    )
    parser.add_argument(
        "--vault-symbol",
        type=str,
        default=None,
        help="Custom vault share token symbol. Default: '{TOKEN}-GMX'.",
    )
    parser.add_argument(
        "--deposit",
        type=float,
        default=None,
        help="USDC amount to deposit. Default: from DEPOSIT_AMOUNT env or 5.",
    )
    parser.add_argument(
        "--no-deposit",
        action="store_true",
        help="Skip depositing USDC into the vault.",
    )
    return parser.parse_args()


# ============================================================================
# Main
# ============================================================================


def main():
    """Deploy a market-specific Lagoon vault for GMX trading."""
    setup_console_logging()

    args = parse_args()

    # Validate: must have either --token or --list-markets
    if not args.token and not args.list_markets:
        print("Error: specify -t/--token or --list-markets", file=sys.stderr)
        sys.exit(1)

    # Parse network and mode
    network = os.environ.get("NETWORK", "mainnet").lower()
    if network not in ("mainnet", "testnet"):
        print(f"Error: NETWORK must be 'mainnet' or 'testnet', got '{network}'", file=sys.stderr)
        sys.exit(1)

    simulate = os.environ.get("SIMULATE", "").lower() in ("true", "1", "yes")

    # Create network config
    config = _create_testnet_config() if network == "testnet" else _create_mainnet_config()

    if simulate and not config.simulate_supported:
        print("Error: simulation is not supported on testnet.", file=sys.stderr)
        sys.exit(1)

    # Load RPC URL
    json_rpc_url = os.environ.get(config.rpc_env_var)
    if not json_rpc_url:
        print(f"Error: {config.rpc_env_var} environment variable required.", file=sys.stderr)
        sys.exit(1)

    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    deposit_amount = Decimal(str(args.deposit)) if args.deposit else Decimal(os.environ.get("DEPOSIT_AMOUNT", "5"))
    forward_eth = os.environ.get("FORWARD_ETH", "true").lower() not in ("false", "0", "no")

    anvil_launch = None

    try:
        # Setup web3 and wallet
        if simulate:
            web3, hot_wallet, anvil_launch = setup_simulation_environment(json_rpc_url, config)
            etherscan_api_key = None
        else:
            private_key = os.environ.get("GMX_PRIVATE_KEY")
            if not private_key and not args.list_markets:
                print("Error: GMX_PRIVATE_KEY required for non-simulate mode.", file=sys.stderr)
                sys.exit(1)

            web3 = create_multi_provider_web3(json_rpc_url)

            if not args.list_markets:
                hot_wallet = HotWallet.from_private_key(private_key)
                hot_wallet.sync_nonce(web3)

        chain_id = web3.eth.chain_id
        chain_name = get_chain_name(chain_id)
        print(f"\nConnected to {chain_name} (chain ID: {chain_id})")
        print(f"Latest block: {web3.eth.block_number:,}")

        # Handle --list-markets
        if args.list_markets:
            list_all_markets(web3)
            return

        # Resolve markets
        resolved_markets: list[tuple[str, HexAddress]] = []
        for token in args.token:
            market_address, canonical_symbol = resolve_market_address(token, web3)
            resolved_markets.append((canonical_symbol, market_address))
            print(f"  Resolved: {canonical_symbol}/USD -> {market_address}")

        symbols_label = "-".join(s for s, _ in resolved_markets)

        # Show wallet info
        eth_balance = web3.eth.get_balance(hot_wallet.address)
        usdc = fetch_erc20_details(web3, config.usdc_address)
        usdc_balance = usdc.fetch_balance_of(hot_wallet.address)

        print(f"\nWallet: {hot_wallet.address}")
        print(f"  ETH:  {web3.from_wei(eth_balance, 'ether')} ETH")
        print(f"  USDC: {usdc_balance}")

        if not args.no_deposit and usdc_balance < deposit_amount:
            print(f"\nError: insufficient USDC. Need {deposit_amount}, have {usdc_balance}.", file=sys.stderr)
            sys.exit(1)

        # =====================================================================
        # Step 1: Deploy vault
        # =====================================================================
        markets_display = ", ".join(f"{s}/USD" for s, _ in resolved_markets)
        mode_label = "SIMULATION" if simulate else ("TESTNET" if network == "testnet" else "MAINNET")
        print("\n" + "=" * 80)
        print(f"DEPLOYING {markets_display} VAULT ({mode_label})")
        print("=" * 80)

        deploy_from_block = web3.eth.block_number

        # Reduce noise during deployment
        logging.getLogger().setLevel(logging.WARNING)

        deploy_info = deploy_market_vault(
            web3=web3,
            hot_wallet=hot_wallet,
            config=config,
            resolved_markets=resolved_markets,
            etherscan_api_key=etherscan_api_key,
            vault_name=args.vault_name,
            vault_symbol=args.vault_symbol,
        )
        vault = deploy_info.vault

        logging.getLogger().setLevel(logging.INFO)
        hot_wallet.sync_nonce(web3)

        # =====================================================================
        # Step 2: Deposit (optional)
        # =====================================================================
        if not args.no_deposit:
            print("\n" + "=" * 80)
            print(f"DEPOSITING {deposit_amount} USDC")
            print("=" * 80)

            fund_lagoon_vault(
                web3,
                vault_address=vault.address,
                asset_manager=hot_wallet.address,
                test_account_with_balance=hot_wallet.address,
                trading_strategy_module_address=vault.trading_strategy_module_address,
                amount=deposit_amount,
                hot_wallet=hot_wallet,
            )

            safe_usdc = usdc.fetch_balance_of(vault.safe_address)
            shares = vault.share_token.fetch_balance_of(hot_wallet.address)
            print(f"\n  Safe USDC balance: {safe_usdc}")
            print(f"  Share balance:     {shares}")

        # =====================================================================
        # Step 3: Setup trading (approve collateral)
        # =====================================================================
        print("\n" + "=" * 80)
        print("SETTING UP GMX TRADING")
        print("=" * 80)

        hot_wallet.sync_nonce(web3)
        lagoon_wallet = setup_vault_trading(
            web3=web3,
            vault=vault,
            asset_manager=hot_wallet,
            config=config,
            forward_eth=forward_eth,
        )

        # =====================================================================
        # Step 4: Guard config report
        # =====================================================================
        print("\n" + "=" * 80)
        print("GUARD CONFIGURATION REPORT")
        print("=" * 80)

        print_guard_report(web3, vault, deploy_from_block)

        # =====================================================================
        # Summary
        # =====================================================================
        print("\n" + "=" * 80)
        print("DEPLOYMENT COMPLETE")
        print("=" * 80)
        print(f"\n  Markets:")
        for sym, addr in resolved_markets:
            print(f"    {sym}/USD  {addr}")
        print(f"  Vault address:    {vault.address}")
        print(f"  Safe address:     {vault.safe_address}")
        print(f"  Trading module:   {vault.trading_strategy_module_address}")
        print(f"  Asset manager:    {hot_wallet.address}")
        print(f"  Forward ETH:      {forward_eth}")

        if not simulate:
            print(f"\n  Vault on explorer:  {config.explorer_url}/address/{vault.address}")
            print(f"  Safe on explorer:   {config.explorer_url}/address/{vault.safe_address}")
        else:
            print("\n  (Simulation mode — vault exists only in Anvil fork)")

        print(f"\nTo trade {markets_display} through this vault, configure your")
        print(f"Freqtrade bot with vault_address={vault.address}")

    finally:
        if anvil_launch is not None:
            print("\nShutting down Anvil fork...")
            anvil_launch.close()


if __name__ == "__main__":
    main()
