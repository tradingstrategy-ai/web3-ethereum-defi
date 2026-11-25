"""Refresh persisted unlevered portfolio tags for five Hyperliquid vaults.

Purpose: Publish the unlevered portfolio classification.
Scope: Only _strategy_tags for the five listed Hypercore (9999) deployments.
Branch: check-hyperliquid-long-short-vault-equity-curves
PR title: feat: classify more Hyperliquid long-short portfolios
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794
Preserves: Other metadata, unrelated rows, prices, leads, reader state and timestamp caches.
Expected: Up to five updated fields. Report absent deployments without creating rows.
When 083 and 084 run first with the current registry, all five can already be current.
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
    VaultSpec(HYPERCORE_CHAIN_ID, "0x115849ce84370f25cadcf0d348510d73837e1aa5"),  # Orbit Value Strategies
    VaultSpec(HYPERCORE_CHAIN_ID, "0x14ecb3f4932522b8d508fa6e237e2746a4999feb"),  # Quantitative Market Neutral Strategy
    VaultSpec(HYPERCORE_CHAIN_ID, "0x35d92bd6571d0bdc5413250b28692e6be905d67b"),  # Abs Return Sophon Quant
    VaultSpec(HYPERCORE_CHAIN_ID, "0x8bb033130be354eed1110ce7228d7095f001d9fe"),  # HyperNeutral #1
    VaultSpec(HYPERCORE_CHAIN_ID, "0x9114a5161f18739ced233190861ea275c8b3a99b"),  # Symphony
)


def migrate(context: MigrationContext) -> None:
    """Refresh the portfolio tags for the listed Hypercore vaults.

    The runner controls preview and persistence of the staged metadata.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after previewing or committing the refresh.
    """
    stage_hyperliquid_strategy_tags(context, TARGETS)
    context.commit_metadata()
