"""Refresh reviewed availability and distinguish VaultMorpho's capped implementation.

Purpose: Refresh the three legacy Upshift pause readers, Odyssey's cap, and VaultMorpho's classification and cap.
Scope: Four Ethereum pause/cap rows and existing VaultMorpho-family catalogue rows on configured chains.
Branch: fix/vault-remaining-issues
PR title: fix: enforce vault pauses and caps and explain unavailable operations
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1777
Preserves: Historical prices, NAV, shares, discovery boundaries and reader state.
Expected: Four pause/cap candidates plus the capped VaultMorpho rows found in saved metadata; report exact counts.

Run through vault-migrate with scanner writers stopped. This does not apply a
present restriction to historical membership. No price backfill is needed:
price units and history acquisition are unchanged. Publish exports after apply.
"""

from dataclasses import replace

from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, GENERIC_ERC4626_PROTOCOL_NAME
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext

#: Existing rows with demonstrated stale classification or availability readers.
TARGETS = (
    "0x0985c88929a776a2e059615137a48ba5a473e25d",
    "0x396a3f77ee1faf5a3c46e878ba7b7a2dcbe55517",
    "0xda89af5bf2eb0b225d787abfa9095610f2e79e7d",
    "0x128272086dde4bd54b5a85110f97f07559bf7331",
    "0x21ed44c18c926c60092b1b2985e2c999421a5a69",
)


def migrate(context: MigrationContext) -> None:
    """Correct current availability and unsupported Morpho defaults without rewriting prices.

    A saved name only selects candidates: contract detection must confirm the
    capped implementation before its metadata changes. Read repaired availability
    fields directly so the scanner's minimum-value threshold cannot hide a pause
    or full cap. Rebuild only fields affected by the incorrect protocol identity.
    Contract-read and provider failures propagate unchanged and prevent a commit.

    :param context: :class:`~eth_defi.vault_protocols.migration.MigrationContext`
        managing metadata, reports and backups. Its
        :meth:`~eth_defi.vault_protocols.migration.MigrationContext.commit_metadata`
        writes changes only when the runner is in apply mode.
    :raises ValueError: If saved identities conflict, the reviewed deployment no
        longer matches, or rebuilding its metadata fails. No commit is made.
    """
    database = context.metadata()
    targets = {VaultSpec(1, address): address == TARGETS[-1] for address in TARGETS}
    for spec, row in database.rows.items():
        features = row["_detection_data"].features if "_detection_data" in row else set()
        if ERC4626Feature.vault_morpho_like in features or (row.get("Name") == "VaultMorpho" and ERC4626Feature.morpho_like in features):
            targets[spec] = True
    connections = {}
    blocks = {}
    changed = []
    missing = []
    unresolved = []
    for spec, is_vault_morpho in targets.items():
        address = spec.vault_address
        row = database.rows.get(spec)
        if row is None:
            missing.append(str(spec))
            continue
        if spec.chain_id not in connections:
            connections[spec.chain_id] = create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
            blocks[spec.chain_id] = connections[spec.chain_id].eth.block_number
        web3 = connections[spec.chain_id]
        block = blocks[spec.chain_id]
        detection = row["_detection_data"]
        if detection.get_spec() != spec:
            raise ValueError(f"Conflicting vault identity at {spec}")
        if is_vault_morpho:
            # Recheck the contract because its saved name alone cannot identify
            # the implementation. Detection uses latest; capacity reads below
            # use the selected block so related balances are consistent.
            features = detect_vault_features(web3, address, verbose=False)
            if ERC4626Feature.vault_morpho_like not in features:
                if spec == VaultSpec(1, TARGETS[-1]):
                    raise ValueError("VaultMorpho no longer exposes the reviewed interface")
                unresolved.append(str(spec))
                continue
            detection = replace(detection, features=features)
        vault = create_vault_instance(web3, address, features=detection.features, token_cache={}, default_block_identifier=block)
        # Read only repaired fields directly: scanner activity thresholds and
        # unrelated optional metadata reads must not erase valid saved data.
        updates = {
            "_deposits_open": vault.fetch_deposit_open(),
            "_deposit_closed_reason": vault.fetch_deposit_closed_reason(),
        }
        if is_vault_morpho:
            # Rebuild only attribution-dependent fields. The generic adapter
            # deliberately reports unknown policy/fees instead of Morpho defaults.
            rebuilt = create_vault_scan_record(web3, detection, block, token_cache={})
            if rebuilt.get("_rpc_failure_category") is not None or rebuilt.get("Protocol") != GENERIC_ERC4626_PROTOCOL_NAME:
                raise ValueError(f"Unattributed VaultMorpho metadata rebuild failed for {spec}")
            names = (
                "Protocol",
                "_detection_data",
                "features",
                "Features",
                "Link",
                "Mgmt fee",
                "Perf fee",
                "Deposit fee",
                "Withdraw fee",
                "Protocol fee",
                "_fees",
                "_manager_name",
                "_morpho_offchain_data",
                "_morpho_api_status",
                "_flags",
                "_notes",
                "_strategy_tags",
                "_lockup",
                "_withdrawal_period",
                "_deposit_permission",
                "_whitelist_notes",
                "_deposit_manager",
            )
            updates.update({name: rebuilt[name] for name in names})
        fields = [k for k, v in updates.items() if row.get(k) != v]
        if fields:
            row.update(updates)
            changed.append({"vault": str(spec), "fields": sorted(fields)})
    context.report(inspected_rows=len(targets) - len(missing), changed_rows=len(changed), updated_fields=sum(len(r["fields"]) for r in changed), missing_specs=missing, unresolved_specs=unresolved, changes=changed)
    context.commit_metadata()
