"""Policy checks preserve outer deposit semantics and reject unproven routes."""

from unittest.mock import Mock, patch
import pytest
from web3 import Web3
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.erc_4626.vault_protocol.vault_morpho.vault import VaultMorphoVault
from eth_defi.vault.exception import UnsupportedVaultProtocol
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.vault.deposit_redeem import VaultDepositPermission

OWNER = "0x1111111111111111111111111111111111111111"
ZERO = "0x" + "00" * 20


def morpho() -> MorphoV2Vault:
    vault = object.__new__(MorphoV2Vault)
    vault.web3 = Mock()
    vault.web3.eth.chain_id = 1
    vault.spec = Mock(vault_address=OWNER, chain_id=1)
    vault.default_block_identifier = 123
    return vault


@pytest.mark.parametrize(
    "implementation",
    [
        "f392a5b96d3248440bb4841b4f50f0862060c1a3",
        "fa0d6e598d13e9220e5de48db4ed087c8f8847b3",
        "816dcddcb7949bd8556058b47e2f638b163d658d",
        "13d8602fa8dc916704194ec25bf73bffd6b39fed",
    ],
)
def test_vault_morpho_implementations_are_all_unsupported(implementation: str) -> None:
    vault = object.__new__(VaultMorphoVault)
    vault.web3 = Mock()
    vault.spec = Mock(vault_address=OWNER, chain_id=1)
    assert fetch_deposit_permission(vault) is VaultDepositPermission.unsupported_protocol
    with pytest.raises(UnsupportedVaultProtocol):
        vault.is_permissionless(OWNER)
    vault.web3.eth.get_storage_at.assert_not_called()


def test_public_outer_with_exit_gate_is_not_permissionless():
    vault = morpho()
    contract = Mock()
    contract.functions.sendSharesGate.return_value.call.return_value = ZERO
    contract.functions.receiveAssetsGate.return_value.call.return_value = ZERO
    contract.functions.sendSharesGate.return_value.call.return_value = OWNER
    contract.functions.canSendShares.return_value.call.return_value = False
    with patch.object(MorphoV2Vault, "fetch_deposit_gates", return_value=(ZERO, ZERO)), patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract", return_value=contract):
        assert vault.is_whitelisted_deposit() is False
        assert vault.is_permissionless(OWNER) is False
        contract.functions.canSendShares.return_value.call.return_value = True
        with pytest.raises(NotImplementedError, match="despite owner admission"):
            vault.is_permissionless(OWNER)


def test_ungated_route_and_restricted_nested_route():
    vault = morpho()
    contract = Mock()
    contract.functions.sendSharesGate.return_value.call.return_value = ZERO
    contract.functions.receiveAssetsGate.return_value.call.return_value = ZERO
    contract.functions.liquidityAdapter.return_value.call.return_value = ZERO
    with patch.object(MorphoV2Vault, "fetch_deposit_gates", return_value=(ZERO, ZERO)), patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract", return_value=contract):
        assert vault.is_permissionless(OWNER) is True
    contract.functions.liquidityAdapter.return_value.call.return_value = OWNER
    contract.functions.parentVault.return_value.call.return_value = OWNER
    vault.web3.eth.get_code.return_value = b"reviewed-test-bytecode"
    contract.functions.canReceiveShares.return_value.call.return_value = False
    contract.functions.morphoVaultV1.return_value.call.return_value = OWNER
    with patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.REVIEWED_PERMISSION_ADAPTER_HASHES", {Web3.keccak(b"reviewed-test-bytecode").hex().removeprefix("0x"): "vault"}), patch.object(MorphoV2Vault, "fetch_deposit_gates", side_effect=[(ZERO, ZERO), (OWNER, ZERO)]), patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract", return_value=contract):
        assert vault.is_permissionless(OWNER) is False


def test_admitted_owner_does_not_override_configured_deposit_policy():
    vault = morpho()
    with patch.object(MorphoV2Vault, "fetch_deposit_gates", return_value=(OWNER, ZERO)), patch.object(MorphoV2Vault, "is_account_whitelisted", return_value=True):
        with pytest.raises(NotImplementedError, match="despite owner admission"):
            vault.is_permissionless(OWNER)


def test_unreviewed_adapter_cannot_claim_permissionless_through_parent_getter():
    vault = morpho()
    contract = Mock()
    contract.functions.sendSharesGate.return_value.call.return_value = ZERO
    contract.functions.receiveAssetsGate.return_value.call.return_value = ZERO
    contract.functions.liquidityAdapter.return_value.call.return_value = OWNER
    contract.functions.parentVault.return_value.call.return_value = OWNER
    vault.web3.eth.get_code.return_value = b"unreviewed custom adapter"
    with patch.object(MorphoV2Vault, "fetch_deposit_gates", return_value=(ZERO, ZERO)), patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract", return_value=contract), pytest.raises(NotImplementedError, match="bytecode"):
        vault.is_permissionless(OWNER)


def test_reviewed_market_requires_its_parent_and_no_getter_fallback():
    vault = morpho()
    contract = Mock()
    contract.functions.liquidityAdapter.return_value.call.return_value = OWNER
    contract.functions.parentVault.return_value.call.return_value = OWNER
    vault.web3.eth.get_code.return_value = b"reviewed market"
    digest = Web3.keccak(b"reviewed market").hex().removeprefix("0x")
    with patch.object(MorphoV2Vault, "_is_ungated", return_value=True), patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract", return_value=contract), patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.REVIEWED_PERMISSION_ADAPTER_HASHES", {digest: "market"}):
        assert vault.is_permissionless(OWNER) is True
        contract.functions.parentVault.return_value.call.return_value = ZERO
        with pytest.raises(NotImplementedError, match="parent"):
            vault.is_permissionless(OWNER)
        contract.functions.parentVault.return_value.call.return_value = OWNER
        contract.functions.morphoVaultV1.return_value.call.return_value = ZERO
        with patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.REVIEWED_PERMISSION_ADAPTER_HASHES", {digest: "vault"}):
            with pytest.raises(NotImplementedError, match="inner vault"):
                vault.is_permissionless(OWNER)


@pytest.mark.parametrize("chain", [1, 8453, 999])
def test_vault_morpho_unsupported_is_independent_of_chain_and_rpc(chain: int) -> None:
    vault = object.__new__(VaultMorphoVault)
    vault.web3 = Mock()
    vault.spec = Mock(vault_address=OWNER, chain_id=chain)
    assert fetch_deposit_permission(vault) is VaultDepositPermission.unsupported_protocol
    with pytest.raises(UnsupportedVaultProtocol):
        vault.get_deposit_manager().can_create_deposit_request(OWNER)
    with pytest.raises(UnsupportedVaultProtocol):
        vault.get_deposit_manager().fetch_depositable_raw_assets(OWNER)
    with pytest.raises(UnsupportedVaultProtocol):
        vault.get_deposit_manager().create_deposit_request(OWNER, raw_amount=100, check_max_deposit=False, check_enough_token=False)
    assert vault.get_deposit_manager().can_create_redemption_request(OWNER) is True
    vault.web3.eth.get_storage_at.assert_not_called()


def test_scan_failure_keeps_unsupported_protocol_status() -> None:
    import datetime
    from types import SimpleNamespace
    from eth_defi.erc_4626.scan import create_vault_scan_record

    adapter = Mock()
    adapter.unsupported_protocol_reason = "Unsupported integration"
    adapter.get_fee_data.return_value = object()  # Force the existing broken-row fallback.
    detection = SimpleNamespace(address=OWNER, features=set(), first_seen_at=datetime.datetime(2026, 1, 1))
    with patch("eth_defi.erc_4626.scan.create_vault_instance", return_value=adapter):
        row = create_vault_scan_record(Mock(), detection, 123, token_cache={})
    assert row["_deposit_permission"] == "unsupported_protocol"


@pytest.mark.parametrize("inner_policy", ["ungated", "restricted", "exit_restricted", "nested"])
def test_route_keeps_one_block_and_uses_adapter_as_inner_owner(inner_policy: str) -> None:
    """Refactoring must preserve the observation block and each leg's custody role.

    1. Set up distinct outer, adapter and inner addresses at a resolved latest block.
    2. Inspect an ungated, restricted or further-nested inner route.
    3. Verify every RPC uses that block and inner admission uses the adapter.
    """

    # 1. Distinct identities catch accidental reuse of the outer custody owner.
    vault = morpho()
    vault.default_block_identifier = "latest"
    vault.web3.eth.block_number = 456
    adapter_address = "0x2222222222222222222222222222222222222222"
    inner_address = "0x3333333333333333333333333333333333333333"
    outer, adapter, inner = Mock(), Mock(), Mock()
    outer.functions.liquidityAdapter.return_value.call.return_value = adapter_address
    adapter.functions.parentVault.return_value.call.return_value = OWNER
    adapter.functions.morphoVaultV1.return_value.call.return_value = inner_address
    inner.functions.liquidityAdapter.return_value.call.return_value = adapter_address if inner_policy == "nested" else ZERO
    inner.functions.canReceiveShares.return_value.call.return_value = False
    for contract in (outer, inner):
        contract.functions.sendSharesGate.return_value.call.return_value = ZERO
        contract.functions.receiveAssetsGate.return_value.call.return_value = ZERO
    if inner_policy == "exit_restricted":
        inner.functions.sendSharesGate.return_value.call.return_value = OWNER
        inner.functions.canSendShares.return_value.call.return_value = False
    vault.web3.eth.get_code.return_value = b"reviewed vault adapter"
    digest = Web3.keccak(b"reviewed vault adapter").hex().removeprefix("0x")
    contracts = {OWNER: outer, adapter_address: adapter, inner_address: inner}
    gates = [(ZERO, ZERO), (OWNER, ZERO) if inner_policy == "restricted" else (ZERO, ZERO)]

    # 2. Further nesting remains unresolved; a refused inner owner remains restricted.
    with (
        patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.get_deployed_contract", side_effect=lambda web3, abi, address: contracts[address]),
        patch("eth_defi.erc_4626.vault_protocol.morpho.vault_v2.REVIEWED_PERMISSION_ADAPTER_HASHES", {digest: "vault"}),
        patch.object(MorphoV2Vault, "fetch_deposit_gates", side_effect=gates) as fetch_gates,
    ):
        if inner_policy == "nested":
            with pytest.raises(NotImplementedError, match="Further nested"):
                vault.is_permissionless(OWNER)
        else:
            assert vault.is_permissionless(OWNER) is (inner_policy == "ungated")

    # 3. No route read may drift back to latest or substitute the Safe on the inner leg.
    assert all(call.args == (456,) for call in fetch_gates.call_args_list)
    vault.web3.eth.get_code.assert_called_once_with(adapter_address, block_identifier=456)
    rpc_calls = [call for contract in contracts.values() for call in contract.mock_calls if call[0].endswith(".call")]
    assert rpc_calls and all(call.kwargs == {"block_identifier": 456} for call in rpc_calls)
    if inner_policy == "restricted":
        inner.functions.canReceiveShares.assert_called_once_with(adapter_address)
    elif inner_policy == "exit_restricted":
        inner.functions.canSendShares.assert_called_once_with(adapter_address)
