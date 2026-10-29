# ruff: noqa: N999
"""Identify the existing native Tori Finance strUSD staking vault.

Purpose: Replace generic catalogue metadata with the maintained Tori reader.
Scope: Ethereum 0x280839980a7ed0d7717f64125fe241012e5f5815 only.
Branch: cover-tori-staked-trusd-vault
PR title: feat: cover Tori native strUSD staking vault
PR: Not opened; local implementation only.
Preserves: Unrelated row fields, discovery boundaries, prices, reader state and timestamp caches.
Expected: One row refreshed; absent target reported without creation. No historical backfill.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. JSON_RPC_ETHEREUM supplies live
withdrawal timing. No provider/token caches or historical data are written.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.tori import refresh_tori_metadata


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed metadata refresh through the guarded runner.

    Preview and apply share validation and live reads. The context owns
    persistence, backups and the ordered lifecycle and writer locks.

    :param context: Guarded migration context and shared staged database.
    :return: ``None`` after reporting and commit or preview staging.
    """
    context.report(**refresh_tori_metadata(context.metadata()))
    context.commit_metadata()
