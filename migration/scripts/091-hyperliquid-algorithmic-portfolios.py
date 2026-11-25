"""Refresh persisted algorithmic trading tags for ten Hyperliquid vaults.

Purpose: Publish the algorithmic trading classification.
Scope: Only _strategy_tags for the ten listed Hypercore (9999) deployments.
Branch: check-hyperliquid-long-short-vault-equity-curves
PR title: feat: classify more Hyperliquid long-short portfolios
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794
Preserves: Other metadata, unrelated rows, prices, leads, reader state and timestamp caches.
Expected: Up to ten updated fields. Report absent deployments without creating rows.
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
    VaultSpec(HYPERCORE_CHAIN_ID, "0x115849ce84370f25cadcf0d348510d73837e1aa5"),  # Orbit Value Strategies
    VaultSpec(HYPERCORE_CHAIN_ID, "0x14ecb3f4932522b8d508fa6e237e2746a4999feb"),  # Quantitative Market Neutral Strategy
    VaultSpec(HYPERCORE_CHAIN_ID, "0x35d92bd6571d0bdc5413250b28692e6be905d67b"),  # Abs Return Sophon Quant
    VaultSpec(HYPERCORE_CHAIN_ID, "0x8bb033130be354eed1110ce7228d7095f001d9fe"),  # HyperNeutral #1
    VaultSpec(HYPERCORE_CHAIN_ID, "0x9114a5161f18739ced233190861ea275c8b3a99b"),  # Symphony
    VaultSpec(HYPERCORE_CHAIN_ID, "0x9e02aca9865e1859bb7865f6f64801e804a173df"),  # AceVault Hyper01
    VaultSpec(HYPERCORE_CHAIN_ID, "0xac26cf5f3c46b5e102048c65b977d2551b72a9c7"),  # Long HYPE & BTC | Short Garbage
    VaultSpec(HYPERCORE_CHAIN_ID, "0xce71894b42f5c31a6ae6ca4fb77441c68ab15086"),  # Buyback Spread Engine
)


def migrate(context: MigrationContext) -> None:
    """Refresh the strategy tags for the listed Hypercore vaults.

    The runner controls preview and persistence of the staged metadata.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after previewing or committing the refresh.
    """
    stage_hyperliquid_strategy_tags(context, TARGETS)
    context.commit_metadata()
