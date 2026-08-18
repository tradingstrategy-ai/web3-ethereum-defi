"""Recalculate persisted strategy tags for every vault metadata row.

The migration uses the current address resolvers backing
:class:`~eth_defi.vault.base.VaultBase` strategy hooks for EVM vaults and the
native resolver used by each perpetual DEX exporter for Hyperliquid, GRVT,
Hibachi, and Lighter. No adapters are constructed, no RPC calls are made, and
no price, parquet, or reader-state files are touched.

The scanner and this migration both replace the complete metadata pickle when
writing. Keep the scanner stopped while applying the migration so a concurrent
scan cannot overwrite either set of changes; dry runs are read-only.

Usage:

.. code-block:: shell

    # Inspect changes without modifying the metadata pickle (the default)
    source .local-test.env && poetry run python scripts/erc-4626/migrate-vault-strategy-tags.py

    # Stop vault-scanner-looped before applying the migration.
    # Persist the current adapter/native classifications
    source .local-test.env && DRY_RUN=false \
        poetry run python scripts/erc-4626/migrate-vault-strategy-tags.py

Environment variables:

- ``VAULT_DB_PATH``: Optional path to ``vault-metadata-db.pickle``.
- ``DRY_RUN``: Set to ``false`` to write changes. Defaults to ``true``.
- ``LOG_LEVEL``: Optional console log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from eth_typing import HexAddress
from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.aave.tags import get_strategy_tags as get_aave_strategy_tags
from eth_defi.erc_4626.vault_protocol.atoma.tags import STRATEGY_TAGS as ATOMA_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.centrifuge.tags import STRATEGY_TAGS as CENTRIFUGE_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.ethena.tags import STRATEGY_TAGS as ETHENA_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.euler.tags import get_strategy_tags as get_euler_strategy_tags
from eth_defi.erc_4626.vault_protocol.gains.tags import STRATEGY_TAGS as GAINS_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.ipor.tags import STRATEGY_TAGS as IPOR_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.morpho.tags import get_strategy_tags as get_morpho_strategy_tags
from eth_defi.erc_4626.vault_protocol.panoptic.tags import STRATEGY_TAGS as PANOPTIC_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.symbiotic.tags import STRATEGY_TAGS as SYMBIOTIC_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.upshift.tags import STRATEGY_TAGS as UPSHIFT_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.yieldnest.tags import STRATEGY_TAGS as YIELDNEST_STRATEGY_TAGS
from eth_defi.grvt.tags import get_strategy_tags as get_grvt_strategy_tags
from eth_defi.hibachi.tags import get_strategy_tags as get_hibachi_strategy_tags
from eth_defi.hyperliquid.tags import get_strategy_tags as get_hyperliquid_strategy_tags
from eth_defi.lighter.tags import get_strategy_tags as get_lighter_strategy_tags
from eth_defi.tokenised_fund.securitize.tags import STRATEGY_TAGS as SECURITIZE_STRATEGY_TAGS
from eth_defi.tokenised_fund.spiko.tags import STRATEGY_TAGS as SPIKO_STRATEGY_TAGS
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

StrategyTagResolver = Callable[[HexAddress], set[StrategyTag] | None]
NativeStrategyTagResolver = Callable[[str], set[StrategyTag]]

# These protocols do not use VaultBase adapters. Their native exporters own
# the default perpetual-futures tag and address-specific classifications.
NATIVE_STRATEGY_TAG_RESOLVERS: dict[ERC4626Feature, NativeStrategyTagResolver] = {
    ERC4626Feature.grvt_native: get_grvt_strategy_tags,
    ERC4626Feature.hibachi_native: get_hibachi_strategy_tags,
    ERC4626Feature.hypercore_native: get_hyperliquid_strategy_tags,
    ERC4626Feature.lighter_native: get_lighter_strategy_tags,
}

# VaultBase adapters expose the same hook, but some constructors perform
# onchain probes. Resolve their maintained address mappings directly from the
# persisted feature flag instead of constructing an adapter in this metadata
# migration. Keep this tuple in the same order as the corresponding branches
# in :func:`eth_defi.erc_4626.classification.create_vault_instance`.
EVM_STRATEGY_TAG_RESOLVERS: tuple[tuple[ERC4626Feature, StrategyTagResolver], ...] = (
    (ERC4626Feature.symbiotic_like, partial(lookup_strategy_tags, SYMBIOTIC_STRATEGY_TAGS)),
    (ERC4626Feature.securitize_like, partial(lookup_strategy_tags, SECURITIZE_STRATEGY_TAGS)),
    (ERC4626Feature.ipor_like, partial(lookup_strategy_tags, IPOR_STRATEGY_TAGS)),
    (ERC4626Feature.spiko_like, partial(lookup_strategy_tags, SPIKO_STRATEGY_TAGS)),
    (ERC4626Feature.morpho_like, get_morpho_strategy_tags),
    (ERC4626Feature.morpho_v2_like, get_morpho_strategy_tags),
    (ERC4626Feature.panoptic_like, partial(lookup_strategy_tags, PANOPTIC_STRATEGY_TAGS)),
    (ERC4626Feature.euler_earn_like, get_euler_strategy_tags),
    (ERC4626Feature.euler_like, get_euler_strategy_tags),
    (ERC4626Feature.domination_finance_like, partial(lookup_strategy_tags, GAINS_STRATEGY_TAGS)),
    (ERC4626Feature.gains_like, partial(lookup_strategy_tags, GAINS_STRATEGY_TAGS)),
    (ERC4626Feature.ostium_like, partial(lookup_strategy_tags, GAINS_STRATEGY_TAGS)),
    (ERC4626Feature.atoma_like, partial(lookup_strategy_tags, ATOMA_STRATEGY_TAGS)),
    (ERC4626Feature.aave_like, get_aave_strategy_tags),
    (ERC4626Feature.upshift_like, partial(lookup_strategy_tags, UPSHIFT_STRATEGY_TAGS)),
    (ERC4626Feature.centrifuge_like, partial(lookup_strategy_tags, CENTRIFUGE_STRATEGY_TAGS)),
    (ERC4626Feature.ethena_like, partial(lookup_strategy_tags, ETHENA_STRATEGY_TAGS)),
    (ERC4626Feature.yieldnest_like, partial(lookup_strategy_tags, YIELDNEST_STRATEGY_TAGS)),
)


@dataclass(slots=True, frozen=True)
class StrategyTagUpdate:
    """Describe one changed persisted strategy-tag set."""

    #: Vault identity in the metadata pickle.
    spec: VaultSpec

    #: Protocol display name stored in the row.
    protocol: str

    #: Tags before the migration, or ``None`` when missing.
    old_tags: frozenset[StrategyTag] | None

    #: Tags returned by the current adapter or native resolver.
    new_tags: frozenset[StrategyTag] | None

    #: Resolver source used to calculate the new value.
    source: str


@dataclass(slots=True, frozen=True)
class StrategyTagMigrationResult:
    """Summarise a strategy-tag migration run."""

    #: Number of metadata rows examined.
    inspected_rows: int

    #: Number of rows that were or would be changed.
    updated_rows: int

    #: Rows that could not be resolved because required persisted metadata was missing or invalid.
    skipped_rows: int

    #: Rows containing legacy or malformed persisted tag values.
    invalid_tag_rows: int

    #: Detailed changed rows in database iteration order.
    updates: tuple[StrategyTagUpdate, ...]

    #: Backup made before an apply run, if any.
    backup_path: Path | None


def parse_boolean_env(value: str | None, *, default: bool) -> bool:
    """Parse a strict boolean environment value.

    :param value:
        Environment value, or ``None`` when unset.
    :param default:
        Value used when the variable is unset.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If a configured value is not recognised.
    """

    if value is None:
        return default
    normalised = value.strip().casefold()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean environment value, got {value!r}")


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose an unused sibling path for a metadata-pickle backup.

    :param vault_db_path:
        Existing metadata pickle to protect before writing.
    :return:
        Non-existing sibling backup path.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-strategy-tags")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def _normalise_persisted_tags(value: object) -> tuple[set[StrategyTag] | None, bool]:
    """Convert a legacy persisted tag collection to enum members.

    :param value:
        Value read from ``VaultRow['_strategy_tags']``.
    :return:
        A normalised mutable set (or ``None`` for missing strategy information)
        and a flag indicating whether every persisted value was recognised.
    """

    if value is None:
        return None, True
    if not isinstance(value, (set, frozenset, list, tuple)):
        return None, False

    tags: set[StrategyTag] = set()
    valid = True
    for tag in value:
        if isinstance(tag, StrategyTag):
            tags.add(tag)
            continue
        try:
            tags.add(StrategyTag(str(tag)))
        except (TypeError, ValueError):
            valid = False
    return tags, valid


def resolve_strategy_tags(spec: VaultSpec, row: VaultRow) -> tuple[set[StrategyTag] | None, str] | None:
    """Resolve current strategy tags from one persisted vault row.

    :param spec:
        Vault identity from the database mapping.
    :param row:
        Persisted metadata row.
    :return:
        ``(tags, source)`` when the row can be resolved, or ``None`` when the
        row lacks usable detection metadata or a maintained resolver.
    """

    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection):
        return None

    protocol_name = get_vault_protocol_name(detection.features)
    native_feature = next((feature for feature in NATIVE_STRATEGY_TAG_RESOLVERS if feature in detection.features), None)
    native_resolver = NATIVE_STRATEGY_TAG_RESOLVERS.get(native_feature) if native_feature is not None else None
    if native_resolver is not None:
        return native_resolver(spec.vault_address), f"{protocol_name} native resolver"

    evm_match = next(((feature, resolver) for feature, resolver in EVM_STRATEGY_TAG_RESOLVERS if feature in detection.features), None)
    if evm_match is None:
        # Do not clear a manually persisted classification merely because this
        # migration has not yet learned about the adapter.
        return None

    _evm_feature, evm_resolver = evm_match
    return evm_resolver(spec.vault_address), f"{protocol_name} tag resolver"


def collect_strategy_tag_updates(vault_db: VaultDatabase) -> tuple[tuple[StrategyTagUpdate, ...], int, int]:
    """Find rows whose persisted tags differ from current adapter classifications.

    :param vault_db:
        Loaded vault metadata database.
    :return:
        Changed rows, unresolved rows, and rows containing legacy tag values.
    """

    updates: list[StrategyTagUpdate] = []
    skipped_rows = 0
    invalid_tag_rows = 0
    for spec, row in vault_db.rows.items():
        try:
            current_tags, persisted_tags_valid = _normalise_persisted_tags(row.get("_strategy_tags"))
            if not persisted_tags_valid:
                invalid_tag_rows += 1
                logger.warning("Found legacy strategy tags for %s", spec.as_string_id())
            resolved = resolve_strategy_tags(spec, row)
        except (AttributeError, ImportError, KeyError, TypeError, ValueError, RuntimeError) as error:
            logger.warning("Could not resolve strategy tags for %s: %s", spec.as_string_id(), error)
            skipped_rows += 1
            continue

        if resolved is None:
            logger.debug("Skipping %s: strategy-tag resolver is unavailable", spec.as_string_id())
            skipped_rows += 1
            continue

        new_tags, source = resolved
        normalised_new_tags = None if new_tags is None else set(new_tags)
        if persisted_tags_valid and current_tags == normalised_new_tags:
            continue

        updates.append(
            StrategyTagUpdate(
                spec=spec,
                protocol=row.get("Protocol", ""),
                old_tags=None if current_tags is None else frozenset(current_tags),
                new_tags=None if normalised_new_tags is None else frozenset(normalised_new_tags),
                source=source,
            )
        )

    return tuple(updates), skipped_rows, invalid_tag_rows


def migrate_vault_strategy_tags(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
) -> StrategyTagMigrationResult:
    """Recalculate strategy tags across the persisted vault metadata database.

    The migration is idempotent. It updates only ``_strategy_tags`` and makes
    a non-overwriting sibling backup before the first apply write.

    :param vault_db_path:
        Existing vault metadata pickle to inspect and optionally update.
    :param dry_run:
        Report changes without modifying the pickle when ``True``.
    :return:
        Counts, changed rows, and backup information.
    """

    vault_db = VaultDatabase.read(vault_db_path)
    updates, skipped_rows, invalid_tag_rows = collect_strategy_tag_updates(vault_db)
    backup_path: Path | None = None

    if not dry_run and updates:
        backup_path = create_backup_path(vault_db_path)
        logger.info("Creating vault metadata backup at %s", backup_path)
        shutil.copy2(vault_db_path, backup_path)
        for update in updates:
            vault_db.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
        vault_db.write(vault_db_path)
        logger.info("Updated %d strategy-tag rows in %s", len(updates), vault_db_path)

    return StrategyTagMigrationResult(
        inspected_rows=len(vault_db.rows),
        updated_rows=len(updates),
        skipped_rows=skipped_rows,
        invalid_tag_rows=invalid_tag_rows,
        updates=updates,
        backup_path=backup_path,
    )


def _format_tags(tags: frozenset[StrategyTag] | None) -> str:
    """Format tags for the operator-facing migration table."""

    if tags is None:
        return "<missing>"
    return ", ".join(sorted(tag.value for tag in tags)) or "<none>"


def main() -> None:
    """Run the strategy-tag migration from environment configuration."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    dry_run = parse_boolean_env(os.environ.get("DRY_RUN"), default=True)
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault database not found: {vault_db_path}")

    result = migrate_vault_strategy_tags(vault_db_path, dry_run=dry_run)
    if result.updates:
        print(
            tabulate(
                [
                    [
                        update.spec.chain_id,
                        update.spec.vault_address,
                        update.protocol,
                        _format_tags(update.old_tags),
                        _format_tags(update.new_tags),
                        update.source,
                    ]
                    for update in result.updates
                ],
                headers=["chain", "address", "protocol", "old tags", "new tags", "source"],
                tablefmt="simple",
            )
        )

    print(f"Inspected {result.inspected_rows:,} vault rows, updated {result.updated_rows:,}, skipped {result.skipped_rows:,} unresolved rows, encountered {result.invalid_tag_rows:,} legacy-tag rows.")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.backup_path:
        print(f"Backup: {result.backup_path}")


if __name__ == "__main__":
    main()
