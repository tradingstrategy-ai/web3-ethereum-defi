"""Scan public Derive v3 native vaults into a local DuckDB file.

Environment variables:

- ``DERIVE_V3_NETWORK``: ``testnet`` (default) or ``mainnet``.
- ``DB_PATH``: Optional destination DuckDB file.
- ``VAULT_IDS``: Optional comma-separated vault subaccount IDs.
- ``LOG_LEVEL``: Console logging level (default ``info``).

Example::

    DERIVE_V3_NETWORK=testnet poetry run python scripts/derive/scan-v3-vaults.py
"""

import logging
import os
from pathlib import Path

from eth_defi.derive.v3_constants import DERIVE_V3_MAINNET_DATABASE, DERIVE_V3_TESTNET_DATABASE
from eth_defi.derive.v3_vault_metrics import scan_derive_v3_vaults
from eth_defi.derive.v3_vaults import DeriveV3VaultClient
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the public Derive v3 vault scan with environment configuration.

    :return: ``None``.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    network = os.environ.get("DERIVE_V3_NETWORK", "testnet")
    client = DeriveV3VaultClient(network=network)
    db_path = Path(os.environ.get("DB_PATH", str(DERIVE_V3_TESTNET_DATABASE if network == "testnet" else DERIVE_V3_MAINNET_DATABASE))).expanduser()
    ids = os.environ.get("VAULT_IDS", "").strip()
    vault_ids = {int(item.strip()) for item in ids.split(",") if item.strip()} if ids else None
    try:
        vault_count, price_count = scan_derive_v3_vaults(client, db_path, vault_ids)
    finally:
        client.close()
    logger.info("Stored %d vaults and %d performance points in %s", vault_count, price_count, db_path)


if __name__ == "__main__":
    main()
