"""A targeted refresh preserves history and unresolved records."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from eth_defi.vault.base import VaultSpec


def test_permission_refresh_changes_only_known_target_policy():
    path = Path(__file__).parents[2] / 'migration/scripts/078-vault-permission-reality.py'
    spec = importlib.util.spec_from_file_location('permission_refresh', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = {}
    for address in module.TARGETS:
        identity = VaultSpec(1, address)
        rows[identity] = {'_detection_data': SimpleNamespace(get_spec=lambda identity=identity:identity, features=set()), '_deposit_permission':'unknown', 'NAV':123, 'First seen':'original'}
    context = Mock()
    context.metadata.return_value.rows = rows
    web = Mock()
    web.eth.block_number = 456
    policies = [SimpleNamespace(value=v) for v in ['permissionless']*4 + ['whitelisted','unknown']]
    with patch.object(module,'read_json_rpc_url',return_value='unused'), patch.object(module,'create_multi_provider_web3',return_value=web), patch.object(module,'create_vault_instance'), patch.object(module,'fetch_deposit_permission',side_effect=policies):
        module.migrate(context)
    assert [r['_deposit_permission'] for r in rows.values()] == ['permissionless']*4+['whitelisted','unknown']
    assert all(r['NAV']==123 and r['First seen']=='original' for r in rows.values())
    assert context.report.call_args.kwargs['changed_rows']==5
    assert len(context.report.call_args.kwargs['unresolved_specs'])==1
    context.commit_metadata.assert_called_once()
