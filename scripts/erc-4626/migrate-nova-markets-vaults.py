#!/usr/bin/env python3
"""Reclassify Nova's existing Arbitrum pool and preserve its entire history.

Fixed scope: chain 42161, pool 0x9ca80193712f372e2ff33a00127292884f6f8598.
The lead and first-seen boundaries are preserved: the deployment already has
historical prices and is changing protocol identity, not observation history.
There is deliberately no price backfill, discovery scan or reader-state reset.

``DRY_RUN`` defaults to ``true``. ``VAULT_DB_PATH`` overrides the pipeline
metadata pickle for isolated verification. See the Nova protocol README for
production commands. No RPC credentials are required for this repair.
"""

import datetime
import logging
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_MARKETS_CHAIN_ID, NOVA_POOL_ADDRESS
from eth_defi.erc_4626.vault_protocol.nova_markets.vault import NovaMarketsVault
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class NovaMigrationResult:
    """Fields changed by the reviewed single-deployment repair."""

    #: Metadata keys proposed or actually updated.
    changed_fields: tuple[str, ...]
    #: Sibling backup written before the original was atomically replaced.
    backup_path: Path | None


def _backup_metadata(path: Path) -> Path:
    """Create a new byte-for-byte backup without overwriting previous backups.

    Exclusive creation retains the original even when an operator repeats a
    partially completed repair.

    :param path: Existing metadata pickle.
    :return: New sibling backup path.
    """
    index = 0
    while True:
        suffix = f".{index}" if index else ""
        candidate = Path(f"{path}.bak-nova-markets{suffix}")
        try:
            backup = candidate.open("xb")
        except FileExistsError:
            index += 1
            continue
        with backup, path.open("rb") as source:
            shutil.copyfileobj(source, backup)
        return candidate


def migrate_nova_markets_vaults(path: Path, *, dry_run: bool = True) -> NovaMigrationResult:
    """Replace stale gTrade identity on the existing Nova row without RPC reads.

    Only the exact chain/address is touched. Detection event counts and
    first-seen information are retained via dataclass replacement; unrelated
    metadata, leads, scan boundaries and historical files are preserved.

    :param path: Existing trusted metadata pickle, or an isolated test copy.
    :param dry_run: Compare without modifying files when ``True``.
    :return: Changed fields and an optional byte-for-byte backup.
    :raises ValueError: If the existing row or detection has conflicting identity.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    lock = nullcontext() if dry_run else wait_other_writers(path.parent / "scan-pipeline", timeout=60)
    with lock:
        database = VaultDatabase.read(path)
        spec = VaultSpec(NOVA_MARKETS_CHAIN_ID, NOVA_POOL_ADDRESS)
        if spec not in database.rows:
            message = "Nova's reviewed existing row is missing; no synthetic row or backfill will be created"
            raise ValueError(message)
        row = database.rows[spec]
        if row.get("Protocol") not in {"Gains Network", "gTrade", "Nova Markets"}:
            raise ValueError(f"Unexpected Nova row protocol: {row.get('Protocol')!r}")
        if row.get("Address", NOVA_POOL_ADDRESS).lower() != NOVA_POOL_ADDRESS:
            message = "Nova row address conflicts with the reviewed deployment"
            raise ValueError(message)
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or detection.get_spec() != spec:
            message = "Nova's existing detection record is missing or has conflicting identity"
            raise ValueError(message)
        if not detection.features or detection.features - {ERC4626Feature.gains_like, ERC4626Feature.nova_markets_like}:
            message = "Nova's existing detection has unexpected protocol features"
            raise ValueError(message)
        features = {ERC4626Feature.nova_markets_like}
        adapter = NovaMarketsVault(web3=None, spec=spec, features=features)
        # Current documented three-day epoch, checked against requestsStart /
        # requestsEvery / requestsCount onchain on 2026-10-28. Future scans read
        # the live configuration through NovaMarketsVault.fetch_epoch_duration.
        withdrawal_period = WithdrawalPeriod(datetime.timedelta(days=3), datetime.timedelta(days=9), WithdrawalDelayType.delay)
        desired = {
            "Protocol": "Nova Markets",
            "protocol_slug": "nova-markets",
            "Name": adapter.name,
            "Features": "nova_markets_like",
            "features": features,
            "_detection_data": replace(detection, features=features),
            "Link": adapter.get_link(),
            "_notes": adapter.get_notes(),
            "_short_description": adapter.short_description,
            "_description": adapter.description,
            "_strategy_tags": adapter.get_strategy_tags(),
            "_curator_slug": "nova-markets",
            "_lockup": withdrawal_period.max_period,
            "_withdrawal_period": withdrawal_period,
            "_deposit_manager": adapter.get_deposit_manager_capability().as_initial_public_schema(),
        }
        changed = tuple(key for key, value in desired.items() if key not in row or row[key] != value)
        logger.info("Nova %s: dry run=%s, changed fields=%s", spec.as_string_id(), dry_run, ", ".join(changed) or "none")
        backup_path = None
        if changed and not dry_run:
            backup_path = _backup_metadata(path)
            row.update(desired)
            database.write(path)
            logger.info("Atomically updated Nova metadata; original backup: %s", backup_path)
        return NovaMigrationResult(changed, backup_path)


def main() -> None:
    """Run the exact-address repair using environment configuration.

    Defaults to a non-mutating comparison and reports the full changed field set.

    :return: ``None`` after a successful comparison or atomic refresh.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    raw_dry_run = os.environ.get("DRY_RUN", "true").strip().lower()
    if raw_dry_run not in {"true", "false"}:
        message = "DRY_RUN must be true or false"
        raise ValueError(message)
    path = Path(os.environ.get("VAULT_DB_PATH", str(get_pipeline_data_dir() / "vault-metadata-db.pickle"))).expanduser()
    migrate_nova_markets_vaults(path, dry_run=raw_dry_run == "true")


if __name__ == "__main__":
    main()
