"""Source-backed Birch Hill hook classification and scoped metadata repair."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from web3.exceptions import ContractLogicError

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.yearn.vault import BIRCH_HILL_DEPOSIT_HOOK, BIRCH_HILL_RWA_USDC, YearnV3Vault
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.mark.parametrize(
    "hook,expected",
    [(BIRCH_HILL_DEPOSIT_HOOK, "whitelisted"), (ZERO_ADDRESS_STR, "permissionless"), ("0x1111111111111111111111111111111111111111", "unknown")],
)
def test_flagship_hook_policy(hook: str, expected: str) -> None:
    """Follow hook replacement without treating arbitrary hooks as public.

    The reviewed hook requires explicit receiver membership. Removing it
    opens deposits; replacing it with unreviewed code requires research.

    :param hook: Simulated hook address at the pinned block.
    :param expected: Scanner enum value for that configuration.
    """
    vault = YearnV3Vault(MagicMock(), BIRCH_HILL_RWA_USDC, default_block_identifier=123)
    vault.__dict__["vault_contract"] = MagicMock()
    call = vault.vault_contract.functions.deposit_hook.return_value.call
    call.return_value = hook
    assert fetch_deposit_permission(vault).value == expected
    call.assert_called_once_with(block_identifier=123)
    vault.vault_contract.functions.deposit_limit_module.assert_not_called()
    assert vault.get_whitelist_notes() is None


@pytest.mark.parametrize("error", [ContractLogicError("execution reverted"), ExtraValueError("RPC timeout")])
def test_flagship_unreadable_hook_is_unknown(error: Exception) -> None:
    """Never turn an unreadable hook into the legacy permissionless default.

    Both EVM failures and transport errors retain the scanner's unknown enum.

    :param error: Concrete simulated failure from the hook read.
    """
    vault = YearnV3Vault(MagicMock(), BIRCH_HILL_RWA_USDC, default_block_identifier=123)
    vault.__dict__["vault_contract"] = MagicMock()
    vault.vault_contract.functions.deposit_hook.return_value.call.side_effect = error
    assert fetch_deposit_permission(vault) is VaultDepositPermission.unknown


def test_flagship_policy_is_chain_scoped() -> None:
    """Do not apply Base's verified hook semantics to another deployment.

    A same-address contract on Ethereum must retain canonical Yearn handling.
    """
    vault = YearnV3Vault(MagicMock(), VaultSpec(1, BIRCH_HILL_RWA_USDC.vault_address), default_block_identifier=123)
    vault.__dict__["vault_contract"] = MagicMock()
    vault.vault_contract.functions.deposit_limit_module.return_value.call.return_value = ZERO_ADDRESS_STR
    assert fetch_deposit_permission(vault) is VaultDepositPermission.permissionless
    vault.vault_contract.functions.deposit_hook.assert_not_called()


@pytest.mark.parametrize("permission", [VaultDepositPermission.whitelisted, VaultDepositPermission.unknown])
def test_flagship_migration_stages_only_admission(monkeypatch: pytest.MonkeyPatch, permission: VaultDepositPermission) -> None:
    """Refresh only reviewed admission fields and refuse unresolved reads.

    Other rows and fields survive unchanged; repeating the repair stages no
    further changes, and unknown policy leaves all metadata untouched.

    :param monkeypatch: Replace only network-bound migration dependencies.
    :param permission: Resolved or unreadable policy supplied by the adapter.
    """
    path = Path(__file__).resolve().parents[3] / "migration/scripts/037-birch-hill-deposit-permission.py"
    spec = importlib.util.spec_from_file_location("birch_hill_permission_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = {"Protocol": "ERC-4626", "_deposit_permission": "permissionless", "_whitelist_notes": "Legacy default", "preserve": 42}
    unrelated = VaultSpec(1, ZERO_ADDRESS_STR)
    database = VaultDatabase(rows={BIRCH_HILL_RWA_USDC: row, unrelated: {"preserve": 99}})
    context = MagicMock()
    context.metadata.return_value = database
    web3 = MagicMock()
    web3.eth.chain_id = 8453
    monkeypatch.setattr(module, "read_json_rpc_url", lambda _chain: "unused")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: web3)
    monkeypatch.setattr(module, "fetch_deposit_permission", lambda _vault: permission)
    if permission is VaultDepositPermission.unknown:
        with pytest.raises(ValueError, match="unresolved"):
            module.migrate(context)
        assert row["_deposit_permission"] == "permissionless"
        assert row["_whitelist_notes"] == "Legacy default"
        context.commit_metadata.assert_not_called()
    else:
        module.migrate(context)
        assert row == {"Protocol": "ERC-4626", "_deposit_permission": "whitelisted", "_whitelist_notes": None, "preserve": 42}
        module.migrate(context)
        assert context.report.call_args.kwargs["changed_rows"] == 0
        assert context.commit_metadata.call_count == 2  # noqa: PLR2004
    assert database.rows[unrelated] == {"preserve": 99}
