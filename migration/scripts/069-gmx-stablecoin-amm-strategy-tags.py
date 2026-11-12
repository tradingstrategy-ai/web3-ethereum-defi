"""Tag the reviewed stablecoin-only GMX pools with amm_stablecoin.

Purpose: Persist the new stablecoin AMM market-making category for GMX pools
    whose every pool asset is a stablecoin.
Scope: Nine frozen GMX deployments below; only _strategy_tags changes.
Branch: add-amm-stablecoin-strategy-tag
PR title: feat: add amm_stablecoin strategy category and GLV [USDG-USDG] description
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1765
Preserves: Descriptions, flags, fees, features, prices, leads, reader state,
    timestamp caches and every unrelated row.
Expected: At most nine existing rows and nine changed fields; missing rows reported.
No network reads or new discovery. The tags come from eth_defi/gmx/tags.py.
Numbered 069 because open PR #1761 reserves 066 to 068.

LLM-generated operational migration; changes require human review.
Run after merge using the stopped-writer preview/apply procedure documented in
.claude/docs/migration-scripts.md, reviewing the entire pending batch.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import EVM_ADAPTER_FEATURE_PRIORITY, NATIVE_STRATEGY_TAG_RESOLVERS, collect_strategy_tag_updates

#: Exact deployments and their required GMX adapter feature; never expand at runtime.
TARGETS = {
    #: GLV [USDG-USDG]
    VaultSpec(42161, "0x4cd5a94a30876320ac65f2e192493ee476f13866"): ERC4626Feature.gmx_glv,
    #: GM swap [USDC-USDG]
    VaultSpec(42161, "0x408e8e83d4b8ac4bdeffdf896b49268a5fe5ef44"): ERC4626Feature.gmx_gm,
    #: GM swap [USDC-USDC.e]
    VaultSpec(42161, "0x9c2433dfd71096c435be9465220bb2b189375ea7"): ERC4626Feature.gmx_gm,
    #: GM swap [USDC-USD₮0]
    VaultSpec(42161, "0xb686bcb112660343e6d15bdb65297e110c8311c4"): ERC4626Feature.gmx_gm,
    #: GM swap [USDC-DAI]
    VaultSpec(42161, "0xe2fedb9e6139a182b98e7c2688ccfa3e9a53c665"): ERC4626Feature.gmx_gm,
    #: GM swap [USDe-USDC]
    VaultSpec(42161, "0x45ad16aaa28fb66ef74d5ca0ab9751f2817c81a4"): ERC4626Feature.gmx_gm,
    #: GM swap [USDC-USDC.e]
    VaultSpec(43114, "0x297e71a931c5825867e8fb937ae5cda9891c2e99"): ERC4626Feature.gmx_gm,
    #: GM swap [USDt-USDT.e]
    VaultSpec(43114, "0xa7b768d6a1f746fd5a513d440df2970ff099b0fc"): ERC4626Feature.gmx_gm,
    #: GM swap [USDC-USDT.e]
    VaultSpec(43114, "0xf3652eba45dc761e7add4091627d5cda21f61613"): ERC4626Feature.gmx_gm,
}


def migrate(context: MigrationContext) -> None:
    """Validate the exact GMX inventory before staging the tag refresh.

    Resolve through the same GMX mapping used by the scanner. A row with
    incompatible detection fails before any row is changed. Missing vaults are
    reported and never inserted. The runner owns all writes.

    :param context: Guarded migration attempt and staged metadata database.
    :return: None after reporting and committing or previewing the changes.
    :raises ValueError: If an existing target cannot be resolved safely.
    """
    database = context.metadata()
    targets = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in targets.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection):
            raise ValueError(f"Missing adapter detection for GMX strategy target {spec}")
        adapter = next((feature for feature in EVM_ADAPTER_FEATURE_PRIORITY if feature in detection.features), None)
        if row.get("Protocol") != "GMX" or adapter != TARGETS[spec] or any(feature in detection.features for feature in NATIVE_STRATEGY_TAG_RESOLVERS):
            raise ValueError(f"Incompatible adapter for GMX strategy target {spec}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=targets))
    if skipped or any(update.new_tags is None for update in updates):
        message = "GMX strategy targets could not resolve maintained tags"
        raise ValueError(message)
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=len(targets), updated_fields=len(updates), legacy_tag_rows=invalid, missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in targets])
    context.commit_metadata()
