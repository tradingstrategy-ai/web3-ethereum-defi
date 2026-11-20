"""Refresh persisted staff picks for 15 native vaults.

Purpose: Publish the November staff selection.
Scope: Only _strategy_tags for the listed Hyperliquid, Lighter and GRVT vaults.
Branch: add-staff-pick-strategy-tag
PR title: feat: add staff pick strategy tag
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1796
Preserves: All other metadata, prices, reader state and timestamp caches.
Expected: Up to 15 updated fields. Report missing deployments without creating rows.

LLM-generated operational migration; changes require human review.
Run after merge through vault-migrate with the scanner stopped.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed deployments, frozen independently of future staff selections.
TARGETS = (
    VaultSpec(9999, "0x5ce0ce4387679de0335a3e8a1c9a7abb4f150a71"),  # Bunny
    VaultSpec(9999, "0xa1b6d8efbcb2fb750a84dbc05649fa4968034f04"),  # PF1
    VaultSpec(9999, "0x4b0eab9444a75a03f1ef340c8beac737afa5ab09"),  # Titan Vault
    VaultSpec(9999, "0xb65dd7c56afbf3b272ab5fc49be44b47dca18003"),  # FuturAI Labs Low Volatility
    VaultSpec(9999, "0x53375ca9a649f337d83d0834fec0db97640a2a06"),  # FuturAI Labs Medium Volatility
    VaultSpec(9999, "0xbefed76a71682b1dc48cca5a10f397cbc492a216"),  # Breaking (FuturAI Labs)
    VaultSpec(9999, "0x8bb033130be354eed1110ce7228d7095f001d9fe"),  # HyperNeutral #1
    VaultSpec(9999, "0x060d01aa996003b3731a992462d7f0ba68bf3b04"),  # Edge & Hedge
    VaultSpec(9999, "0xce71894b42f5c31a6ae6ca4fb77441c68ab15086"),  # Buyback Spread Engine
    VaultSpec(9999, "0x490af7d4a048a81db0f677517ed6373565b42349"),  # Passivbot Canon
    VaultSpec(9999, "0xc3b1bf1f1e6fb8161ef4dc2f34e2c56f70b94b11"),  # Cold Process
    VaultSpec(9999, "0xa415c4ded17cac76a1d81a54d9f4aeb48ffd4dd0"),  # BitRocket
    VaultSpec(9998, "lighter-pool-281474976552443"),  # Steady Wealth Builder
    VaultSpec(9998, "lighter-pool-281474976543116"),  # Candle Effect v2
    VaultSpec(325, "vlt:38exn6urqqrjk6hnypihgxo2oro"),  # KangCFA
)


def migrate(context: MigrationContext) -> None:
    """Refresh the selected vaults from their native tag registries.

    Resolve existing rows before staging changes. Missing vaults receive
    their classifications when first collected by their native exporter.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the refresh.
    :raises ValueError: If a target lacks its expected native classification or resolvable tags.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    native_protocols = {9999: ("Hyperliquid", ERC4626Feature.hypercore_native), 9998: ("Lighter", ERC4626Feature.lighter_native), 325: ("GRVT", ERC4626Feature.grvt_native)}
    for spec, row in rows.items():
        protocol, feature = native_protocols[spec.chain_id]
        detection = row.get("_detection_data")
        if row.get("Protocol") != protocol or not isinstance(detection, ERC4262VaultDetection) or feature not in detection.features:
            raise ValueError(f"Unexpected protocol classification for {spec.as_string_id()}")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve strategy tags for all selected vaults"
        raise ValueError(message)

    for update in updates:
        rows[update.spec]["_strategy_tags"] = set(update.new_tags)

    context.report(
        inspected_rows=len(rows),
        changed_rows=len(updates),
        updated_fields=len(updates),
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows],
        legacy_tag_rows=invalid,
    )
    context.commit_metadata()
