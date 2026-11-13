"""Refresh the persisted strategy tags of reviewed native perp DEX vaults.

Purpose: Add the new Martingale-like strategy tag to the maintainer-reviewed
Hyperliquid and Lighter vaults that run limit order ladder market making with
Martingale-like position averaging, add high-frequency, high-win-rate
trading to PF1, and reclassify both pmalt vaults from
high-frequency, high-win-rate to discretionary trading.
Scope: Only _strategy_tags for ten Hypercore (9999) vaults and two Lighter
(9998) pools: PF1, BitRocket, Bunny, Growi HF, Steady Wealth Builder, both
FuturAI Labs vaults, Stratwise Multi-Asset Public, Cold Process,
Passivbot Canon and both pmalt vaults.
Branch: add-martingale-like-strategy-tag
PR title: feat: add Martingale-like strategy tag and reclassify pmalt
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1770
Preserves: All other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most twelve existing rows and twelve changed fields; report absent rows
without creating them. Idempotent transformation.
No network reads or historical scanning are required.

LLM-generated operational migration; changes require human review.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Maintainer-reviewed vaults, mapped to the native feature their persisted
#: detection evidence must carry. Reviewed on 2026-11-13.
TARGETS: dict[VaultSpec, ERC4626Feature] = {
    #: PF1
    VaultSpec(HYPERCORE_CHAIN_ID, "0xa1b6d8efbcb2fb750a84dbc05649fa4968034f04"): ERC4626Feature.hypercore_native,
    #: BitRocket
    VaultSpec(HYPERCORE_CHAIN_ID, "0xa415c4ded17cac76a1d81a54d9f4aeb48ffd4dd0"): ERC4626Feature.hypercore_native,
    #: Bunny
    VaultSpec(HYPERCORE_CHAIN_ID, "0x5ce0ce4387679de0335a3e8a1c9a7abb4f150a71"): ERC4626Feature.hypercore_native,
    #: Growi HF
    VaultSpec(HYPERCORE_CHAIN_ID, "0x1e37a337ed460039d1b15bd3bc489de789768d5e"): ERC4626Feature.hypercore_native,
    #: FuturAI Labs - High Sharpe, Medium Volatility
    VaultSpec(HYPERCORE_CHAIN_ID, "0x53375ca9a649f337d83d0834fec0db97640a2a06"): ERC4626Feature.hypercore_native,
    #: FuturAI Labs - High Sharpe, Low Volatility
    VaultSpec(HYPERCORE_CHAIN_ID, "0xb65dd7c56afbf3b272ab5fc49be44b47dca18003"): ERC4626Feature.hypercore_native,
    #: Stratwise Multi-Asset Public
    VaultSpec(HYPERCORE_CHAIN_ID, "0x0ff219ac20596b457558341bc410bc7a08a1394c"): ERC4626Feature.hypercore_native,
    #: Cold Process
    VaultSpec(HYPERCORE_CHAIN_ID, "0xc3b1bf1f1e6fb8161ef4dc2f34e2c56f70b94b11"): ERC4626Feature.hypercore_native,
    #: Passivbot Canon
    VaultSpec(HYPERCORE_CHAIN_ID, "0x490af7d4a048a81db0f677517ed6373565b42349"): ERC4626Feature.hypercore_native,
    #: Steady Wealth Builder
    VaultSpec(LIGHTER_CHAIN_ID, "lighter-pool-281474976552443"): ERC4626Feature.lighter_native,
    #: pmalt (Hyperliquid)
    VaultSpec(HYPERCORE_CHAIN_ID, "0x4dec0a851849056e259128464ef28ce78afa27f6"): ERC4626Feature.hypercore_native,
    #: pmalt (Lighter)
    VaultSpec(LIGHTER_CHAIN_ID, "lighter-pool-281474976552918"): ERC4626Feature.lighter_native,
}


def migrate(context: MigrationContext) -> None:
    """Refresh only the reviewed vaults through their native tag resolvers.

    Restrict collection to the target rows and validate their native protocol
    evidence before staging changes, so an unrelated row at the same address
    can never be retagged. The runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If a present target lacks the expected native feature
        or its strategy tags cannot be resolved.
    """
    database = context.metadata()
    present = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    missing = [spec.as_string_id() for spec in TARGETS if spec not in present]

    for spec, row in present.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or TARGETS[spec] not in detection.features:
            raise ValueError(f"Strategy-tag target {spec} is not classified as {TARGETS[spec].value}")

    updated_fields = 0
    invalid = 0
    if present:
        updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=present))
        if skipped:
            raise ValueError(f"Could not resolve strategy tags for {skipped} of {len(present)} target rows")
        for update in updates:
            database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
        updated_fields = len(updates)

    context.report(inspected_rows=len(present), missing_specs=missing, updated_fields=updated_fields, legacy_tag_rows=invalid)
    context.commit_metadata()
