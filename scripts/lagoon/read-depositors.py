"""Read all depositors for a Lagoon vault using Hypersync.

Usage:

.. code-block:: shell

    source .local-test.env
    export JSON_RPC_URL=$JSON_RPC_BASE
    export VAULT_ADDRESS=0x7d8Fab3E65e6C81ea2a940c050A7c70195d1504f
    python scripts/lagoon/read-depositors.py

"""

import asyncio
import datetime
import logging
import os
from dataclasses import dataclass
from decimal import Decimal

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.chain import get_chain_name
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.vault.base import VaultSpec
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.utils import setup_console_logging

import hypersync
from hypersync import BlockField, LogField

logger = logging.getLogger(__name__)

# ERC-4626 Deposit event topic
DEPOSIT_TOPIC = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"


@dataclass
class DepositEvent:
    """A single deposit event."""

    timestamp: datetime.datetime
    address: str
    amount: Decimal


async def scan_depositors(
    client: hypersync.HypersyncClient,
    vault_address: str,
    start_block: int,
    end_block: int,
    decimals: int,
) -> list[DepositEvent]:
    """Scan for all Deposit events for a specific vault using Hypersync.

    :return:
        List of DepositEvent with date, address, and amount
    """

    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block,
        logs=[
            hypersync.LogSelection(
                address=[vault_address],
                topics=[[DEPOSIT_TOPIC]],
            )
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.ADDRESS,
                LogField.TRANSACTION_HASH,
                LogField.TOPIC0,
                LogField.TOPIC1,  # sender
                LogField.TOPIC2,  # owner
                LogField.DATA,  # assets, shares
            ],
        ),
    )

    receiver = await client.stream(query, hypersync.StreamConfig())

    deposits: list[DepositEvent] = []

    while True:
        res = await asyncio.wait_for(receiver.recv(), timeout=90.0)
        if res is None:
            break

        if res.data.logs:
            # Build block timestamp lookup
            block_timestamps = {b.number: int(b.timestamp, 16) for b in res.data.blocks}

            for log in res.data.logs:
                # Owner is indexed topic2 in Deposit event
                owner = log.topics[2] if len(log.topics) > 2 else None
                if not owner:
                    continue

                # Convert to checksummed address
                owner_addr = "0x" + owner[-40:]

                # Parse assets from data field (first 32 bytes)
                # Deposit event: Deposit(address indexed sender, address indexed owner, uint256 assets, uint256 shares)
                if log.data and len(log.data) >= 66:  # 0x + 64 hex chars
                    raw_assets = int(log.data[2:66], 16)
                    amount = Decimal(raw_assets) / Decimal(10**decimals)
                else:
                    amount = Decimal(0)

                # Get timestamp
                block_num = log.block_number
                timestamp_unix = block_timestamps.get(block_num, 0)
                timestamp = native_datetime_utc_fromtimestamp(timestamp_unix)

                deposits.append(
                    DepositEvent(
                        timestamp=timestamp,
                        address=owner_addr,
                        amount=amount,
                    )
                )

    return deposits


def main():
    setup_console_logging()

    # Use generic JSON_RPC_URL like scan-vaults.py
    json_rpc_url = os.environ["JSON_RPC_URL"]
    vault_address = os.environ.get("VAULT_ADDRESS", "0x7d8Fab3E65e6C81ea2a940c050A7c70195d1504f")
    hypersync_api_key = os.environ["HYPERSYNC_API_KEY"]

    web3 = create_multi_provider_web3(json_rpc_url)
    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id)

    print(f"Connected to {chain_name} (chain ID: {chain_id})")
    print(f"Current block: {web3.eth.block_number:,}")

    # Create vault instance to get metadata
    spec = VaultSpec(chain_id=chain_id, vault_address=vault_address)
    vault = LagoonVault(web3, spec)

    print(f"Vault: {vault.name}")
    print(f"Vault address: {vault_address}")
    print(f"Vault version: {vault.version.value}")
    print(f"Denomination token: {vault.denomination_token.symbol}")

    decimals = vault.denomination_token.decimals

    # Configure Hypersync - auto-detect server based on chain ID
    hypersync_url = get_hypersync_server(web3)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url, bearer_token=hypersync_api_key))

    # Scan from block 0 to latest
    start_block = 0
    end_block = web3.eth.block_number

    print(f"\nScanning Deposit events from block {start_block:,} to {end_block:,}...")

    deposits = asyncio.run(scan_depositors(client, vault_address, start_block, end_block, decimals))

    print(f"\nFound {len(deposits)} deposit events\n")

    # Display results as table with Date, Address, Deposit amount
    data = [
        {
            "Date": d.timestamp.strftime("%Y-%m-%d %H:%M"),
            "Address": d.address,
            "Deposit amount": f"{d.amount:,.2f}",
        }
        for d in sorted(deposits, key=lambda x: x.timestamp)
    ]

    table = tabulate(data, headers="keys", tablefmt="simple")
    print(table)

    # Print total deposits
    total_deposits = sum(d.amount for d in deposits)
    print(f"\nTotal deposits: {total_deposits:,.2f} {vault.denomination_token.symbol}")


if __name__ == "__main__":
    main()
