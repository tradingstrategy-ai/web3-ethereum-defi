# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Publish source-reviewed direct LP fees for the unified Veranta vault.

Purpose: Publish reviewed direct LP fees, a fixed 0.5% withdrawal reference and the dynamic actual-fee disclosure.
Scope: Only fee fields and _description for Base 8453:0x944766f715b51967e56afde5f0aa76ceacc9e7f9.
Branch: fix/veranta-fee-data
PR title: fix: publish reviewed Veranta vault fees
PR: Not yet opened; record the PR URL on this branch before merge.
Preserves: Protocol, curator, short description, notes, flags, risk, capability, unrelated metadata, leads, price history, reader state and timestamp caches.
Expected: At most one existing row, six fee fields and its long description; missing target reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. JSON_RPC_BASE supplies pinned
identity and fee reads; no historical price backfill or reader reset.
See docs/source/vaults/veranta/index.rst for the production run reminder.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.veranta import refresh_veranta_fees


def migrate(context: MigrationContext) -> None:
    """Stage verified fee changes through the existing guarded runner.

    Source identity and all live reads must succeed before the row is changed.
    The context owns preview staging, backups, locking and atomic persistence.

    :param context: Guarded migration attempt and shared staged database.
    :return: ``None`` after reporting and committing or previewing fee changes.
    """
    context.report(**refresh_veranta_fees(context.metadata()))
    context.commit_metadata()
