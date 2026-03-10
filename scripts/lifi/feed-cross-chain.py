"""Feed gas to hot wallets across multiple chains using LI.FI.

Checks native token gas balances on target chains and bridges
gas from a source chain when any target is running low.

Currency conversion
-------------------

All thresholds (``MIN_GAS_USD``, ``TOP_UP_GAS_USD``) are specified in USD.
The conversion between native tokens and USD works as follows:

1. For each chain, the native token USD price is fetched from the
   LI.FI token API (``GET /v1/token?chain={chain_id}&token=0x0000...0000``).
   This returns the current ``priceUSD`` for the chain's native token
   (e.g. ETH, MATIC, BNB).

2. The on-chain native balance (in wei) is read via ``web3.eth.get_balance()``
   and converted to a human-readable amount by dividing by 10^18.

3. The USD value of the balance is: ``native_balance * price_usd``.

4. When a top-up is needed the source amount depends on ``SOURCE_TOKEN``:

   - **native** (default): ``top_up_usd / source_native_price_usd``,
     converted to wei (18 decimals).
   - **usdc**: ``top_up_usd / usdc_price_usd``, converted to raw units
     using :py:meth:`~eth_defi.token.TokenDetails.convert_to_raw`
     (typically 6 decimals). The USDC address for the source chain is
     looked up from :py:data:`~eth_defi.token.USDC_NATIVE_TOKEN`.

5. This raw amount is passed to the LI.FI ``/v1/quote`` endpoint which
   returns a ready-to-sign transaction that bridges from the source token
   to native token on the target chain.

Environment variables:

- ``PRIVATE_KEY`` - Hot wallet private key (0x-prefixed hex)
- ``SOURCE_CHAIN`` - Source chain name, using internal names from :py:data:`eth_defi.chain.CHAIN_NAMES`
  (e.g. "ethereum", "arbitrum", "base", "polygon"). Case-insensitive.
  Resolved to a chain ID via :py:func:`eth_defi.chain.get_chain_id_by_name`,
  then passed as a numeric chain ID to the LI.FI API.
- ``TARGET_CHAINS`` - Comma-separated target chain names, same naming as ``SOURCE_CHAIN``
  (e.g. "base,polygon,arbitrum")
- ``MIN_GAS_USD`` - Minimum gas balance in USD (default: 5)
- ``TOP_UP_GAS_USD`` - Amount to bridge when topping up in USD (default: 20)
- ``DRY_RUN`` - Set to "true" to only fetch quotes and display balances without executing (default: false)
- ``SOURCE_TOKEN`` - Source token to bridge: "native" (default) or "usdc".
  When set to "usdc", the USDC address for the source chain is looked up
  from :py:data:`eth_defi.token.USDC_NATIVE_TOKEN`. LI.FI handles the
  swap from USDC to the target chain's native gas token.
- ``LIFI_API_KEY`` - Optional LI.FI API key for higher rate limits
- ``JSON_RPC_*`` - RPC URLs for each chain (e.g. ``JSON_RPC_ETHEREUM``, ``JSON_RPC_BASE``)
- ``LOG_LEVEL`` - Logging level (default: info)

Usage:

.. code-block:: shell

    export PRIVATE_KEY=<...>
    export DRY_RUN=true
    export SOURCE_CHAIN=arbitrum
    export TARGET_CHAINS="base, ethereum, monad, hyperliquid, avalanche"
    export MIN_GAS_USD=5
    export TOP_UP_GAS_USD=10
    python scripts/lifi/feed-cross-chain.py

"""

import logging
import os
from decimal import Decimal

from tabulate import tabulate

from eth_defi.chain import get_chain_id_by_name, get_chain_name
from eth_defi.hotwallet import HotWallet
from eth_defi.lifi.constants import DEFAULT_MIN_GAS_USD, DEFAULT_TOP_UP_GAS_USD, LIFI_NATIVE_TOKEN_ADDRESS
from eth_defi.lifi.crosschain import execute_crosschain_swaps, fetch_crosschain_gas_balances, prepare_crosschain_swaps
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=log_level)

    # Read configuration from environment
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

    # Source token: "native" (default) or "usdc"
    source_token_choice = os.environ.get("SOURCE_TOKEN", "native").lower().strip()

    # Resolve chain names to chain IDs.
    # We use our internal chain names from eth_defi.chain.CHAIN_NAMES (e.g. "ethereum", "arbitrum", "base").
    # These are resolved to numeric chain IDs via get_chain_id_by_name() and then passed
    # to the LI.FI API which accepts numeric chain IDs in its fromChain/toChain parameters.
    # The RPC URL for each chain is read from JSON_RPC_{CHAIN_NAME} environment variables
    # via eth_defi.provider.env.read_json_rpc_url().
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

    logger.info("Source chain: %s (ID: %s)", source_chain_name, source_chain_id)
    logger.info("Target chains: %s", ", ".join(f"{name} ({cid})" for name, cid in zip(target_chain_names, target_chain_ids)))
    logger.info("Min gas: $%s, Top-up: $%s", min_gas_usd, top_up_usd)

    # Create Web3 connections
    source_rpc_url = read_json_rpc_url(source_chain_id)
    source_web3 = create_multi_provider_web3(source_rpc_url)

    target_web3s = {}
    for chain_id in target_chain_ids:
        rpc_url = read_json_rpc_url(chain_id)
        target_web3s[chain_id] = create_multi_provider_web3(rpc_url)

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

    # Create wallet
    wallet = HotWallet.from_private_key(private_key)
    logger.info("Wallet address: %s", wallet.address)

    # Check current balances (source chain first, then targets)
    all_web3s = {source_chain_id: source_web3, **target_web3s}
    balances_native, balances_usd = fetch_crosschain_gas_balances(
        target_web3s=all_web3s,
        wallet_address=wallet.address,
    )

    # Fetch USDC balance on the source chain if available
    source_usdc_address = USDC_NATIVE_TOKEN.get(source_chain_id)
    source_usdc_balance = Decimal("0")
    if source_usdc_address:
        usdc_details = fetch_erc20_details(source_web3, source_usdc_address)
        source_usdc_balance = usdc_details.fetch_balance_of(wallet.address)

    balance_table = []

    # Source chain first — show both native and USDC balances
    source_native = balances_native.get(source_chain_id, Decimal("0"))
    source_usd = balances_usd.get(source_chain_id, Decimal("0"))
    usdc_col = f"${source_usdc_balance:.2f}" if source_usdc_address else "-"
    balance_table.append([source_chain_name, source_chain_id, f"{source_native:.6f}", f"${source_usd:.2f}", usdc_col, "-", "SOURCE"])

    # Target chains
    for chain_id in target_chain_ids:
        chain_name = get_chain_name(chain_id)
        native = balances_native.get(chain_id, Decimal("0"))
        usd = balances_usd.get(chain_id, Decimal("0"))
        status = "OK" if usd >= min_gas_usd else "LOW"
        balance_table.append([chain_name, chain_id, f"{native:.6f}", f"${usd:.2f}", "-", f"${min_gas_usd:.2f}", status])

    print("\nCurrent gas balances:")
    print(
        tabulate(
            balance_table,
            headers=["Chain", "ID", "Native balance", "USD value", "USDC balance", "Min required", "Status"],
            tablefmt="simple",
        )
    )

    # Prepare swaps
    print(f"\nPreparing cross-chain swaps (source token: {source_token_choice})...")
    swaps = prepare_crosschain_swaps(
        wallet=wallet,
        source_web3=source_web3,
        target_web3s=target_web3s,
        min_gas_usd=min_gas_usd,
        top_up_usd=top_up_usd,
        source_token_address=source_token_address,
    )

    if not swaps:
        print("\nAll chains have sufficient gas. Nothing to do.")
        return

    # Display proposed swaps
    swap_table = []
    for swap in swaps:
        source_name = get_chain_name(swap.source_chain_id)
        target_name = get_chain_name(swap.target_chain_id)
        swap_table.append(
            [
                f"{source_name} -> {target_name}",
                f"${swap.target_balance_usd:.2f}",
                f"${swap.from_amount_usd:.2f}",
                f"{swap.from_amount_raw} raw",
                f"~{swap.quote.execution_duration}s" if swap.quote.execution_duration else "N/A",
            ]
        )

    print(f"\nProposed swaps ({len(swaps)}):")
    print(
        tabulate(
            swap_table,
            headers=["Route", "Current balance", "Bridge amount", "From amount", "Est. duration"],
            tablefmt="simple",
        )
    )

    if dry_run:
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

    # Display results
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


if __name__ == "__main__":
    main()
