"""Fill missing or label-only Arcadia descriptions, retaining existing narratives.

Purpose: Fill missing or label-only Arcadia descriptions, retaining existing narratives.
Scope: Every existing row classified as Arcadia Finance; optional per-chain API lookup with local fallback.
Branch: check-arcadia-vault-offchain-metadata
PR title: feat: add Arcadia vault descriptions and offchain metadata
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1685
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from tqdm_loggable.auto import tqdm

from eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata import fetch_arcadia_vaults
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.arcadia import migrate_arcadia_vault_metadata


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    database = context.metadata()
    chains = sorted({spec.chain_id for spec, row in database.rows.items() if row.get("Protocol") == "Arcadia Finance"})
    with TemporaryDirectory(prefix="migration-arcadia-") as directory:
        indices = {chain: fetch_arcadia_vaults(chain, cache_path=Path(directory)) for chain in tqdm(chains, desc="Fetching Arcadia metadata")}
    migrate_arcadia_vault_metadata(database, indices, dry_run=False)
    context.report(chains=chains)
    context.commit_metadata()
