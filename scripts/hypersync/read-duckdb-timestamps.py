"""Integrity check for DuckDB timestamps in Hypersync exports.

"""

from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.event_reader.timestamp_cache import load_timestamp_cache

def main():
    # Arbirum
    chain_id =  42161

    timestamp_db = load_timestamp_cache(
        chain_id=chain_id,
    )

    # Get all blocks
    data = timestamp_db.query(1, 999_999_999)

    print(f"Loaded {len(data)} timestamps from DuckDB cache for chain {chain_id}")

    # Print current Python process memory usage





if __name__ == "__main__":
    main()