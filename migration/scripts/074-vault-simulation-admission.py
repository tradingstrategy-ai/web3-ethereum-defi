"""Refresh admission for the 40 reviewed simulation targets and correct MAAT classification.

Purpose: Replace unknown/stale persisted policy using the repaired adapters; correct the MAAT strategy formerly classified as Fluid by name.
Scope: Existing reviewed targets only; admission and MAAT adapter-derived metadata. No new rows or price writes.
Branch: fix/vault-simulation-coverage
PR title: feat: expand guarded vault simulation coverage and settlement
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1773
Preserves: Historical prices, discovery boundaries, reader state and unrelated metadata.
Expected: At most 40 existing rows; counts depend on current admission state.

Run through vault-migrate with scanner writers stopped. No historical price
backfill is needed: this corrects existing metadata, not price coverage.
Production apply and normal export publication are separate operator steps.
"""

from dataclasses import replace

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission, create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed chain/address identities; duplicate display names are not keys.
TARGETS = (
    "1-0x1abd0403591be494771115d74ed9e120530f356e",
    "1-0x273da948aca9261043fbdb2a857bc255ecc29012",
    "1-0x2916dfb3324377645cac4c5e78dffce910a61393",
    "1-0x30cacd22f178c9e57b0b010e1f9432881aa530c4",
    "1-0x3c1a2d1c5d7acfbfb9adcb168694e2fcd6e713c1",
    "1-0x408b6a3e2daf288864968454aae786a2a042df36",
    "1-0x43e3f5e27422c3a938269368eb7c3a3b545bb6ca",
    "1-0x7290245b3e564f0ae2da5af0690ef4842cf13c75",
    "1-0x7c51228c60fe580249f0bd99f61740c0594b23b3",
    "1-0x7f47b1ced85f5d773cd68ca606ed6f0fee381046",
    "1-0x885984bd7a15aded423096fa1b82c556e8bb30fd",
    "1-0x8bcd746976885b5832bad07b4921e3f2dd1d3703",
    "1-0x953972ea0c1703c58f09fb6fd2477fdcf0fee074",
    "1-0x9fb7b4477576fe5b32be4c1843afb1e55f251b33",
    "1-0xa7569a44f348d3d70d8ad5889e50f78e33d80d35",
    "1-0xa7b78fbd9ad54cab94418978c05548317a7ca867",
    "1-0xab5955eb671d150527f8e61a42b703832f86616c",
    "1-0xbeef00a59b577423653a1526c7009bde103f542b",
    "1-0xbff755fac5e2fadd58155982046b2ebc0746a97f",
    "1-0xc207d3f66537d7f66456808379a0e560cf20da36",
    "1-0xc7d7f434c015f2a7e77df9763ade300f2171a05a",
    "1-0xdc4e33582784f8b577f4e84c47af9b31e583e6db",
    "1-0xe275a69b93a5745e1560ee82d033205d323bfad9",
    "143-0x288604ba0e9f08c22815eb36c2f4a49a44ee16bb",
    "42161-0x1a996cb54bb95462040408c06122d45d6cdb6096",
    "42161-0x466aa7fe90e1b2797f53ab303e677379c6542e7f",
    "42161-0x6db146db8dcf2f96948c3982f0770871246ae810",
    "42161-0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c",
    "8453-0x036a01efddc87f6634ffde0533ee528b90fc7a45",
    "8453-0x1b75f2074a2c07415e148d26f3618d8f44b3a0e9",
    "8453-0x223ec656415eec0c6989f0e9a20c78d24dc7f00b",
    "8453-0x314fd07319ef645ba7d548915ccd91f4788a1839",
    "8453-0x54d8417bd21c86a7806b58f5aa2e2e0bb88b856a",
    "8453-0x9c6864105aec23388c89600046213a44c384c831",
    "8453-0xe690a58ef52854513462745237f6a213a0d54df1",
    "8453-0xf42f5795d9ac7e9d757db633d693cd548cfd9169",
    "8453-0xf6a70085b7f79fa76b04ebf7a2d7d87c3c5c04bc",
    "999-0x818a0ee5165cbb011d6d537578744bc1e90bd433",
    "1-0x91393f69a308844a1b4830de1a57fb489c9d88ce",
    "1-0xe44b314e95a40a590975fc878dabd0c76c25a366",
)


def migrate(context: MigrationContext) -> None:
    """Refresh reviewed policy through the existing scanner and staged database.

    :param context: Existing migration context owning preview, backups and commit.
    :return: ``None`` after reporting staged changes.
    """
    database = context.metadata()
    connections = {}
    changed = []
    missing = []
    unresolved = []
    for key in TARGETS:
        chain, address = key.split("-", 1)
        spec = VaultSpec(int(chain), address)
        row = database.rows.get(spec)
        if row is None:
            missing.append(key)
            continue
        detection = row["_detection_data"]
        if detection.get_spec() != spec:
            raise ValueError(f"Conflicting vault identity at {key}")
        features = set(detection.features)
        is_maat = key == "42161-0x466aa7fe90e1b2797f53ab303e677379c6542e7f"
        if is_maat:
            features.discard(ERC4626Feature.fluid_like)
            features.add(ERC4626Feature.maat_like)
        if spec.chain_id not in connections:
            connections[spec.chain_id] = create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
        web3 = connections[spec.chain_id]
        vault = create_vault_instance(web3, address, features=features)
        permission = fetch_deposit_permission(vault).value
        if permission == "unknown":
            unresolved.append(key)
            continue
        updates = {"_deposit_permission": permission}
        if is_maat:
            corrected_detection = replace(detection, features=features)
            rebuilt = create_vault_scan_record(web3, corrected_detection, web3.eth.block_number, token_cache={})
            if rebuilt.get("Protocol") != "MAAT":
                raise ValueError(f"MAAT metadata rebuild failed for {key}")
            updates.update({name: value for name, value in rebuilt.items() if name not in {"NAV", "Shares", "First seen", "Address"}})
            updates["_deposit_permission"] = permission
        fields = {name: value for name, value in updates.items() if row.get(name) != value}
        if fields:
            row.update(fields)
            changed.append({"id": key, "fields": sorted(fields)})
    context.report(inspected_rows=len(TARGETS) - len(missing), changed_rows=len(changed), updated_fields=sum(len(r["fields"]) for r in changed), missing_specs=missing, unresolved_specs=unresolved, changes=changed)
    context.commit_metadata()
