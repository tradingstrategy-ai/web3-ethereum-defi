"""Heal broken vault metadata entries in the vault database.

When the vault scanner encounters a transient RPC error (HTTP 400/500,
timeout, etc.) it stores a placeholder record with ``<broken: ...>`` as
the name.  Because ``update_leads_and_rows()`` used to overwrite
unconditionally, a single bad rescan could destroy previously good
metadata for a vault.

This script:

1. Loads the vault-metadata-db.pickle (local or from R2).
2. Finds all explicitly broken entries (``<broken: ...>``) across every chain.
3. Re-reads each broken vault from the chain via JSON-RPC.
4. Replaces the broken record with the freshly read good data.
5. Writes the healed pickle back (and optionally uploads to R2).

By default only targets entries whose name starts with ``<broken``
(transient RPC failures).  Set ``HEAL_ALL=true`` to also attempt
healing entries with empty names — these are usually false-positive
Deposit event detections and unlikely to succeed.

Environment variables:

- ``MAX_WORKERS``: Thread pool size for parallel RPC reads (default: 8)
- ``DRY_RUN``: Set to ``true`` to only report broken vaults without healing (default: false)
- ``HEAL_ALL``: Set to ``true`` to also attempt empty-name entries (default: false)
- ``JSON_RPC_<CHAIN>``: RPC URL per chain (required for chains that have broken vaults)

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/heal-broken-vaults.py

"""

import logging
import os
from pathlib import Path

from joblib import Parallel, delayed
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.chain import CHAIN_NAMES, get_chain_name
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, _is_broken_row, get_pipeline_data_dir

logger = logging.getLogger(__name__)


def heal_single_vault(
    chain_id: int,
    spec: VaultSpec,
    broken_row: VaultRow,
) -> tuple[VaultSpec, VaultRow | None]:
    """Attempt to heal a single broken vault entry by re-reading from chain.

    :return:
        ``(spec, new_row)`` on success, ``(spec, None)`` on failure.
    """
    try:
        json_rpc_url = read_json_rpc_url(chain_id)
    except ValueError:
        logger.warning(
            "No RPC URL configured for chain %d (%s), skipping %s",
            chain_id,
            CHAIN_NAMES.get(chain_id, "unknown"),
            spec.vault_address,
        )
        return spec, None

    try:
        web3 = create_multi_provider_web3(json_rpc_url)
        block_number = web3.eth.block_number
        detection = broken_row["_detection_data"]
        token_cache = TokenDiskCache()

        new_row = create_vault_scan_record(
            web3,
            detection,
            block_number,
            token_cache=token_cache,
        )

        if _is_broken_row(new_row):
            logger.warning(
                "Vault %s on chain %d still broken after re-read: %s",
                spec.vault_address,
                chain_id,
                new_row.get("Name"),
            )
            return spec, None

        return spec, new_row

    except Exception as e:
        logger.warning(
            "Failed to heal vault %s on chain %d: %s",
            spec.vault_address,
            chain_id,
            e,
        )
        return spec, None


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/heal-broken-vaults.log"),
    )

    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    heal_all = os.environ.get("HEAL_ALL", "false").lower() == "true"

    vault_db_path = get_pipeline_data_dir() / "vault-metadata-db.pickle"
    assert vault_db_path.exists(), f"Vault database not found at {vault_db_path}"

    vault_db = VaultDatabase.read(vault_db_path)
    print(f"Loaded {len(vault_db.rows):,} vault metadata entries from {vault_db_path}")

    # Find broken entries
    broken: list[tuple[VaultSpec, VaultRow]] = []
    skipped_empty = 0
    for spec, row in vault_db.rows.items():
        if not _is_broken_row(row):
            continue
        name = row.get("Name") or ""
        if name.startswith("<broken"):
            # Explicit transient failure — always heal
            broken.append((spec, row))
        elif heal_all:
            # Empty-name entries (likely false positives) — only with HEAL_ALL
            broken.append((spec, row))
        else:
            skipped_empty += 1

    if skipped_empty:
        print(f"Skipped {skipped_empty:,} empty-name entries (likely false positives, use HEAL_ALL=true to include)")

    if not broken:
        print("No broken vault entries found.")
        return

    # Group by chain for reporting
    by_chain: dict[int, list[tuple[VaultSpec, VaultRow]]] = {}
    for spec, row in broken:
        by_chain.setdefault(spec.chain_id, []).append((spec, row))

    table_rows = []
    for chain_id in sorted(by_chain.keys()):
        chain_name = get_chain_name(chain_id) if chain_id in CHAIN_NAMES else str(chain_id)
        entries = by_chain[chain_id]
        for spec, row in entries:
            table_rows.append(
                [
                    chain_name,
                    spec.vault_address[:10] + "...",
                    row.get("Name", ""),
                    row.get("Protocol", ""),
                ]
            )

    print(f"\nFound {len(broken)} broken vault entries across {len(by_chain)} chains:\n")
    print(tabulate(table_rows, headers=["Chain", "Address", "Name", "Protocol"], tablefmt="simple"))

    if dry_run:
        print("\nDry run — no changes written.")
        return

    # Heal broken entries using thread pool
    print(f"\nHealing {len(broken)} broken vaults using {max_workers} workers...")

    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(heal_single_vault)(spec.chain_id, spec, row) for spec, row in tqdm(broken, desc="Healing broken vaults"))

    healed = 0
    failed = 0
    for spec, new_row in results:
        if new_row is not None:
            vault_db.rows[spec] = new_row
            healed += 1
            logger.info(
                "Healed %s on chain %d: %s (%s)",
                spec.vault_address,
                spec.chain_id,
                new_row.get("Name"),
                new_row.get("Protocol"),
            )
        else:
            failed += 1

    print(f"\nResults: {healed} healed, {failed} still broken out of {len(broken)} total")

    if healed > 0:
        vault_db.write(vault_db_path)
        print(f"Saved healed vault database to {vault_db_path}")
    else:
        print("No vaults were healed, database unchanged.")


if __name__ == "__main__":
    main()
