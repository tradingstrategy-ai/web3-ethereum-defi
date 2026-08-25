"""Migrate GMX V2 GM and GLV metadata into the common vault database.

Existing rows are identified by chain and share-token address, so each run also
refreshes display metadata such as the concise GM/GLV trading-pair name and
the direct GMX pool-details link.

Environment variables:

- ``CHAINS``: Comma-separated ``arbitrum`` and/or ``avalanche``. Defaults to both.
- ``VAULT_DATABASE``: Metadata pickle path. Defaults under ``PIPELINE_DATA_DIR``.
- ``DRY_RUN``: Set to ``true`` to enumerate without writing.
- ``MAX_WORKERS``: Metadata worker count. Defaults to 8.
- ``LOG_LEVEL``: Console log level. Defaults to ``info``.
"""

import os
from contextlib import nullcontext
from pathlib import Path

from tabulate import tabulate

from eth_defi.gmx.vault_catalog import GMX_CHAIN_NAMES_BY_ID
from eth_defi.gmx.vault_sync import fetch_and_sync_gmx_vault_catalogue
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

CHAIN_IDS_BY_NAME = {name: chain_id for chain_id, name in GMX_CHAIN_NAMES_BY_ID.items()}


def main() -> None:
    """Migrate configured GMX deployments and atomically update metadata."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    chain_names = tuple(name.strip().lower() for name in os.environ.get("CHAINS", "arbitrum,avalanche").split(",") if name.strip())
    unknown = set(chain_names) - set(CHAIN_IDS_BY_NAME)
    if unknown:
        raise ValueError(f"Unsupported GMX chains: {sorted(unknown)}")

    database_path = Path(os.environ.get("VAULT_DATABASE", get_pipeline_data_dir() / "vault-metadata-db.pickle")).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    token_cache = TokenDiskCache()
    rows = []
    lock = nullcontext() if dry_run else wait_other_writers(get_pipeline_data_dir() / "scan-pipeline", timeout=60)
    with lock:
        vault_db = VaultDatabase.read(database_path) if database_path.exists() else VaultDatabase()
        for chain_name in chain_names:
            chain_id = CHAIN_IDS_BY_NAME[chain_name]
            web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
            result = fetch_and_sync_gmx_vault_catalogue(web3=web3, vault_db=vault_db, token_cache=token_cache, max_workers=max_workers)
            rows.append((chain_name, chain_id, result.products, result.inserted, result.updated))

        if not dry_run:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            vault_db.write(database_path)
            token_cache.commit()

    print(tabulate(rows, headers=("Chain", "Chain ID", "Products", "Inserted", "Updated"), tablefmt="rounded_outline"))
    print(f"{'Dry run; not written' if dry_run else 'Written'}: {database_path}")


if __name__ == "__main__":
    main()
