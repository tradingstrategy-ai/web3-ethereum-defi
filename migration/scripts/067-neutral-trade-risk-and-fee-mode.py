"""Refresh native Neutral Trade risk and fee mechanisms from reviewed accounting.

Purpose: Replace unassessed risk and fee mode in persisted native metadata.
Scope: Database-wide metadata transformation for Protocol == Neutral Trade,
Solana dataset chain 9992 and provider-neutral solana-vault identities only.
Branch: check-neutral-drade-api-docs
PR title: feat: collect Neutral Trade native vault history through the partner API
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1761
Preserves: All rates, stricter risk overrides, EVM/Accountable records, other
metadata, prices, source history, reader state and timestamp caches.
Expected: Native risk is at least High; fees are externalised, or feeless for
explicitly all-zero base schedules. No network reads or price backfill needed:
this changes interpretation of existing observations, not their history.

LLM-generated operational migration; changes require human review. Stop the
looped scanner before preview or apply. No API key is required for this refresh.
"""

from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID
from eth_defi.neutral_trade.vault_data_export import FEE_BASIS, resolve_native_fees
from eth_defi.vault.fee import FeeData
from eth_defi.vault.risk import get_vault_risk
from eth_defi.vault_protocols.migration import MigrationContext

# ruff: noqa: N999 - numbered operational migration


def migrate(context: MigrationContext) -> None:
    """Refresh cached classifications without changing stored fee rates or prices.

    Validate native fee records before staging any changes. The runner owns
    preview, backups, locks and persistence; stricter risk overrides survive.

    :param context: Guarded migration attempt and staged metadata database.
    :return: ``None`` after staging and reporting the metadata-only refresh.
    """
    database = context.metadata()
    targets = {spec: row for spec, row in database.rows.items() if spec.chain_id == SOLANA_DATASET_CHAIN_ID and spec.vault_address.startswith("solana-vault-") and row.get("Protocol") == "Neutral Trade"}
    for spec, row in targets.items():
        assert isinstance(row.get("_fees"), FeeData), f"Missing native fee data for {spec}"
    for spec, row in targets.items():
        row["_fees"] = resolve_native_fees(row["_fees"])
        risk = get_vault_risk("Neutral Trade", spec.vault_address)
        previous = row.get("_risk")
        # Resolve the default dynamically, retaining only stricter overrides.
        # Otherwise a later protocol risk increase could be masked by this cache.
        if previous is not None and risk is not None and previous.value <= risk.value:
            row["_risk"] = None
        if row.get("_neutral_trade") is not None:
            row["_neutral_trade"]["fee_basis"] = FEE_BASIS
    context.report(inspected_rows=len(targets))
    context.commit_metadata()
