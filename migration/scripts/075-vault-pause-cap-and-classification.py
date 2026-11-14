"""Refresh reviewed availability and distinguish VaultMorpho's capped implementation.

Purpose: Refresh the three legacy Upshift pause readers, Odyssey's cap, and VaultMorpho's classification and cap.
Scope: Five existing Ethereum vault rows; current availability and VaultMorpho metadata only.
Branch: fix/vault-remaining-issues
PR title: fix: enforce vault pauses and caps and explain unavailable operations
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1777
Preserves: Historical prices, NAV, shares, discovery boundaries and reader state.
Expected: At most five existing rows change; current contract state determines availability.

Run through vault-migrate with scanner writers stopped. This does not apply a
present restriction to historical membership. No price backfill is needed:
price units and history acquisition are unchanged. Publish exports after apply.
"""

from dataclasses import replace

from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
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
    """Refresh existing scan fields through the repaired readers, retaining history.

    :param context: Runner-owned metadata staging, reports, backups and commit.
    :return: ``None`` after staging reviewed changes.
    """
    database = context.metadata()
    web3 = None
    block = None
    changed = []
    missing = []
    for address in TARGETS:
        spec = VaultSpec(1, address)
        row = database.rows.get(spec)
        if row is None:
            missing.append(str(spec))
            continue
        if web3 is None:
            web3 = create_multi_provider_web3(read_json_rpc_url(1))
            block = web3.eth.block_number
        detection = row["_detection_data"]
        if detection.get_spec() != spec:
            raise ValueError(f"Conflicting vault identity at {spec}")
        is_vault_morpho = address == TARGETS[-1]
        if is_vault_morpho:
            # Interface detection uses latest; capacity reads below use the numeric block.
            features = detect_vault_features(web3, address, verbose=False)
            if ERC4626Feature.vault_morpho_like not in features:
                raise ValueError("VaultMorpho no longer exposes the reviewed interface")
            detection = replace(detection, features=features)
        vault = create_vault_instance(web3, address, features=detection.features, token_cache={}, default_block_identifier=block)
        # Read only repaired fields directly: scanner activity thresholds and
        # unrelated optional metadata reads must not erase valid saved data.
        updates = {
            "_deposits_open": vault.fetch_deposit_open(),
            "_deposit_closed_reason": vault.fetch_deposit_closed_reason(),
        }
        if is_vault_morpho:
            protocol = get_vault_protocol_name(detection.features)
            if protocol != "Morpho":
                raise ValueError(f"Unexpected VaultMorpho protocol identity for {spec}")
            updates.update(
                {
                    "Protocol": protocol,
                    "_detection_data": detection,
                    "features": set(detection.features),
                    "Features": ", ".join(sorted(f.name for f in detection.features)),
                }
            )
        fields = [k for k, v in updates.items() if row.get(k) != v]
        if fields:
            row.update(updates)
            changed.append({"vault": address, "fields": sorted(fields)})
    context.report(inspected_rows=len(TARGETS) - len(missing), changed_rows=len(changed), updated_fields=sum(len(r["fields"]) for r in changed), missing_specs=missing, changes=changed)
    context.commit_metadata()
