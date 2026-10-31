"""Correct persisted Upshift account-admission classifications.

Purpose: Replace the former blanket permissionless classification with each deployment's account-admission policy, including permissioned USPC Custody Vault.
Scope: Every existing metadata row with Upshift detection/features; only _deposit_permission changes. No address or chain scope overrides.
Branch: check-upshift-deposit-adapter
PR title: feat: support guarded Upshift vault deposits and withdrawals
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1705
Preserves: Capability, notes, flags, unrelated metadata, prices, leads, reader state and timestamp caches.
Expected: The 2026-10-30 production snapshot has 126 Upshift rows, including 12 allow-listed deployments. Counts follow live policy; pending 017 may already correct USPC.

This metadata-only repair adds no discoveries or early price gaps, so no price
backfill is needed. Reads finish before staging changes; unresolved policies
fail the migration. Repeating the transformation is safe and idempotent.
LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific production input. Existing JSON_RPC_*
configuration supplies current state. Run through the guarded migration runner
with scanner writers stopped; normal scanner publication follows separately.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.upshift import refresh_upshift_deposit_permissions


def migrate(context: MigrationContext) -> None:
    """Stage protocol-wide admission repair through the existing runner.

    The context owns preview, backups, persistence, journalling and locks.
    Source-proven policy is resolved by the maintained adapter and scanner.

    :param context: Guarded migration context and shared staged database.
    :return: ``None`` after reporting and commit or preview staging.
    """
    context.report(**refresh_upshift_deposit_permissions(context.metadata()))
    context.commit_metadata()
