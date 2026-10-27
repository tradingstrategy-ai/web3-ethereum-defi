#!/usr/bin/env python3
"""Register the historical Asseto IP Vault lead and refresh its metadata.

Stop the persistent scanner before applying to its database. This script
backs up that pickle and preserves unrelated records. It does not open reader
state, timestamp caches or price Parquet files, and never runs price scans.

Example::

    source .local-test.env && DRY_RUN=true poetry run python scripts/erc-4626/migrate-asseto-royalty-vault.py

Use ``DRY_RUN=false`` to apply after reviewing the proposed row. Optional
``VAULT_DB_PATH`` selects a metadata pickle; ``JSON_RPC_SONEIUM`` supplies
the RPC connection. Normal discovery also injects this historical lead.
"""

import logging
import os
import shutil
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.asseto.royalty_constants import ROYALTY_CHAIN_ID
from eth_defi.tokenised_fund.asseto.royalty_migration import migrate_royalty_metadata
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the backed-up metadata migration using environment settings.

    Dry runs are the default and do not write persistent state. Applying only
    changes the IP Vault row; the caller must stop competing scanner writers.

    :return: None after reporting the proposed or applied metadata.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    setting = os.environ.get("DRY_RUN", "true").lower()
    if setting not in {"true", "false"}:
        message = "DRY_RUN must be true or false"
        raise ValueError(message)
    dry_run = setting == "true"
    path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    database = VaultDatabase.read(path)
    web3 = create_multi_provider_web3(read_json_rpc_url(ROYALTY_CHAIN_ID))
    row = migrate_royalty_metadata(database, web3, dry_run=dry_run)
    print(tabulate([(row["Address"], row["Protocol"], row["Symbol"], row["NAV"], "Still under verification")], headers=("Address", "Protocol", "Shares", "Assets", "Status")))
    if not dry_run:
        backup = path.with_name(f"{path.name}.asseto-royalty-{native_datetime_utc_now():%Y%m%dT%H%M%S%f}.bak")
        shutil.copy2(path, backup)
        database.write(path)
        logger.info("Saved IP Vault metadata; backup: %s", backup)
    else:
        logger.info("Dry run complete; no metadata, reader state or prices written")


if __name__ == "__main__":
    main()
