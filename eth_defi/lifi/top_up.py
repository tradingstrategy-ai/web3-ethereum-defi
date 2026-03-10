"""Cross-chain gas top-up orchestration using LI.FI.

High-level functions for checking gas balances across multiple EVM
chains and bridging native tokens from a source chain to any target
chain that is running low.

This module provides :py:func:`perform_top_up` which orchestrates the
full flow: read configuration, check balances, prepare and execute
bridge swaps, verify delivery, and display final balances.

Environment variables:

- ``PRIVATE_KEY`` — hot wallet private key (0x-prefixed hex)
- ``SOURCE_CHAIN`` — source chain name (e.g. ``arbitrum``, ``base``)
- ``TARGET_CHAINS`` — comma-separated target chain names
- ``MIN_GAS_USD`` — minimum gas balance in USD (default: 5)
- ``TOP_UP_GAS_USD`` — amount to bridge when topping up in USD (default: 20)
- ``DRY_RUN`` — set to ``true`` to only display quotes without executing
- ``SOURCE_TOKEN`` — source token: ``native`` (default) or ``usdc``
- ``LIFI_API_KEY`` — optional LI.FI API key for higher rate limits
- ``JSON_RPC_*`` — RPC URLs for each chain
- ``LOG_LEVEL`` — logging level (default: ``warning``)

Example:

.. code-block:: python

    from eth_defi.lifi.top_up import perform_top_up

    perform_top_up()
"""

import logging
import os
from dataclasses import dataclass
from decimal import Decimal

from hexbytes import HexBytes
from tabulate import tabulate
from web3 import Web3

from eth_defi.chain import get_chain_id_by_name, get_chain_name
from eth_defi.hotwallet import HotWallet
from eth_defi.lifi.constants import DEFAULT_MIN_GAS_USD, DEFAULT_TOP_UP_GAS_USD, LIFI_NATIVE_TOKEN_ADDRESS
from eth_defi.lifi.crosschain import (
    CrossChainSwap,
    CrossChainSwapResult,
    execute_crosschain_swaps,
    fetch_crosschain_gas_balances,
    prepare_crosschain_swaps,
    wait_crosschain_swaps,
)
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.utils import setup_console_logging


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TopUpConfig:
    """Configuration for cross-chain gas top-up.

    Read from environment variables via :py:func:`read_top_up_config`.
    """

    #: Hot wallet private key
    private_key: str

    #: Source chain numeric ID
    source_chain_id: int

    #: Source chain human name
    source_chain_name: str

    #: Target chain numeric IDs
    target_chain_ids: list[int]

    #: Minimum gas balance in USD before triggering a top-up
    min_gas_usd: Decimal

    #: Amount to bridge in USD when topping up
    top_up_usd: Decimal

    #: Whether to only display quotes without executing
    dry_run: bool

    #: Source token: "native" or "usdc"
    source_token_choice: str

    #: Resolved source token address
    source_token_address: str


def read_top_up_config() -> TopUpConfig:
    """Read and validate top-up configuration from environment variables.

    :return:
        Validated configuration

    :raise ValueError:
        If required environment variables are missing or invalid
    """
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        raise ValueError("PRIVATE_KEY environment variable is required")

    source_chain_name = os.environ.get("SOURCE_CHAIN")
    if not source_chain_name:
        raise ValueError("SOURCE_CHAIN environment variable is required (e.g. 'arbitrum')")

    target_chains_str = os.environ.get("TARGET_CHAINS")
    if not target_chains_str:
        raise ValueError("TARGET_CHAINS environment variable is required (e.g. 'base,polygon,arbitrum')")

    min_gas_usd = Decimal(os.environ.get("MIN_GAS_USD", str(DEFAULT_MIN_GAS_USD)))
    top_up_usd = Decimal(os.environ.get("TOP_UP_GAS_USD", str(DEFAULT_TOP_UP_GAS_USD)))
    dry_run = os.environ.get("DRY_RUN", "false").lower() in ("true", "1", "yes")
    source_token_choice = os.environ.get("SOURCE_TOKEN", "native").lower().strip()

    # Resolve chain names to chain IDs
    source_chain_id = get_chain_id_by_name(source_chain_name)
    if source_chain_id is None:
        raise ValueError(f"Unknown source chain: {source_chain_name}")

    target_chain_names = [name.strip() for name in target_chains_str.split(",")]
    target_chain_ids = []
    for name in target_chain_names:
        if not name:
            continue
        chain_id = get_chain_id_by_name(name)
        if chain_id is None:
            raise ValueError(f"Unknown target chain: {name}")
        target_chain_ids.append(chain_id)

    # Resolve source token address
    if source_token_choice == "native":
        source_token_address = LIFI_NATIVE_TOKEN_ADDRESS
    elif source_token_choice == "usdc":
        usdc_address = USDC_NATIVE_TOKEN.get(source_chain_id)
        if not usdc_address:
            raise ValueError(f"No USDC address configured for source chain {source_chain_name} (chain_id={source_chain_id}). Supported chains: {list(USDC_NATIVE_TOKEN.keys())}")
        source_token_address = usdc_address
    else:
        raise ValueError(f"Unknown SOURCE_TOKEN value: {source_token_choice!r}. Use 'native' or 'usdc'.")

    return TopUpConfig(
        private_key=private_key,
        source_chain_id=source_chain_id,
        source_chain_name=source_chain_name,
        target_chain_ids=target_chain_ids,
        min_gas_usd=min_gas_usd,
        top_up_usd=top_up_usd,
        dry_run=dry_run,
        source_token_choice=source_token_choice,
        source_token_address=source_token_address,
    )


def create_connections(
    config: TopUpConfig,
) -> tuple[Web3, dict[int, Web3], HotWallet]:
    """Create Web3 connections and wallet from configuration.

    :param config:
        Top-up configuration

    :return:
        Tuple of (source_web3, target_web3s, wallet)
    """
    source_rpc_url = read_json_rpc_url(config.source_chain_id)
    source_web3 = create_multi_provider_web3(source_rpc_url)

    target_web3s = {}
    for chain_id in config.target_chain_ids:
        rpc_url = read_json_rpc_url(chain_id)
        target_web3s[chain_id] = create_multi_provider_web3(rpc_url)

    wallet = HotWallet.from_private_key(config.private_key)

    return source_web3, target_web3s, wallet


def display_balances(
    source_web3: Web3,
    target_web3s: dict[int, Web3],
    wallet: HotWallet,
    config: TopUpConfig,
):
    """Fetch and display gas balances across all chains.

    Shows a table with native token balance, USD value, USDC balance
    (for source chain), and status (OK/LOW) for each chain.

    :param source_web3:
        Web3 connection to the source chain

    :param target_web3s:
        Dict mapping chain_id to Web3 connection

    :param wallet:
        Hot wallet to check balances for

    :param config:
        Top-up configuration
    """
    all_web3s = {config.source_chain_id: source_web3, **target_web3s}
    balances_native, balances_usd = fetch_crosschain_gas_balances(
        target_web3s=all_web3s,
        wallet_address=wallet.address,
    )

    # Fetch USDC balance on the source chain if available
    source_usdc_address = USDC_NATIVE_TOKEN.get(config.source_chain_id)
    source_usdc_balance = Decimal("0")
    if source_usdc_address:
        usdc_details = fetch_erc20_details(source_web3, source_usdc_address)
        source_usdc_balance = usdc_details.fetch_balance_of(wallet.address)

    balance_table = []

    # Source chain first
    source_native = balances_native.get(config.source_chain_id, Decimal("0"))
    source_usd = balances_usd.get(config.source_chain_id, Decimal("0"))
    usdc_col = f"${source_usdc_balance:.2f}" if source_usdc_address else "-"
    balance_table.append(
        [
            config.source_chain_name,
            config.source_chain_id,
            f"{source_native:.6f}",
            f"${source_usd:.2f}",
            usdc_col,
            "-",
            "SOURCE",
        ]
    )

    # Target chains
    for chain_id in config.target_chain_ids:
        chain_name = get_chain_name(chain_id)
        native = balances_native.get(chain_id, Decimal("0"))
        usd = balances_usd.get(chain_id, Decimal("0"))
        status = "OK" if usd >= config.min_gas_usd else "LOW"
        balance_table.append(
            [
                chain_name,
                chain_id,
                f"{native:.6f}",
                f"${usd:.2f}",
                "-",
                f"${config.min_gas_usd:.2f}",
                status,
            ]
        )

    print("\nCurrent gas balances:")
    print(
        tabulate(
            balance_table,
            headers=["Chain", "ID", "Native balance", "USD value", "USDC balance", "Min required", "Status"],
            tablefmt="simple",
        )
    )


def display_proposed_swaps(swaps: list[CrossChainSwap]):
    """Display a table of proposed cross-chain swaps.

    :param swaps:
        List of prepared swaps
    """
    swap_table = []
    for swap in swaps:
        source_name = get_chain_name(swap.source_chain_id)
        target_name = get_chain_name(swap.target_chain_id)
        age = swap.quote.get_age_seconds()
        age_col = f"{age:.0f}s ago"
        swap_table.append(
            [
                f"{source_name} -> {target_name}",
                f"${swap.target_balance_usd:.2f}",
                f"${swap.from_amount_usd:.2f}",
                f"{swap.from_amount_raw} raw",
                f"~{swap.quote.execution_duration}s" if swap.quote.execution_duration else "N/A",
                age_col,
            ]
        )

    print(f"\nProposed swaps ({len(swaps)}):")
    print(
        tabulate(
            swap_table,
            headers=["Route", "Current balance", "Bridge amount", "From amount", "Est. duration", "Quote age"],
            tablefmt="simple",
        )
    )


def display_results(results: list[CrossChainSwapResult]):
    """Display a table of executed swap results.

    :param results:
        List of swap execution results
    """
    result_table = []
    for result in results:
        target_name = get_chain_name(result.swap.target_chain_id)
        result_table.append(
            [
                target_name,
                f"${result.swap.from_amount_usd:.2f}",
                result.tx_hash.hex(),
            ]
        )

    print(f"\nCompleted {len(results)} swap(s):")
    print(
        tabulate(
            result_table,
            headers=["Target chain", "Amount", "Tx hash"],
            tablefmt="simple",
        )
    )


def verify_and_display_final(
    results: list[CrossChainSwapResult],
    source_web3: Web3,
    target_web3s: dict[int, Web3],
    wallet: HotWallet,
    config: TopUpConfig,
):
    """Wait for bridge transfers, display verification status, and show final balances.

    1. Polls LI.FI ``/v1/status`` for each swap until delivery or timeout
    2. Displays a verification table with transfer statuses
    3. Fetches and displays updated gas balances on all chains

    :param results:
        List of swap execution results

    :param source_web3:
        Web3 connection to the source chain

    :param target_web3s:
        Dict mapping chain_id to Web3 connection

    :param wallet:
        Hot wallet to check balances for

    :param config:
        Top-up configuration
    """
    # Wait for bridge transfers to complete
    print("\nWaiting for bridge transfers to complete...")
    statuses = wait_crosschain_swaps(results)

    # Display verification table
    verify_table = []
    for result in results:
        target_name = get_chain_name(result.swap.target_chain_id)
        status_data = statuses.get(result.tx_hash, {})
        status = status_data.get("status", "UNKNOWN")

        # Extract receiving tx hash if available
        receiving = status_data.get("receiving", {})
        receiving_tx = receiving.get("txHash", "-")

        verify_table.append(
            [
                target_name,
                f"${result.swap.from_amount_usd:.2f}",
                status,
                receiving_tx,
            ]
        )

    print(f"\nBridge transfer status ({len(results)}):")
    print(
        tabulate(
            verify_table,
            headers=["Target chain", "Amount", "Status", "Receiving tx"],
            tablefmt="simple",
        )
    )

    # Display final balances
    print("\nFetching final balances...")
    display_balances(source_web3, target_web3s, wallet, config)


def perform_top_up():
    """Orchestrate the full cross-chain gas top-up flow.

    Reads configuration from environment variables, checks gas balances,
    prepares and executes bridge swaps, verifies delivery, and displays
    final balances.

    See module docstring for environment variable reference.
    """
    log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=log_level)

    config = read_top_up_config()

    logger.info("Source chain: %s (ID: %s)", config.source_chain_name, config.source_chain_id)
    logger.info("Min gas: $%s, Top-up: $%s", config.min_gas_usd, config.top_up_usd)

    source_web3, target_web3s, wallet = create_connections(config)
    logger.info("Wallet address: %s", wallet.address)

    # Display current balances
    display_balances(source_web3, target_web3s, wallet, config)

    # Prepare swaps
    print(f"\nPreparing cross-chain swaps (source token: {config.source_token_choice})...")
    swaps = prepare_crosschain_swaps(
        wallet=wallet,
        source_web3=source_web3,
        target_web3s=target_web3s,
        min_gas_usd=config.min_gas_usd,
        top_up_usd=config.top_up_usd,
        source_token_address=config.source_token_address,
    )

    if not swaps:
        print("\nAll chains have sufficient gas. Nothing to do.")
        return

    display_proposed_swaps(swaps)

    if config.dry_run:
        print("\nDry run mode - not executing any swaps.")
        return

    # Ask for confirmation
    print()
    response = input("Execute these swaps? [y/N]: ").strip().lower()
    if response != "y":
        print("Aborted.")
        return

    # Execute swaps
    print("\nExecuting swaps...")
    wallet.sync_nonce(source_web3)

    results = execute_crosschain_swaps(
        wallet=wallet,
        source_web3=source_web3,
        swaps=swaps,
    )

    display_results(results)

    # Verify and display final balances
    verify_and_display_final(results, source_web3, target_web3s, wallet, config)
