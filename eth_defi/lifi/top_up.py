"""Cross-chain gas top-up orchestration using LI.FI.

High-level functions for checking gas balances across multiple EVM
chains and bridging native tokens from a source chain to any target
chain that is running low.

This module provides :py:func:`perform_top_up` which orchestrates the
full flow: read configuration, check balances, prepare and execute
bridge swaps, verify delivery, and display final balances.

Environment variables:

- ``PRIVATE_KEY`` — hot wallet private key (0x-prefixed hex)
- ``SOURCE_CHAIN`` — source chain name (default: ``arbitrum``)
- ``TARGET_CHAINS`` — gas destinations (default: ``ethereum,hyperliquid``)
- ``ETHEREUM_USDC_AMOUNT`` — optional source USDC amount to bridge to Ethereum
  in addition to gas top-ups (default: 0, disabled). Fees reduce received USDC.
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

# ruff: noqa: T201 -- operator-facing tables and confirmation prompt.

import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal

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
from eth_defi.lifi.quote import fetch_lifi_quote
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
    private_key: str = field(repr=False)

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

    #: Explicit source USDC spend for Ethereum; zero leaves USDC untouched.
    ethereum_usdc_amount: Decimal = Decimal(0)


def read_top_up_config() -> TopUpConfig:
    """Read and validate top-up configuration from environment variables.

    :return:
        Validated configuration

    :raise ValueError:
        If required environment variables are missing or invalid
    """
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        message = "PRIVATE_KEY environment variable is required"
        raise ValueError(message)

    source_chain_name = os.environ.get("SOURCE_CHAIN", "arbitrum")
    target_chains_str = os.environ.get("TARGET_CHAINS", "ethereum,hyperliquid")
    ethereum_usdc_amount = Decimal(os.environ.get("ETHEREUM_USDC_AMOUNT", "0"))
    if not ethereum_usdc_amount.is_finite() or ethereum_usdc_amount < 0:
        message = "ETHEREUM_USDC_AMOUNT must be finite and non-negative"
        raise ValueError(message)

    min_gas_usd = Decimal(os.environ.get("MIN_GAS_USD", str(DEFAULT_MIN_GAS_USD)))
    top_up_usd = Decimal(os.environ.get("TOP_UP_GAS_USD", str(DEFAULT_TOP_UP_GAS_USD)))
    dry_run = os.environ.get("DRY_RUN", "false").lower() in {"true", "1", "yes"}
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

    target_chain_ids = list(dict.fromkeys(c for c in target_chain_ids if c != source_chain_id))
    if ethereum_usdc_amount and (source_chain_id == 1 or source_chain_id not in USDC_NATIVE_TOKEN):
        message = "ETHEREUM_USDC_AMOUNT requires a non-Ethereum source with native USDC"
        raise ValueError(message)

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
        ethereum_usdc_amount=ethereum_usdc_amount,
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

    assert source_web3.eth.chain_id == config.source_chain_id, "Wrong source RPC chain"
    target_web3s = {}
    required_chains = set(config.target_chain_ids) | ({1} if config.ethereum_usdc_amount else set())
    for chain_id in sorted(required_chains):
        rpc_url = read_json_rpc_url(chain_id)
        target_web3s[chain_id] = create_multi_provider_web3(rpc_url)
        assert target_web3s[chain_id].eth.chain_id == chain_id, "Wrong destination RPC chain"

    wallet = HotWallet.from_private_key(config.private_key)

    return source_web3, target_web3s, wallet


def display_balances(
    source_web3: Web3,
    target_web3s: dict[int, Web3],
    wallet: HotWallet,
    config: TopUpConfig,
) -> None:
    """Fetch and display gas balances across all chains.

    Shows a table with native token balance, USD value, USDC balance
    and status (OK/LOW) for each gas target. USDC-only targets are labelled.

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

    balance_table = []
    for chain_id, web3 in all_web3s.items():
        token_address = USDC_NATIVE_TOKEN.get(chain_id)
        usdc_balance = str(fetch_erc20_details(web3, token_address).fetch_balance_of(wallet.address)) if token_address else "-"
        status = "SOURCE" if chain_id == config.source_chain_id else "USDC only"
        if chain_id in config.target_chain_ids:
            status = "OK" if balances_usd[chain_id] >= config.min_gas_usd else "LOW"
        balance_table.append(
            [
                get_chain_name(chain_id),
                chain_id,
                f"{balances_native[chain_id]:.6f}",
                f"${balances_usd[chain_id]:.2f}",
                usdc_balance,
                status,
            ]
        )
    print("\nCurrent wallet balances:")
    print(tabulate(balance_table, headers=["Chain", "ID", "Native balance", "USD value", "USDC balance", "Status"]))


def display_proposed_swaps(swaps: list[CrossChainSwap]) -> None:
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
                "USDC" if swap.quote.to_token.lower() == USDC_NATIVE_TOKEN.get(swap.target_chain_id, "").lower() else "native",
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
            headers=["Route", "Receive", "Current balance", "Bridge amount", "From amount", "Est. duration", "Quote age"],
            tablefmt="simple",
        )
    )


def display_results(results: list[CrossChainSwapResult]) -> None:
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

    print(f"\nConfirmed {len(results)} source transaction(s); delivery still pending:")
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
) -> None:
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

    incomplete = [r.tx_hash.hex() for r in results if statuses.get(r.tx_hash, {}).get("status") != "DONE" or statuses.get(r.tx_hash, {}).get("substatus") != "COMPLETED"]
    if incomplete:
        raise RuntimeError(f"Transfer not delivered as quoted: {incomplete}. Inspect original transactions before retrying; partial/refunded transfers are not successful funding.")

    # Display final balances
    print("\nFetching final balances...")
    display_balances(source_web3, target_web3s, wallet, config)


def prepare_ethereum_usdc_transfer(
    source_web3: Web3,
    ethereum_web3: Web3,
    wallet: HotWallet,
    amount: Decimal,
) -> CrossChainSwap:
    """Quote an explicit native-USDC transfer to the same wallet on Ethereum.

    This is an additional transfer regardless of destination gas balance.
    The amount is source spending, not guaranteed received USDC; fees apply.
    Uses the existing `LI.FI quote API <https://docs.li.fi/api-reference/get-a-quote-for-a-token-transfer>`_.

    :param source_web3: Non-Ethereum source connection with native USDC.
    :param ethereum_web3: Ethereum mainnet connection.
    :param wallet: Source signer and destination recipient.
    :param amount: Positive human-readable source USDC spend.
    :return: Quote suitable for the normal approval/execution flow.
    """
    source_chain = source_web3.eth.chain_id
    assert source_chain != 1 and ethereum_web3.eth.chain_id == 1
    assert amount.is_finite() and amount > 0
    source_token = fetch_erc20_details(source_web3, USDC_NATIVE_TOKEN[source_chain])
    destination_token = fetch_erc20_details(ethereum_web3, USDC_NATIVE_TOKEN[1])
    raw = source_token.convert_to_raw(amount)
    assert raw > 0 and source_token.convert_to_decimals(raw) == amount, "USDC amount has unsupported precision"
    assert source_token.fetch_balance_of(wallet.address) >= amount, "Insufficient source USDC"
    quote = fetch_lifi_quote(
        from_chain_id=source_chain,
        to_chain_id=1,
        from_token=source_token.address,
        to_token=destination_token.address,
        from_amount=raw,
        from_address=wallet.address,
        to_address=wallet.address,
    )
    assert quote.get_transaction_request() and quote.estimate_to_amount_min > 0, "No executable Ethereum USDC route"
    print(f"Ethereum USDC: spend {amount}; estimated receive {destination_token.convert_to_decimals(quote.estimate_to_amount)}; quoted minimum {destination_token.convert_to_decimals(quote.estimate_to_amount_min)}")
    return CrossChainSwap(
        source_chain_id=source_chain,
        target_chain_id=1,
        from_amount_raw=raw,
        from_amount_usd=amount,
        target_balance_usd=destination_token.fetch_balance_of(wallet.address),
        min_gas_usd=Decimal(0),
        top_up_usd=amount,
        transaction_request=quote.get_transaction_request(),
        quote=quote,
    )


def perform_top_up() -> None:
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
        target_web3s={c: target_web3s[c] for c in config.target_chain_ids},
        min_gas_usd=config.min_gas_usd,
        top_up_usd=config.top_up_usd,
        source_token_address=config.source_token_address,
    )

    ethereum_before = None
    if config.ethereum_usdc_amount:
        token = fetch_erc20_details(target_web3s[1], USDC_NATIVE_TOKEN[1])
        ethereum_before = token.fetch_balance_of(wallet.address)
        swaps.append(prepare_ethereum_usdc_transfer(source_web3, target_web3s[1], wallet, config.ethereum_usdc_amount))

    if not swaps:
        print("\nAll chains have sufficient gas and no USDC transfer was requested. Nothing to do.")
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
    assert len(results) == len(swaps), "Not every requested transfer was submitted; inspect transaction hashes before retrying"
    verify_and_display_final(results, source_web3, target_web3s, wallet, config)
    if ethereum_before is not None:
        received = token.fetch_balance_of(wallet.address) - ethereum_before
        minimum = sum(token.convert_to_decimals(r.swap.quote.estimate_to_amount_min) for r in results if r.swap.target_chain_id == 1 and r.swap.quote.to_token.lower() == token.address.lower())
        assert received >= minimum, f"Ethereum USDC not credited as quoted: observed {received}, expected at least {minimum}; inspect original transfers before retrying"
        print(f"Observed Ethereum USDC increase: {received}")
