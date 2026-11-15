"""Refresh six permission records after verified implementation inspection.

Scope: four reviewed VaultMorpho proxies and two stale Morpho V2 records.
Preserves: prices, NAV, discovery and all fields except _deposit_permission.
Run reminder: preview/apply through vault-migrate with scanner writers stopped,
then publish the normal export and verify both public permission fields.
"""
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext

#: Fixed reviewed identities; no discovery or price backfill is required.
TARGETS = (
    '0x21ed44c18c926c60092b1b2985e2c999421a5a69',
    '0x52a965c435cffbc3eebf9200550cc597f4e54029',
    '0x9376bed03f9653e3927880f0d9024f612227a70a',
    '0xd2f62c8e654dbcbc03abfc2f9bfcd7f84027a5d8',
    '0x91393f69a308844a1b4830de1a57fb489c9d88ce',
    '0xe44b314e95a40a590975fc878dabd0c76c25a366',
)


def migrate(context: MigrationContext) -> None:
    """Read current policy through the adapter and preserve unresolved records.

    :param context: Existing staged metadata and migration receipt owner.
    """
    database = context.metadata()
    web3 = create_multi_provider_web3(read_json_rpc_url(1))
    block = web3.eth.block_number
    changes, missing, unresolved = [], [], []
    for address in TARGETS:
        spec = VaultSpec(1, address)
        row = database.rows.get(spec)
        if row is None:
            missing.append(str(spec))
            continue
        detection = row['_detection_data']
        if detection.get_spec() != spec:
            raise ValueError(f'Conflicting metadata identity: {spec}')
        vault = create_vault_instance(web3, address, features=set(detection.features))
        if vault is None:
            unresolved.append(str(spec))
            continue
        vault.default_block_identifier = block
        permission = fetch_deposit_permission(vault).value
        if permission == 'unknown':
            unresolved.append(str(spec))
            continue
        before = row.get('_deposit_permission')
        if before != permission:
            row['_deposit_permission'] = permission
            changes.append({'address':address, 'before':before, 'after':permission})
    context.report(changed_rows=len(changes), updated_fields=len(changes),
                   observed_block=block, missing_specs=missing,
                   unresolved_specs=unresolved, changes=changes)
    context.commit_metadata()
