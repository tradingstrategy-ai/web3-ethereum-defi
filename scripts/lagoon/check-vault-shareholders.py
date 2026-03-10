"""Check who holds shares for a Lagoon vault.

Scans the vault's share token Transfer events to find all current holders
and their balances.

Example:

.. code-block:: shell

    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    python scripts/lagoon/check-vault-shareholders.py --vault 0x05Ec266b7b85F8a28A271041c9b40a15941Bf81F

Environment variables
---------------------

``JSON_RPC_ARBITRUM``
    Arbitrum mainnet RPC endpoint.
"""

import argparse
import logging
import os
import sys
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.vault import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging


logger = logging.getLogger(__name__)

#: Zero address for detecting mints/burns
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def find_shareholders(
    web3: Web3,
    vault: LagoonVault,
    from_block: int | None = None,
    verbose: bool = False,
) -> dict[HexAddress, Decimal]:
    """Find all share token holders by scanning Transfer events.

    :param web3: Web3 instance.
    :param vault: LagoonVault instance.
    :param from_block: Block to start scanning from. Defaults to recent 500k blocks.
    :param verbose: Print each Transfer event as it is found.
    :return: Dictionary of address -> share balance (only non-zero).
    """
    share_token = vault.share_token
    share_contract = share_token.contract

    if from_block is None:
        # Scan last ~500k blocks (~2 weeks on Arbitrum)
        current_block = web3.eth.block_number
        from_block = max(0, current_block - 500_000)

    print(f"\nScanning Transfer events for {share_token.symbol} ({share_token.address})...")
    print(f"  From block: {from_block:,}")

    # Use get_logs with chunked block ranges to avoid timeouts
    chunk_size = 50_000
    current_block = web3.eth.block_number
    addresses: set[HexAddress] = set()
    total_events = 0

    block = from_block
    while block <= current_block:
        end_block = min(block + chunk_size - 1, current_block)
        events = share_contract.events.Transfer.get_logs(
            from_block=block,
            to_block=end_block,
        )
        total_events += len(events)

        for event in events:
            from_addr = event["args"]["from"]
            to_addr = event["args"]["to"]
            value = event["args"]["value"]
            block_num = event["blockNumber"]
            if from_addr != ZERO_ADDRESS:
                addresses.add(Web3.to_checksum_address(from_addr))
            if to_addr != ZERO_ADDRESS:
                addresses.add(Web3.to_checksum_address(to_addr))
            if verbose:
                print(f"    Transfer: {from_addr[:10]}... -> {to_addr[:10]}... | {value} | block {block_num}")

        block = end_block + 1

    print(f"  Found {total_events} Transfer events")

    # Also check the vault contract itself (ERC-7540 holds shares during settlement)
    addresses.add(Web3.to_checksum_address(vault.address))

    # Query current balances
    holders: dict[HexAddress, Decimal] = {}
    for addr in sorted(addresses):
        balance = share_token.fetch_balance_of(addr)
        if balance > 0:
            holders[addr] = balance

    return holders


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Check who holds shares for a Lagoon vault.",
    )
    parser.add_argument(
        "--vault",
        type=str,
        required=True,
        help="Lagoon vault contract address.",
    )
    parser.add_argument(
        "--from-block",
        type=int,
        default=None,
        help="Block to start scanning from. Default: last 500k blocks.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each Transfer event as it is found.",
    )
    parser.add_argument(
        "--rpc-env",
        type=str,
        default="JSON_RPC_ARBITRUM",
        help="RPC environment variable name. Default: JSON_RPC_ARBITRUM.",
    )
    return parser.parse_args()


def main():
    """Check vault shareholders."""
    setup_console_logging()
    args = parse_args()

    json_rpc_url = os.environ.get(args.rpc_env)
    if not json_rpc_url:
        print(f"Error: {args.rpc_env} environment variable required.", file=sys.stderr)
        sys.exit(1)

    web3 = create_multi_provider_web3(json_rpc_url)
    print(f"Connected to chain {web3.eth.chain_id}, block {web3.eth.block_number:,}")

    vault_address = Web3.to_checksum_address(args.vault)

    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier="latest",
        require_denomination_token=True,
    )
    assert isinstance(vault, LagoonVault), f"Address {vault_address} is not a Lagoon vault"

    usdc = vault.underlying_token
    share_token = vault.share_token

    print(f"\nVault:        {vault.address}")
    print(f"Safe:         {vault.safe_address}")
    print(f"Module:       {vault.trading_strategy_module_address}")
    print(f"Underlying:   {usdc.symbol} ({usdc.address})")
    print(f"Share token:  {share_token.symbol} ({share_token.address})")

    safe_usdc = usdc.fetch_balance_of(vault.safe_address)
    print(f"\nSafe {usdc.symbol} balance: {safe_usdc}")

    # Find shareholders
    holders = find_shareholders(web3, vault, from_block=args.from_block, verbose=args.verbose)

    if not holders:
        print("\nNo shareholders found. All shares have been redeemed or never minted.")
    else:
        print(f"\nShareholders ({len(holders)}):")
        print(f"  {'Address':<44} {'Balance':<20} {'Note'}")
        print("  " + "-" * 75)
        for addr, balance in sorted(holders.items(), key=lambda x: x[1], reverse=True):
            note = ""
            if addr == vault.safe_address:
                note = "(Safe)"
            elif addr == vault.address:
                note = "(Vault contract — pending settlement)"
            print(f"  {addr:<44} {str(balance):<20} {note}")

    # Also check pending redemptions/deposits on the vault contract
    vault_shares = share_token.fetch_balance_of(vault.address)
    if vault_shares > 0:
        print(f"\nNote: Vault contract holds {vault_shares} {share_token.symbol}")
        print("  These are pending settlement (unclaimed deposits or redemptions).")

    # Check silo
    try:
        silo_address = vault.silo_address
        if silo_address:
            silo_shares = share_token.fetch_balance_of(silo_address)
            silo_usdc = usdc.fetch_balance_of(silo_address)
            print(f"\nSilo ({silo_address}):")
            print(f"  Shares: {silo_shares} {share_token.symbol}")
            print(f"  USDC:   {silo_usdc} {usdc.symbol}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
