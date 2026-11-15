"""Policy checks preserve outer deposit semantics and reject unproven routes."""
from unittest.mock import Mock, patch
import pytest
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.erc_4626.vault_protocol.vault_morpho.vault import VaultMorphoVault

OWNER = "0x1111111111111111111111111111111111111111"
ZERO = "0x" + "00" * 20


def morpho() -> MorphoV2Vault:
    vault = object.__new__(MorphoV2Vault)
    vault.web3 = Mock()
    vault.web3.eth.chain_id = 1
    vault.spec = Mock(vault_address=OWNER, chain_id=1)
    vault.default_block_identifier = 123
    return vault


@pytest.mark.parametrize('implementation', [
    'f392a5b96d3248440bb4841b4f50f0862060c1a3',
    'fa0d6e598d13e9220e5de48db4ed087c8f8847b3',
    '816dcddcb7949bd8556058b47e2f638b163d658d',
    '13d8602fa8dc916704194ec25bf73bffd6b39fed',
])
def test_reviewed_vault_morpho_implementation_is_public(implementation: str) -> None:
    vault = object.__new__(VaultMorphoVault)
    vault.web3 = Mock()
    vault.spec = Mock(vault_address=OWNER, chain_id=1)
    vault.web3.eth.chain_id = 1
    vault.default_block_identifier = 123
    vault.web3.eth.get_storage_at.return_value = bytes(12) + bytes.fromhex(implementation)
    assert vault.is_whitelisted_deposit() is False
    assert vault.is_permissionless(OWNER) is True
    assert vault.web3.eth.get_storage_at.call_args.kwargs['block_identifier'] == 123
    vault.web3.eth.get_storage_at.return_value = bytes(32)
    with pytest.raises(NotImplementedError, match='Unreviewed'):
        vault.is_permissionless(OWNER)


def test_public_outer_with_exit_gate_is_not_permissionless():
    vault = morpho()
    contract = Mock()
    contract.functions.canSendShares.return_value.call.return_value = False
    with patch.object(MorphoV2Vault, 'fetch_deposit_gates', return_value=(ZERO, ZERO)), patch.object(MorphoV2Vault, '_fetch_gate_address', return_value=OWNER), patch('eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract', return_value=contract):
        assert vault.is_whitelisted_deposit() is False
        assert vault.is_permissionless(OWNER) is False
        contract.functions.canSendShares.return_value.call.return_value = True
        with pytest.raises(NotImplementedError, match='despite owner admission'):
            vault.is_permissionless(OWNER)


def test_ungated_route_and_restricted_nested_route():
    vault = morpho()
    contract = Mock()
    contract.functions.liquidityAdapter.return_value.call.return_value = ZERO
    with patch.object(MorphoV2Vault, 'fetch_deposit_gates', return_value=(ZERO, ZERO)), patch.object(MorphoV2Vault, '_fetch_gate_address', return_value=ZERO), patch('eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract', return_value=contract):
        assert vault.is_permissionless(OWNER) is True
    contract.functions.liquidityAdapter.return_value.call.return_value = OWNER
    contract.functions.parentVault.return_value.call.return_value = OWNER
    vault.web3.eth.get_code.return_value = b"reviewed-test-bytecode"
    contract.functions.canReceiveShares.return_value.call.return_value = False
    vault.web3.eth.call.return_value = bytes(12) + bytes.fromhex(OWNER[2:])
    with patch('eth_defi.erc_4626.vault_protocol.morpho.vault_v2.REVIEWED_PERMISSION_ADAPTER_HASHES', {__import__('web3').Web3.keccak(b'reviewed-test-bytecode').hex().removeprefix('0x'): 'vault'}), patch.object(MorphoV2Vault, 'fetch_deposit_gates', side_effect=[(ZERO, ZERO), (OWNER, ZERO)]), patch.object(MorphoV2Vault, '_fetch_gate_address', return_value=ZERO), patch('eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract', return_value=contract):
        assert vault.is_permissionless(OWNER) is False


def test_admitted_owner_does_not_override_configured_deposit_policy():
    vault = morpho()
    with patch.object(MorphoV2Vault, 'fetch_deposit_gates', return_value=(OWNER, ZERO)), patch.object(MorphoV2Vault, 'is_account_whitelisted', return_value=True):
        with pytest.raises(NotImplementedError, match='despite owner admission'):
            vault.is_permissionless(OWNER)


def test_unreviewed_adapter_cannot_claim_permissionless_through_parent_getter():
    vault = morpho()
    contract = Mock()
    contract.functions.liquidityAdapter.return_value.call.return_value = OWNER
    contract.functions.parentVault.return_value.call.return_value = OWNER
    vault.web3.eth.get_code.return_value = b'unreviewed custom adapter'
    with patch.object(MorphoV2Vault, 'fetch_deposit_gates', return_value=(ZERO,ZERO)), patch.object(MorphoV2Vault, '_fetch_gate_address', return_value=ZERO), patch('eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract',return_value=contract), pytest.raises(NotImplementedError,match='bytecode'):
        vault.is_permissionless(OWNER)
