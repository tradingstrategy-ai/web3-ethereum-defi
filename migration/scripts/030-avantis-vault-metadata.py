"""Classify the existing unified Avantis avUSDC vault on Base.

Purpose: Publish protocol identity, fee, admission, capability and strategy metadata.
Scope: Base 0x944766f715b51967e56afde5f0aa76ceacc9e7f9 only; one required existing row.
Branch: identify-avantis-usdc-vault-protocol
PR title: feat: add Avantis unified avUSDC vault support
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1725
Preserves: Unrelated metadata, manual notes, all leads, price history, reader state and timestamp caches.
Expected: One inspected row; changed fields reported; retries are idempotent.

No price backfill is needed: the vault already has historical prices. No reader
reset, historical scan, export or production publication occurs in this script.
LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. Normal Base RPC configuration is required.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.avantis import refresh_avantis_metadata


def migrate(context: MigrationContext) -> None:
    """Stage the one-vault repair using the runner's locks and backup policy.

    :param context: Shared staged metadata and migration reporting.
    :return: None after preview or commit.
    """
    context.report(**refresh_avantis_metadata(context.metadata()))
    context.commit_metadata()
