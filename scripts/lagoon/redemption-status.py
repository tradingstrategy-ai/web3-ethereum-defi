"""Display Lagoon vault deposit and redemption status.

Shows a quick snapshot of:

- Authoritative total assets (`totalAssets()`)
- Denomination token holdings split by location (Safe, Silo, Vault)
- System-wide deposit and redemption queues (Silo balances)

Usage:

.. code-block:: shell

    source .local-test.env
    export JSON_RPC_URL=$JSON_RPC_BASE
    export VAULT_ADDRESS=0x7d8Fab3E65e6C81ea2a940c050A7c70195d1504f
    poetry run python scripts/lagoon/redemption-status.py

"""

import logging
import os
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress
from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class HoldingRow:
    """Denomination token holdings in one location."""

    location: str
    address: HexAddress
    amount: Decimal


def _format_amount(amount: Decimal, decimals: int = 2) -> str:
    return f"{amount:,.{decimals}f}"


def main() -> None:
    setup_console_logging()

    json_rpc_url = os.environ["JSON_RPC_URL"]
    vault_address = os.environ.get("VAULT_ADDRESS", "0x7d8Fab3E65e6C81ea2a940c050A7c70195d1504f")

    web3 = create_multi_provider_web3(json_rpc_url)
    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id)
    block_number = web3.eth.block_number

    print(f"Connected to {chain_name} (chain ID: {chain_id})")
    print(f"Current block: {block_number:,}")

    spec = VaultSpec(chain_id=chain_id, vault_address=vault_address)
    vault = LagoonVault(web3, spec)

    denomination_token = vault.denomination_token

    print(f"Vault: {vault.name}")
    print(f"Vault address: {vault_address}")
    print(f"Vault version: {vault.version.value}")
    print(f"Denomination token: {denomination_token.symbol}")

    total_assets_raw = vault.vault_contract.functions.totalAssets().call(block_identifier=block_number)
    total_assets = denomination_token.convert_to_decimals(total_assets_raw)

    print(f"\nAuthoritative total assets (totalAssets()): {_format_amount(total_assets)} {denomination_token.symbol}")

    safe_address = vault.safe_address
    silo_address = vault.silo_address
    vault_contract_address = vault.vault_contract.address

    holding_rows = [
        HoldingRow("Safe", safe_address, denomination_token.fetch_balance_of(safe_address, block_number)),
        HoldingRow("Silo (pending deposits)", silo_address, denomination_token.fetch_balance_of(silo_address, block_number)),
        HoldingRow("Vault (pending redemptions)", vault_contract_address, denomination_token.fetch_balance_of(vault_contract_address, block_number)),
    ]

    holdings_sum = sum((row.amount for row in holding_rows), start=Decimal(0))

    holdings_table = tabulate(
        [
            {
                "Location": row.location,
                "Address": row.address,
                f"{denomination_token.symbol} balance": _format_amount(row.amount),
            }
            for row in holding_rows
        ],
        headers="keys",
        tablefmt="simple",
    )

    print("\nDenomination token holdings breakdown")
    print(holdings_table)
    print(f"\nSum of listed holdings: {_format_amount(holdings_sum)} {denomination_token.symbol}")

    flow_manager = vault.get_flow_manager()
    deposit_queue_assets = flow_manager.fetch_pending_deposit(block_number)
    redemption_queue_shares = flow_manager.fetch_pending_redemption(block_number)
    share_price = vault.fetch_share_price(block_number)
    redemption_queue_assets = flow_manager.calculate_underlying_needed_for_redemptions(block_number)

    queues_table = tabulate(
        [
            {
                "Metric": "Deposit queue",
                "Units": denomination_token.symbol,
                "Amount": _format_amount(deposit_queue_assets),
            },
            {
                "Metric": "Redemption queue",
                "Units": f"{vault.share_token.symbol} (shares)",
                "Amount": _format_amount(redemption_queue_shares),
            },
            {
                "Metric": "Redemption queue (underlying needed)",
                "Units": denomination_token.symbol,
                "Amount": _format_amount(redemption_queue_assets),
            },
            {
                "Metric": "Share price",
                "Units": f"{denomination_token.symbol}/{vault.share_token.symbol}",
                "Amount": _format_amount(share_price, decimals=8),
            },
        ],
        headers="keys",
        tablefmt="simple",
    )

    print("\nQueues (system-wide)")
    print(queues_table)


if __name__ == "__main__":
    main()
