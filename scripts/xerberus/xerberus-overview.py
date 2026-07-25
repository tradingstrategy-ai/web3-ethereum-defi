"""Inspect a local Xerberus DuckDB database.

Usage:

.. code-block:: shell

    poetry run python scripts/xerberus/xerberus-overview.py
"""

import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.xerberus.constants import resolve_xerberus_database_path
from eth_defi.xerberus.database import XerberusDatabase


def main() -> None:
    """Print entity counts and sample scored pools/protocols."""
    db_path = resolve_xerberus_database_path()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    db = XerberusDatabase(db_path, read_only=True)
    try:
        counts = db.get_entity_counts()
        print(f"Database: {db_path}")
        print(tabulate([counts], headers="keys", tablefmt="fancy_grid"))

        pools = db.get_latest_registry_entities(entity_type="pool")
        if len(pools) > 0:
            scored = pools.dropna(subset=["score"]).sort_values("score", ascending=False).head(15)
            if len(scored) > 0:
                cols = [c for c in ("name", "chain_id", "address", "score", "entity_id") if c in scored.columns]
                print("\nTop scored pools:")
                print(tabulate(scored[cols].to_dict("records"), headers="keys", tablefmt="fancy_grid"))

        protocols = db.get_latest_registry_entities(entity_type="protocol")
        if len(protocols) > 0:
            scored = protocols.dropna(subset=["score"]).sort_values("score", ascending=False).head(15)
            if len(scored) > 0:
                cols = [c for c in ("name", "score", "entity_id") if c in scored.columns]
                print("\nTop scored protocols:")
                print(tabulate(scored[cols].to_dict("records"), headers="keys", tablefmt="fancy_grid"))
    finally:
        db.close()


if __name__ == "__main__":
    main()
