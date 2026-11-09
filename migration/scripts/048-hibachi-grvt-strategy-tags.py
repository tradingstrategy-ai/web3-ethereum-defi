"""Refresh reviewed Hibachi and GRVT strategy tags.

Purpose: Classify current Fire ID 4 and new strategies, and remove stale tags.
Scope: Only _strategy_tags for the 33 fixed identities below.
Branch: mighty-wombat
PR title: fix: refresh Hibachi and GRVT vault strategy tags
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1745
Preserves: Other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most 33 existing rows and changed fields; report missing rows.
Evidence: docs/research/hibachi-grvt-strategy-review-2026-11-09.md.
No network reads or historical price backfill are required.

LLM-generated operational migration; changes require human review.
Run after merge using .claude/docs/migration-scripts.md. Stop the scanner and
competing writers before preview and apply; restart the same inspected image
for normal cleaning, exports and uploads to publish the corrected tags.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Fixed current inventory plus the historical Fire vault.
TARGETS = (
    #: The Crypto Meerkat.
    VaultSpec(325, "vlt:2zqosukicgltfcjdet4kpmecvfg"),
    #: Ampersan Research Limited.
    VaultSpec(325, "vlt:2zqttakxz5jkdjuyjpbwudibkbw"),
    #: AI Alpha Strategy.
    VaultSpec(325, "vlt:2zrj92gtwomgi8yddq8hntbf2lz"),
    #: MizerXBT Strategy.
    VaultSpec(325, "vlt:2zrkgkmfjqjlezllk24ledpwqbc"),
    #: AllDeFi Quant Directional Strategy.
    VaultSpec(325, "vlt:2ztqfqdo7iipsg2ki99glseyfmb"),
    #: Rogue Trader Discretionary.
    VaultSpec(325, "vlt:2zu1ohpgg3e3bef9h9gwsj8crd9"),
    #: Silentist.
    VaultSpec(325, "vlt:302flw8mveki8wc4e7sf4fwczto"),
    #: Ignight Inverse-Trend (INACTIVE).
    VaultSpec(325, "vlt:328ljdq3lxhyoccqi3mtxhhq5u3"),
    #: Ignight Inverse-Trend.
    VaultSpec(325, "vlt:32aef5qkjhkibvf8inqq9oqsyep"),
    #: Grvt Liquidity Provider (GLP).
    VaultSpec(325, "vlt:34dtzyg6lhkgm49je5aabi9tebw"),
    #: ProjectBTC - Liquidity Cycle Protocol.
    VaultSpec(325, "vlt:34vl6upbsdhmiebvnragnnkucfk"),
    #: Fisher8 Vault.
    VaultSpec(325, "vlt:35j0gsfvpviwtto6hamblg9rexg"),
    #: AI Grid Trading Strategy [INACTIVE].
    VaultSpec(325, "vlt:35jpjdfrwaeuj0w7he4wi5vboag"),
    #: Adaptive Trend Reversal System (INACTIVE).
    VaultSpec(325, "vlt:36qtkmzkjcerbi0fmibxwltroij"),
    #: Warwolf: Kinetic Volatility Capture.
    VaultSpec(325, "vlt:371sgbnors7u71g6wpoo1lrst4p"),
    #: Pharos Market Maker (PMM).
    VaultSpec(325, "vlt:386cnqkc6tb186ix2mtgayhbfrz"),
    #: AI Follower.
    VaultSpec(325, "vlt:38enbl9m2f4almsegttr2irlhia"),
    #: KangCFA.
    VaultSpec(325, "vlt:38exn6urqqrjk6hnypihgxo2oro"),
    #: Ethereum Moving Average Long/Short.
    VaultSpec(325, "vlt:38t5xhyuxmzunwkud80clxhitok"),
    #: RoboNet AI Long/Short Strategy.
    VaultSpec(325, "vlt:38wg4abwh126ay9l3fjhhem73co"),
    #: Show Me The BTC (Breakout).
    VaultSpec(325, "vlt:3a483mupgtm3v22dalkd16spqfg"),
    #: BTC Maxi Lab.
    VaultSpec(325, "vlt:3b9tvtvisaumciyw14znklwgnq3"),
    #: Solana Moving Average Long/Short.
    VaultSpec(325, "vlt:3brdxa1w6s5uhqkzc3jvravw1d9"),
    #: [Archived] Test Vault.
    VaultSpec(325, "vlt:3e4ebobovxfmmukmpnltcxtvtfp"),
    #: Balanced Bundle.
    VaultSpec(325, "vlt:3eesf9iphosimfc4szp2ixoqgiw"),
    #: Opportunistic Bundle.
    VaultSpec(325, "vlt:3eexdzmz72tqcinpbu4f4i0dm2z"),
    #: LBZ Trading Trend pullback.
    VaultSpec(325, "vlt:3i8nkgzoo173zwckkhmvo4fdrft"),
    #: Global Macro Hedge Trading.
    VaultSpec(325, "vlt:3l5macfjcmky91ckru4tejdzxpl"),
    #: High Yield Earn.
    VaultSpec(325, "vlt:3lpp5meo1g1pquz8ebwdee8sgpo"),
    #: Growi Alpha Vault.
    VaultSpec(9997, "hibachi-vault-2"),
    #: Fire Liquidity Provider.
    VaultSpec(9997, "hibachi-vault-4"),
    #: Robonet - Allora Woodpecker.
    VaultSpec(9997, "hibachi-vault-5"),
    #: Fire Liquidity Provider (historical).
    VaultSpec(9997, "hibachi-vault-3"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed classifications using the scanner's shared resolver.

    Validate protocol detection and all proposals before staging any changes.
    The runner owns locks, previews, backups and persistence.

    :param context: Guarded migration attempt and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the changes.
    :raises ValueError: If a target has conflicting detection or cannot resolve.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or ({325: ERC4626Feature.grvt_native, 9997: ERC4626Feature.hibachi_native}[spec.chain_id] not in detection.features):
            raise ValueError(f"Hibachi/GRVT target is not a Hibachi/GRVT vault: {spec}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve all reviewed Hibachi/GRVT strategy tags"
        raise ValueError(message)
    for update in updates:
        rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(
        inspected_rows=len(rows),
        changed_rows=len(updates),
        updated_fields=len(updates),
        legacy_tag_rows=invalid,
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows],
    )
    context.commit_metadata()
