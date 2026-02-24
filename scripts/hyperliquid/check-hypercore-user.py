"""Check a HyperCore user's balances and vault positions.

Queries the Hyperliquid info API for spot balances, perpetual account state,
and vault equity positions.

Environment variables
---------------------
- ``ADDRESS``: HyperCore user address to query (required).
- ``NETWORK``: ``mainnet`` (default) or ``testnet``.
- ``LOG_LEVEL``: Logging level (default: ``info``).

Usage::

    ADDRESS=0xfBF2cc6708DC303484b3b8008F1DEcC6d934787a poetry run python scripts/hyperliquid/check-hypercore-user.py

    # Testnet
    NETWORK=testnet ADDRESS=0xAbc... poetry run python scripts/hyperliquid/check-hypercore-user.py
"""

import logging
import os

from tabulate import tabulate

from eth_defi.hyperliquid.api import (
    fetch_perp_clearinghouse_state,
    fetch_spot_clearinghouse_state,
    fetch_user_vault_equities,
)
from eth_defi.hyperliquid.session import (
    HYPERLIQUID_API_URL,
    HYPERLIQUID_TESTNET_API_URL,
    create_hyperliquid_session,
)
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(default_log_level=log_level)

    address = os.environ.get("ADDRESS")
    assert address, "ADDRESS environment variable required"

    network = os.environ.get("NETWORK", "mainnet").lower()
    assert network in ("mainnet", "testnet"), f"NETWORK must be 'mainnet' or 'testnet', got '{network}'"

    api_url = HYPERLIQUID_TESTNET_API_URL if network == "testnet" else HYPERLIQUID_API_URL

    print(f"HyperCore user: {address}")
    print(f"Network: {network}")
    print(f"API: {api_url}")

    session = create_hyperliquid_session(api_url=api_url)

    # Spot balances
    spot = fetch_spot_clearinghouse_state(session, user=address)
    if spot.balances:
        rows = [[b.coin, f"{b.total:,.6f}", f"{b.hold:,.6f}"] for b in spot.balances]
        print("\nSpot balances:")
        print(tabulate(rows, headers=["Token", "Total", "Hold"], tablefmt="simple"))
    else:
        print("\nSpot balances: none")

    if spot.evm_escrows:
        rows = [[e.coin, f"{e.total:,.6f}"] for e in spot.evm_escrows]
        print("\nEVM escrows (bridged, pending HyperCore processing):")
        print(tabulate(rows, headers=["Token", "Amount"], tablefmt="simple"))

    # Perpetual account
    perp = fetch_perp_clearinghouse_state(session, user=address)
    ms = perp.margin_summary
    perp_rows = [
        ["Account value", f"{ms.account_value:,.2f} USDC"],
        ["Total notional position", f"{ms.total_ntl_pos:,.2f} USDC"],
        ["Raw USD balance", f"{ms.total_raw_usd:,.2f} USDC"],
        ["Margin used", f"{ms.total_margin_used:,.2f} USDC"],
        ["Withdrawable", f"{perp.withdrawable:,.2f} USDC"],
    ]
    print("\nPerp account:")
    print(tabulate(perp_rows, tablefmt="simple"))

    if perp.asset_positions:
        rows = [
            [
                p.coin,
                f"{p.size:,.4f}",
                f"{p.entry_price:,.2f}" if p.entry_price else "-",
                f"{p.position_value:,.2f}",
                f"{p.unrealised_pnl:,.2f}",
                f"{p.liquidation_price:,.2f}" if p.liquidation_price else "-",
            ]
            for p in perp.asset_positions
        ]
        print("\nPerp positions:")
        print(tabulate(rows, headers=["Coin", "Size", "Entry", "Value", "PnL", "Liq price"], tablefmt="simple"))

    # Vault positions
    equities = fetch_user_vault_equities(session, user=address)
    if equities:
        rows = [[eq.vault_address, f"{eq.equity:,.6f}", eq.locked_until.isoformat()] for eq in equities]
        print("\nVault positions:")
        print(tabulate(rows, headers=["Vault", "Equity (USDC)", "Locked until (UTC)"], tablefmt="simple"))
    else:
        print("\nVault positions: none")


if __name__ == "__main__":
    main()
