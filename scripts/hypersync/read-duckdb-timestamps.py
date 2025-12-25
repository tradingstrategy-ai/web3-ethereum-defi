"""Integrity check for DuckDB timestamps in Hypersync exports."""

from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.event_reader.timestamp_cache import load_timestamp_cache


def main():
    # Arbirum
    chain_id = 42161

    timestamp_db = load_timestamp_cache(
        chain_id=chain_id,
    )

    print(f"Database has blocks from {timestamp_db.get_first_and_last_block()[0]} to {timestamp_db.get_first_and_last_block()[1]}")
    print(f"Total entries: {timestamp_db.get_count():,}")

    # Get all blocks
    slicer = timestamp_db.query(1, 100)

    print(f"Loaded {len(slicer)} timestamps from DuckDB cache for chain {chain_id}")

    # Print current Python process memory usage


if __name__ == "__main__":
    main()
