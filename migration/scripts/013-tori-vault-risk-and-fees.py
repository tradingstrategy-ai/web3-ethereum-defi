# ruff: noqa: N999
"""Refresh fees for the reviewed Tori risk and fee coverage.

Purpose: Refresh cached fees; current risk and product disclosures resolve during export.
Scope: The 17 exact chain/address pairs in tori.disclosures.TORI_EXPOSURES.
Branch: cover-tori-vault-risk-and-fees
PR title: feat: cover risk and fees across Tori vault exposures
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1698
Preserves: Protocol/curator identities, cached risk, flags, notes, descriptions, unrelated rows, prices, discovery boundaries, reader state and timestamp caches.
Expected: 17 inspected rows when all tracked targets are present; missing rows reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. JSON_RPC_ETHEREUM and
JSON_RPC_MONAD supply current-state fee reads. No historical backfill.
"""

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.tori_risk_and_fees import refresh_tori_risk_and_fees


def migrate(context: MigrationContext) -> None:
    """Stage validated fee updates through the guarded runner.

    Preview and apply use the same maintained fee resolvers. The runner owns
    backups, persistence, and lifecycle-before-writer locking.

    :param context: Guarded migration attempt and staged database.
    :return: ``None`` after reporting and commit or preview staging.
    """
    context.report(**refresh_tori_risk_and_fees(context.metadata()))
    context.commit_metadata()
