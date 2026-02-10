#!/usr/bin/env python
"""Examine vault reader states and print broken contracts.

Usage:
    poetry run python scripts/erc-4626/check-reader-states.py

Environment variables:
    READER_STATE_PATH: Path to reader state pickle file (optional)
"""

import os
import pickle
from pathlib import Path

from tabulate import tabulate

from eth_defi.chain import CHAIN_NAMES


def main():
    reader_state_path = os.environ.get(
        "READER_STATE_PATH",
        str(Path.home() / ".tradingstrategy/vaults/reader-state.pickle")
    )

    if not Path(reader_state_path).exists():
        print(f"Reader state file not found: {reader_state_path}")
        return

    with open(reader_state_path, "rb") as f:
        reader_states = pickle.load(f)

    print(f"Loaded {len(reader_states)} reader states from {reader_state_path}\n")

    # Collect broken calls
    broken_calls = []
    total_calls_checked = 0

    for (chain_id, vault_address), state in reader_states.items():
        call_status = getattr(state, "call_status", {})
        for function_name, (check_block, reverts) in call_status.items():
            total_calls_checked += 1
            if reverts:
                chain_name = CHAIN_NAMES.get(chain_id, f"Chain {chain_id}")
                broken_calls.append({
                    "Chain": chain_name,
                    "Chain ID": chain_id,
                    "Vault": vault_address[:10] + "...",
                    "Full Address": vault_address,
                    "Function": function_name,
                    "Detected at Block": check_block,
                })

    print(f"Total calls checked across all vaults: {total_calls_checked}")

    if not broken_calls:
        print("\nNo broken calls detected.")
        return

    print(f"\nFound {len(broken_calls)} broken calls:\n")

    # Summary table
    headers = ["Chain", "Vault", "Function", "Detected at Block"]
    rows = [[c["Chain"], c["Vault"], c["Function"], f"{c['Detected at Block']:,}"] for c in broken_calls]
    print(tabulate(rows, headers=headers, tablefmt="grid"))

    # Group by chain
    print("\n\nBroken calls by chain:")
    by_chain = {}
    for call in broken_calls:
        chain = call["Chain"]
        if chain not in by_chain:
            by_chain[chain] = []
        by_chain[chain].append(call)

    for chain, calls in sorted(by_chain.items()):
        print(f"\n{chain}:")
        for call in calls:
            print(f"  {call['Full Address']}: {call['Function']}")

    # Summary stats
    print("\n\nSummary:")
    print(f"  Total vaults with reader states: {len(reader_states)}")
    print(f"  Total calls checked: {total_calls_checked}")
    print(f"  Total broken calls: {len(broken_calls)}")
    print(f"  Chains with broken calls: {len(by_chain)}")


if __name__ == "__main__":
    main()
