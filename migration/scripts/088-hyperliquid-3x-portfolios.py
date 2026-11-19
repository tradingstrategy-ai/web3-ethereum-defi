"""Refresh persisted 3x portfolio tags for two Hyperliquid vaults.

Purpose: Publish the 3x portfolio classification.
Scope: Only _strategy_tags for the two listed Hypercore (9999) deployments.
Branch: check-hyperliquid-long-short-vault-equity-curves
PR title: feat: classify more Hyperliquid long-short portfolios
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794
Preserves: Other metadata, unrelated rows, prices, leads, reader state and timestamp caches.
Expected: Up to two updated fields. Report absent deployments without creating rows.
Earlier pending tag migrations may already have refreshed these rows against the current registry.
The tag refresh requires no network calls or historical price backfill.

LLM-generated operational migration; changes require human review.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import stage_hyperliquid_strategy_tags

#: Reviewed deployments, frozen independently of future tag registry changes.
TARGETS = (
    VaultSpec(HYPERCORE_CHAIN_ID, "0x060d01aa996003b3731a992462d7f0ba68bf3b04"),  # Edge & Hedge
    VaultSpec(HYPERCORE_CHAIN_ID, "0x07fd993f0fa3a185f7207adccd29f7a87404689d"),  # [ Systemic Strategies ] L/S Grids
)


def migrate(context: MigrationContext) -> None:
    """Refresh the portfolio tags for the listed Hypercore vaults.

    The runner controls preview and persistence of the staged metadata.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after previewing or committing the refresh.
    """
    stage_hyperliquid_strategy_tags(context, TARGETS)
    context.commit_metadata()
