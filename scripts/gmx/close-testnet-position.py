"""
Close a GMX position held by a Lagoon vault on Arbitrum Sepolia

Flattens an open GMX perpetuals position through the vault's guard, reading the
position size directly from the on-chain ``SyntheticsReader``.

Why this exists
---------------

``scripts/lagoon/lagoon-gmx-example.py`` deploys a fresh vault on every run, so a
run that fails partway leaves an orphaned position behind, owned by a vault the
next run knows nothing about. This script closes such a position without
redeploying anything.

It also works around a testnet limitation. GMX publishes no REST or GraphQL
position source for ``arbitrum_sepolia``, so the CCXT adapter's position lookup
falls back to on-chain reads and is unreliable there — it can report "no position"
while one is plainly open on chain, at which point a close silently becomes a
no-op. Reading the size straight from the ``SyntheticsReader`` and passing it as
an explicit USD close size sidesteps that entirely.

Usage
-----

The vault, Safe and guard module addresses are printed by
``lagoon-gmx-example.py`` under "STEP 1: Deploy Lagoon vault"::

    export ARBITRUM_SEPOLIA_RPC_URL="https://sepolia-rollup.arbitrum.io/rpc"
    export GMX_PRIVATE_KEY="0x..."

    python scripts/gmx/close-testnet-position.py \\
        --vault 0xD302c4f3f9702C8A13808feCd65b22E588Cd1eD6 \\
        --trading-module 0x14516401E61b16D9198A8Dadf1Faa7188B384Abc

Inspect without sending anything::

    python scripts/gmx/close-testnet-position.py --vault 0x... --trading-module 0x... --dry-run

Environment variables
---------------------

- ``ARBITRUM_SEPOLIA_RPC_URL`` (or ``JSON_RPC_ARBITRUM_SEPOLIA``): RPC endpoint.
- ``GMX_PRIVATE_KEY`` (or ``PRIVATE_KEY``): the vault's asset manager. Must be the
  key that is authorised on the ``TradingStrategyModuleV0``, not a Safe owner.

Notes
-----

- The close is a market order. It is submitted, not executed — a GMX keeper fills
  it a few seconds later. The script waits and confirms the position is gone.
- The asset manager pays the keeper execution fee in ETH (``forward_eth=True``),
  so the Safe itself does not need an ETH balance.
- Sepolia prices execution fees off a stale gas figure, so the buffer defaults
  high. Any excess is refunded.
"""

import argparse
import logging
import os
import sys
import time

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.contracts import get_contract_addresses, get_reader_contract
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from web3 import Web3

logger = logging.getLogger(__name__)

#: Arbitrum Sepolia.
CHAIN_ID = 421614

#: GMX prices the execution fee off a stale ~0.02 gwei on Sepolia while the
#: on-chain InsufficientExecutionFee check uses the real gas price, so
#: over-provision heavily. Excess is refunded.
DEFAULT_EXECUTION_BUFFER = 100.0

#: How long to wait for a keeper to fill the close, in seconds.
KEEPER_TIMEOUT = 180


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    :return: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Close a GMX position held by a Lagoon vault on Arbitrum Sepolia.",
    )
    parser.add_argument("--vault", required=True, help="Lagoon vault address")
    parser.add_argument("--trading-module", required=True, help="TradingStrategyModuleV0 (guard) address enabled on the Safe")
    parser.add_argument("--safe", default=None, help="Safe address. Resolved from the vault when omitted.")
    parser.add_argument("--symbol", default="ETH/USDC:USDC", help="CCXT market symbol (default: %(default)s)")
    parser.add_argument("--collateral", default="USDC.SG", help="Collateral token symbol (default: %(default)s)")
    parser.add_argument(
        "--execution-buffer",
        type=float,
        default=DEFAULT_EXECUTION_BUFFER,
        help="Execution fee multiplier (default: %(default)s)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the position and exit without sending a transaction")
    return parser.parse_args()


def resolve_rpc_url() -> str:
    """Read the Arbitrum Sepolia RPC endpoint from the environment.

    :return: RPC URL.
    :raises SystemExit: If no endpoint is configured.
    """
    url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL") or os.environ.get("JSON_RPC_ARBITRUM_SEPOLIA")
    if not url:
        sys.exit("Set ARBITRUM_SEPOLIA_RPC_URL or JSON_RPC_ARBITRUM_SEPOLIA")
    return url


def resolve_private_key() -> str:
    """Read the asset manager private key from the environment.

    :return: Private key, ``0x``-prefixed.
    :raises SystemExit: If no key is configured.
    """
    key = os.environ.get("GMX_PRIVATE_KEY") or os.environ.get("PRIVATE_KEY")
    if not key:
        sys.exit("Set GMX_PRIVATE_KEY or PRIVATE_KEY")
    return key if key.startswith("0x") else "0x" + key


def fetch_position(web3: Web3, safe_address: str) -> tuple[float, bool, float] | None:
    """Read the Safe's first open GMX position from the on-chain reader.

    Deliberately bypasses the CCXT adapter's position lookup, which is unreliable
    on Arbitrum Sepolia — GMX serves no REST or GraphQL position data there.

    :param web3: Web3 connection to Arbitrum Sepolia.
    :param safe_address: Safe that owns the position.
    :return: ``(size_usd, is_long, collateral_amount)``, or ``None`` when flat.
    """
    addresses = get_contract_addresses("arbitrum_sepolia")
    reader = get_reader_contract(web3, "arbitrum_sepolia")
    positions = reader.functions.getAccountPositions(
        Web3.to_checksum_address(addresses.datastore),
        Web3.to_checksum_address(safe_address),
        0,
        10,
    ).call()

    if not positions:
        return None

    numbers, flags = positions[0][1], positions[0][2]
    return numbers[0] / 10**30, flags[0], numbers[2] / 10**6


def wait_until_flat(web3: Web3, safe_address: str, timeout: int = KEEPER_TIMEOUT) -> bool:
    """Poll until the account has no open positions.

    :param web3: Web3 connection to Arbitrum Sepolia.
    :param safe_address: Safe that owns the position.
    :param timeout: Seconds to wait for a keeper.
    :return: ``True`` if the position closed within the timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fetch_position(web3, safe_address) is None:
            return True
        time.sleep(10)
    return False


def main() -> int:
    """Close the vault's open GMX position.

    :return: Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    rpc_url = resolve_rpc_url()
    web3 = create_multi_provider_web3(rpc_url)
    if web3.eth.chain_id != CHAIN_ID:
        sys.exit(f"Expected Arbitrum Sepolia (chain {CHAIN_ID}), connected to {web3.eth.chain_id}")

    vault = LagoonVault(web3, VaultSpec(CHAIN_ID, args.vault))
    # LagoonVault does not resolve the guard module from the Safe, so supply it.
    vault.trading_strategy_module_address = Web3.to_checksum_address(args.trading_module)
    safe_address = Web3.to_checksum_address(args.safe or vault.safe_address)

    print(f"vault: {args.vault}")
    print(f"safe : {safe_address}")

    position = fetch_position(web3, safe_address)
    if position is None:
        print("No open positions — nothing to close.")
        return 0

    size_usd, is_long, collateral = position
    print(f"position: {'LONG' if is_long else 'SHORT'} sizeUsd=${size_usd:,.2f} collateral={collateral:,.4f} {args.collateral}")

    if args.dry_run:
        print("--dry-run: not sending anything.")
        return 0

    asset_manager = HotWallet.from_private_key(resolve_private_key())

    wallet = LagoonGMXTradingWallet(
        vault=vault,
        asset_manager=asset_manager,
        forward_eth=True,
    )
    # Sync exactly once. LagoonGMXTradingWallet.sync_nonce() delegates to the
    # asset manager, so syncing both re-reads the transaction count twice — and
    # against a load-balanced provider the second read can come from a node a
    # block behind, resetting the counter backwards into "nonce too low".
    # See the warning on HotWallet.sync_nonce().
    wallet.sync_nonce(web3)

    gmx = GMX(
        params={
            "rpcUrl": rpc_url,
            "wallet": wallet,
            "executionBuffer": args.execution_buffer,
            "defaultSlippage": 0.005,
        }
    )
    gmx.load_markets()

    market = gmx.markets[args.symbol]["info"]["gmx_market_address"]
    print(f"market: {args.symbol} -> {market}")

    order = gmx.create_order(
        symbol=args.symbol,
        type="market",
        # Selling closes a long, buying closes a short.
        side="sell" if is_long else "buy",
        # Sized in USD, not tokens. The USD figure is the position's own
        # entry-priced sizeInUsd straight from the reader, which is the basis
        # GMX compares against — see the BASIS CONTRACT note in
        # eth_defi.gmx.ccxt.exchange.create_order().
        amount=0,
        params={
            "size_usd": float(size_usd),
            "leverage": 1.0,
            "collateral_symbol": args.collateral,
            "reduceOnly": True,
            "wait_for_execution": False,
        },
    )
    print(f"close order submitted: id={order.get('id')} status={order.get('status')}")

    print("Waiting for a GMX keeper to execute...")
    if wait_until_flat(web3, safe_address):
        print("Position closed.")
        return 0

    print(f"Position still open after {KEEPER_TIMEOUT}s. The keeper may still fill it — re-run with --dry-run to check.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
