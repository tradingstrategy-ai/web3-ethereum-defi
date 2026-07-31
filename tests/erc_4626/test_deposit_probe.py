"""Unit tests for guarded vault-deposit probe selection and local state."""

import json
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hexbytes import HexBytes

import eth_defi.erc_4626.deposit_redeem as erc_4626_deposit_redeem
import eth_defi.vault.deposit_redeem as vault_deposit_redeem
from eth_defi.erc_4626 import deposit_probe
from eth_defi.erc_4626.deposit_probe import DEFAULT_STATUS_PATH, VaultDepositProbeCandidate, VaultDepositProbeOutput, fetch_max_deposit_guidance, log_probe_tables, merge_redemption_flow_failure, prepare_probe_deposit_request, require_simulation, run_from_environment, select_candidates, update_status
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.csigma.deposit_redeem import CSUPERIOR_V2_POOL_ADDRESS, CsigmaDepositManager
from eth_defi.erc_4626.vault_protocol.d2.vault import D2DepositManager
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager, GainsRedemptionTicket
from eth_defi.erc_4626.vault_protocol.kiln.vault import KilnVault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.erc_4626.vault_protocol.summer.vault import SummerVault
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import UnsupportedVaultSimulation, VaultDepositManagerCapability, VaultFlowUnavailable
from eth_defi.vault.vaultdb import VaultDatabase


def test_probe_actor_wallets_keep_governance_separate_from_guarded_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe must not accidentally exercise GuardV0's governance bypass."""
    created_addresses = iter(
        (
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
        )
    )
    funded_addresses: list[str] = []
    synced_addresses: list[str] = []

    class FakeAccount:
        """Create opaque signing-account sentinels."""

        @staticmethod
        def create() -> object:
            """Return one opaque account handle."""
            return object()

    class FakeWallet:
        """Capture Anvil wallet setup without creating private keys."""

        def __init__(self, _account: object) -> None:
            """Assign the next deterministic address."""
            self.address = next(created_addresses)

        def sync_nonce(self, _web3: object) -> None:
            """Record nonce synchronisation."""
            synced_addresses.append(self.address)

    def fake_set_balance(_web3: object, address: str, _amount: int) -> None:
        """Record the actor whose Anvil native balance was funded."""
        funded_addresses.append(address)

    monkeypatch.setattr(deposit_probe, "Account", FakeAccount)
    monkeypatch.setattr(deposit_probe, "HotWallet", FakeWallet)
    monkeypatch.setattr(deposit_probe, "set_balance", fake_set_balance)

    governance, asset_manager = deposit_probe._create_probe_actor_wallets(object())

    assert governance.address != asset_manager.address
    assert funded_addresses == [governance.address, asset_manager.address]
    assert synced_addresses == [governance.address, asset_manager.address]


def test_vault_deposit_manager_capability_exposes_directional_public_support() -> None:
    """The public JSON schema preserves directional and multi-asset support."""
    complete = VaultDepositManagerCapability(True, True, "synchronous", "asynchronous")
    assert complete.as_initial_public_schema() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "asynchronous",
    }
    assert VaultDepositManagerCapability(False, False).as_initial_public_schema() == {
        "can_deposit": False,
        "can_redeem": False,
    }
    assert VaultDepositManagerCapability(True, False, "synchronous", None).as_initial_public_schema() is None
    assert VaultDepositManagerCapability(
        True,
        False,
        "synchronous",
        None,
        deposit_assets=("0x0000000000000000000000000000000000000001",),
        publish_partial=True,
    ).as_initial_public_schema() == {
        "can_deposit": True,
        "can_redeem": False,
        "deposit_flow": "synchronous",
        "deposit_assets": ["0x0000000000000000000000000000000000000001"],
    }
    assert VaultDepositManagerCapability(False, True, None, "asynchronous").as_initial_public_schema() is None
    with pytest.raises(ValueError, match="deposit_flow"):
        VaultDepositManagerCapability(True, True, None, "asynchronous")
    with pytest.raises(ValueError, match="deposit_unsupported_reason"):
        VaultDepositManagerCapability(True, True, "synchronous", "synchronous", deposit_unsupported_reason="unsupported")
    with pytest.raises(ValueError, match="supports_anvil_settlement"):
        VaultDepositManagerCapability(True, True, "synchronous", "synchronous", supports_anvil_settlement=True)
    with pytest.raises(ValueError, match="anvil_settlement_unsupported_reason"):
        VaultDepositManagerCapability(True, True, "synchronous", "asynchronous", supports_anvil_settlement=False)
    with pytest.raises(ValueError, match="anvil_settlement_unsupported_reason"):
        VaultDepositManagerCapability(True, True, "synchronous", "asynchronous", anvil_settlement_unsupported_reason="reason")
    with pytest.raises(ValueError, match="deposit_assets"):
        VaultDepositManagerCapability(False, False, deposit_assets=("0x0000000000000000000000000000000000000001",))


def test_lagoon_capability_advertises_verified_anvil_settlement() -> None:
    """Lagoon advertises only the ticketed settlement driver it implements."""
    assert object.__new__(LagoonVault).get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "asynchronous",
        "redemption_flow": "asynchronous",
        "supports_anvil_settlement": True,
    }


#: Any cSigma deployment that is not the verified cSuperior V2 pool, so the
#: adapter takes its generic ``maxRedeem()`` capacity path.
NON_CSUPERIOR_VAULT_ADDRESS = "0x0000000000000000000000000000000000000002"


def test_csigma_redemption_preflight_preserves_raw_share_capacity() -> None:
    """cSigma exposes maxRedeem() as an amount-aware preflight result."""

    available_raw_shares = 45_388
    requested_raw_shares = 907_757

    class Call:
        """Minimal contract-call result."""

        @staticmethod
        def call() -> int:
            """Return fixed immediate redemption capacity."""
            return available_raw_shares

    class Functions:
        """Minimal cSigma contract namespace."""

        @staticmethod
        def maxRedeem(owner: str) -> Call:  # noqa: N802
            """Build an owner-specific capacity call."""
            assert owner == "0x0000000000000000000000000000000000000001"
            return Call()

    manager = object.__new__(CsigmaDepositManager)
    # The adapter selects its capacity strategy by vault address: only the
    # verified cSuperior V2 pool uses the withdrawal-manager path, while every
    # other cSigma deployment keeps the generic maxRedeem() behaviour. Stub a
    # non-cSuperior address so this test exercises that maxRedeem() branch, which
    # is what Functions above models.
    assert NON_CSUPERIOR_VAULT_ADDRESS != CSUPERIOR_V2_POOL_ADDRESS
    manager.vault = type(
        "Vault",
        (),
        {
            "address": NON_CSUPERIOR_VAULT_ADDRESS,
            "vault_contract": type("Contract", (), {"functions": Functions()})(),
        },
    )()

    available = manager.fetch_redemption_preflight("0x0000000000000000000000000000000000000001", available_raw_shares)
    assert available.available is True
    assert available.available_raw_shares == available_raw_shares

    zero = manager.fetch_redemption_preflight("0x0000000000000000000000000000000000000001", 0)
    assert zero.available is True
    assert zero.requested_raw_shares == 0

    unavailable = manager.fetch_redemption_preflight("0x0000000000000000000000000000000000000001", requested_raw_shares)
    assert unavailable.available is False
    assert unavailable.requested_raw_shares == requested_raw_shares
    assert unavailable.available_raw_shares == available_raw_shares
    assert unavailable.reason == "redemption_capacity_limited"


def test_erc4626_subclass_can_use_probe_generic_fallback_after_interface_check() -> None:
    """A reader subclass exposes generic support without public certification."""

    class ReaderOnlyVault(ERC4626Vault):
        pass

    class Call:
        def call(self):
            return "0x0000000000000000000000000000000000000001"

    class Functions:
        @staticmethod
        def asset():
            return Call()

        @staticmethod
        def maxDeposit(_owner):
            return Call()

        @staticmethod
        def maxRedeem(_owner):
            return Call()

    subclass = object.__new__(ReaderOnlyVault)
    subclass.vault_contract = type("Contract", (), {"functions": Functions()})()
    assert subclass.supports_generic_deposit_manager() is True
    assert subclass.get_deposit_manager_capability() is None


def test_successful_readers_are_synchronous_manager_certified() -> None:
    """Successful guarded deposit probes enable public manager metadata."""
    assert "eth_defi.erc_4626.vault_protocol.kiln.vault.KilnVault" in CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES
    assert "eth_defi.erc_4626.vault_protocol.summer.vault.SummerVault" in CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES
    assert "eth_defi.erc_4626.vault_protocol.yearn.vault.YearnV3Vault" in CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES
    assert object.__new__(KilnVault).get_deposit_manager_capability().as_initial_public_schema() is not None
    assert object.__new__(SummerVault).get_deposit_manager_capability().as_initial_public_schema() is not None
    assert object.__new__(YearnV3Vault).get_deposit_manager_capability().as_initial_public_schema() is not None


def test_morpho_readers_keep_callable_manager_without_lifecycle_certification() -> None:
    """Morpho managers remain callable but do not advertise full immediate redemption."""
    for vault_type in (MorphoV1Vault, MorphoV2Vault):
        class_name = f"{vault_type.__module__}.{vault_type.__qualname__}"
        vault = object.__new__(vault_type)
        assert class_name not in CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES
        assert vault.get_deposit_manager_capability() is None
        assert isinstance(vault.get_deposit_manager(), ERC4626DepositManager)


def test_max_deposit_guidance_is_reported_without_deciding_generic_support() -> None:
    """A zero ERC-4626 response remains guidance, not a closed-vault result."""

    class Call:
        def __init__(self, value: str | int):
            self.value = value

        def call(self) -> str | int:
            return self.value

    class Functions:
        @staticmethod
        def asset() -> Call:
            return Call("0x0000000000000000000000000000000000000001")

        @staticmethod
        def maxDeposit(_owner: str) -> Call:
            return Call(0)

    vault = object.__new__(ERC4626Vault)
    vault.vault_contract = type("Contract", (), {"functions": Functions()})()
    assert fetch_max_deposit_guidance(vault) == "0"
    assert vault.supports_generic_deposit_manager() is True


def test_generic_redemption_manager_accepts_raw_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe can redeem its exact minted share balance without a decimal read."""
    vault = object.__new__(ERC4626Vault)
    manager = ERC4626DepositManager(vault)
    function = object()
    monkeypatch.setattr(erc_4626_deposit_redeem, "redeem_4626", lambda *args, **kwargs: function)
    request = manager.create_redemption_request(
        owner="0x0000000000000000000000000000000000000001",
        raw_shares=123,
    )
    assert request.raw_shares == 123
    assert request.funcs == [function]


def test_guard_validation_request_requires_global_erc4626_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generic ERC-4626 validation accepts only an authoritative zero-cap closure."""
    vault = object.__new__(ERC4626Vault)
    vault.web3 = object()
    vault.spec = VaultSpec(chain_id=1, vault_address="0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(vault, "get_protocol_name", lambda: "Example")
    manager = ERC4626DepositManager(vault)
    owner = "0x0000000000000000000000000000000000000001"
    monkeypatch.setattr(manager, "_assert_anvil_guard_validation", lambda: None)
    monkeypatch.setattr(manager, "check_deposit_whitelist", lambda _owner: None)

    monkeypatch.setattr(vault, "fetch_deposit_closed_reason", lambda: None)
    with pytest.raises(UnsupportedVaultSimulation) as exc_info:
        manager.create_deposit_request_for_guard_validation(owner, raw_amount=123)

    assert exc_info.value.unsupported_reason == "closed_deposit_guard_validation_not_closed"

    monkeypatch.setattr(vault, "fetch_deposit_closed_reason", lambda: "Max deposit cap reached (maxDeposit=0)")
    with pytest.raises(VaultFlowUnavailable) as closed_exc_info:
        manager.create_deposit_request(owner=owner, raw_amount=123)

    assert closed_exc_info.value.preflight_result == "deposit_closed"
    assert closed_exc_info.value.available_raw_amount == 0

    observed: dict[str, object] = {}
    expected_request = object()

    def create_deposit_request(**kwargs: object) -> object:
        observed.update(kwargs)
        return expected_request

    monkeypatch.setattr(manager, "create_deposit_request", create_deposit_request)

    request = manager.create_deposit_request_for_guard_validation(owner, raw_amount=123)

    assert request is expected_request
    assert observed == {
        "owner": owner,
        "to": owner,
        "raw_amount": 123,
        "check_max_deposit": False,
        "check_enough_token": False,
    }


def test_d2_guard_validation_request_bypasses_only_the_closed_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2 generates GuardV0 calldata without opening its funding epoch.

    This verifies the dedicated method rather than a general bypass flag. The
    inherited request builder continues to enforce D2 account admission; only
    the temporary D2 epoch/capacity/balance gates are omitted for a
    non-broadcast GuardV0 policy check.
    """
    manager = object.__new__(D2DepositManager)
    owner = "0x0000000000000000000000000000000000000001"
    observed: dict[str, object] = {}
    expected_request = object()

    def parent_create_deposit_request(self: ERC4626DepositManager, **kwargs: object) -> object:
        assert self is manager
        observed.update(kwargs)
        return expected_request

    monkeypatch.setattr(ERC4626DepositManager, "create_deposit_request", parent_create_deposit_request)
    monkeypatch.setattr(manager, "_assert_anvil_guard_validation", lambda: None)

    request = manager.create_deposit_request_for_guard_validation(owner, raw_amount=123)

    assert request is expected_request
    assert observed == {
        "owner": owner,
        "to": owner,
        "raw_amount": 123,
        "check_max_deposit": False,
        "check_enough_token": False,
    }


def test_guard_validation_request_rejects_non_anvil_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The temporary-check bypass is inaccessible outside an Anvil fork."""
    vault = object.__new__(ERC4626Vault)
    vault.web3 = object()
    vault.spec = VaultSpec(chain_id=1, vault_address="0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(vault, "get_protocol_name", lambda: "Example")
    manager = ERC4626DepositManager(vault)
    monkeypatch.setattr(vault_deposit_redeem, "is_anvil", lambda _web3: False)

    with pytest.raises(UnsupportedVaultSimulation) as exc_info:
        manager.create_deposit_request_for_guard_validation(
            "0x0000000000000000000000000000000000000002",
            raw_amount=123,
        )

    assert exc_info.value.unsupported_reason == "anvil_provider_required"
    assert exc_info.value.direction == "deposit"
    assert exc_info.value.phase == "guard_validation"


def test_probe_records_preflight_refusal_without_aborting() -> None:
    """A manager policy denial becomes a per-vault probe result."""

    class RefusingManager:
        """Minimal manager that rejects before creating a transaction."""

        @staticmethod
        def create_deposit_request(**_kwargs):
            reason = "Account is not whitelisted"
            raise VaultFlowUnavailable(
                reason,
                protocol="Example",
                direction="deposit",
                phase="preflight",
                decoded_error="NotWhitelisted",
                preflight_result="whitelisting-needed",
                function_selector=HexBytes("0x85b77f45"),
                error_selector=HexBytes("0x584a7938"),
            )

    capability = {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "asynchronous",
        "redemption_flow": "asynchronous",
    }
    request, failure = prepare_probe_deposit_request(
        RefusingManager(),
        "0x0000000000000000000000000000000000000001",
        1,
        capability,
        "not available",
    )

    assert request is None
    assert failure is not None
    assert failure["outcome"] == "flow_unavailable"
    assert failure["deposit_manager"] == capability
    assert failure["flow_error"] == {
        "protocol": "Example",
        "direction": "deposit",
        "phase": "preflight",
        "decoded_error": "NotWhitelisted",
        "preflight_result": "whitelisting-needed",
        "function_selector": "85b77f45",
        "error_selector": "584a7938",
        "access_delay": None,
    }


def test_probe_preserves_successful_deposit_when_redemption_is_unavailable() -> None:
    """An immediate redemption delay must not erase successful deposit evidence."""
    result = {
        "outcome": "success",
        "message": None,
        "deposit_manager": {
            "can_deposit": True,
            "can_redeem": True,
            "deposit_flow": "synchronous",
            "redemption_flow": "synchronous",
        },
        "minted_share_amount_raw": "123",
    }
    error = VaultFlowUnavailable(
        "IPOR redemption is temporarily locked",
        protocol="IPOR",
        direction="redeem",
        phase="preflight",
        access_delay=3600,
    )

    merged = merge_redemption_flow_failure(result, error)

    assert merged["outcome"] == "success"
    assert merged["minted_share_amount_raw"] == "123"
    assert merged["redemption_status_detail"] == "flow_unavailable"
    assert merged["redemption_message"] == ("IPOR redemption is temporarily locked (protocol=IPOR, direction=redeem, phase=preflight, access_delay=3600)")
    assert merged["redemption_flow_error"] == {
        "protocol": "IPOR",
        "direction": "redeem",
        "phase": "preflight",
        "decoded_error": None,
        "preflight_result": None,
        "function_selector": None,
        "error_selector": None,
        "access_delay": 3600,
    }


def test_probe_requires_explicit_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The script refuses before any provider can be constructed."""
    monkeypatch.delenv("SIMULATE", raising=False)
    with pytest.raises(RuntimeError, match="SIMULATE=true"):
        require_simulation()
    monkeypatch.setenv("SIMULATE", "false")
    with pytest.raises(RuntimeError, match="SIMULATE=true"):
        require_simulation()
    monkeypatch.setenv("SIMULATE", "true")
    require_simulation()


def test_default_probe_status_is_a_packaged_data_artifact() -> None:
    """The release snapshot is not sourced from an operator home directory."""
    assert DEFAULT_STATUS_PATH.name == "vault-deposit-status.json"
    assert DEFAULT_STATUS_PATH.parent.name == "deposit-status"
    assert DEFAULT_STATUS_PATH.is_file()


def test_select_candidates_deduplicates_explicit_ids_and_requires_capability() -> None:
    """Explicit candidate order is stable and unsupported rows are excluded."""
    first = VaultSpec(8453, "0x0000000000000000000000000000000000000001")
    second = VaultSpec(8453, "0x0000000000000000000000000000000000000002")
    token = "0x0000000000000000000000000000000000000010"
    database = VaultDatabase(
        rows={
            first: {
                "NAV": Decimal("100"),
                "Protocol": "Example",
                "_denomination_token": {"address": token},
                "_deposit_manager": {"can_deposit": True, "can_redeem": True},
            },
            second: {
                "NAV": Decimal("100"),
                "Protocol": "Example",
                "_denomination_token": {"address": token},
                "_deposit_manager": None,
            },
        },
    )
    candidates = select_candidates(
        database,
        selection="vault_ids",
        vault_ids=f"{first.as_string_id()},{first.as_string_id()},{second.as_string_id()}",
    )
    assert [candidate.spec for candidate in candidates] == [first]


def test_select_candidates_rejects_unknown_explicit_ids() -> None:
    """An explicit typo must not degrade into a partial or empty probe run."""
    missing = VaultSpec(8453, "0x0000000000000000000000000000000000000001")
    with pytest.raises(ValueError, match="missing from the vault database"):
        select_candidates(VaultDatabase(rows={}), selection="vault_ids", vault_ids=missing.as_string_id())


def test_protocol_candidates_are_ranked_by_nav_and_chain_filter() -> None:
    """Protocol batches choose the largest same-chain vaults before truncation."""
    token = "0x0000000000000000000000000000000000000010"
    arbitrum_small = VaultSpec(42161, "0x0000000000000000000000000000000000000001")
    arbitrum_large = VaultSpec(42161, "0x0000000000000000000000000000000000000002")
    base_largest = VaultSpec(8453, "0x0000000000000000000000000000000000000003")
    database = VaultDatabase(
        rows={
            arbitrum_small: {"NAV": Decimal("10"), "Protocol": "Example", "_denomination_token": {"address": token}, "_deposit_manager": {"can_deposit": True}},
            arbitrum_large: {"NAV": Decimal("100"), "Protocol": "Example", "_denomination_token": {"address": token}, "_deposit_manager": {"can_deposit": True}},
            base_largest: {"NAV": Decimal("1000"), "Protocol": "Example", "_denomination_token": {"address": token}, "_deposit_manager": {"can_deposit": True}},
        },
    )
    candidates = select_candidates(database, selection="protocol", protocol="example", chain_id=42161)
    assert [candidate.spec for candidate in candidates] == [arbitrum_large, arbitrum_small]


def test_min_tvl_uses_usd_nav_without_requiring_one_denomination_token() -> None:
    """USD NAV selection includes qualifying vaults with different assets."""
    usdc_vault = VaultSpec(42161, "0x0000000000000000000000000000000000000001")
    weth_vault = VaultSpec(42161, "0x0000000000000000000000000000000000000002")
    database = VaultDatabase(
        rows={
            usdc_vault: {"NAV": Decimal("100"), "Protocol": "Example", "_denomination_token": {"address": "0x0000000000000000000000000000000000000010"}, "_deposit_manager": {"can_deposit": True}},
            weth_vault: {"NAV": Decimal("200"), "Protocol": "Example", "_denomination_token": {"address": "0x0000000000000000000000000000000000000020"}, "_deposit_manager": {"can_deposit": True}},
        },
    )
    candidates = select_candidates(database, selection="min_tvl", min_tvl=Decimal("100"))
    assert {candidate.spec for candidate in candidates} == {usdc_vault, weth_vault}


def test_candidate_displays_denomination_symbol_and_address() -> None:
    """Probe output identifies the denomination token without a holder map."""
    candidate = VaultDepositProbeCandidate(
        VaultSpec(42161, "0x0000000000000000000000000000000000000001"),
        {"Denomination": "USDC"},
        "0x0000000000000000000000000000000000000010",
    )
    assert candidate.denomination_token_label == "USDC (0x0000000000000000000000000000000000000010)"


def test_uncertified_legacy_rows_require_explicit_probe_opt_in() -> None:
    """Old databases can be probed without becoming public capability metadata."""
    spec = VaultSpec(42161, "0x0000000000000000000000000000000000000001")
    database = VaultDatabase(
        rows={
            spec: {
                "NAV": Decimal("100"),
                "Protocol": "Example",
                "_denomination_token": {"address": "0x0000000000000000000000000000000000000010"},
                "_deposit_manager": None,
            },
        },
    )
    assert select_candidates(database, selection="protocol", protocol="example") == []
    assert len(select_candidates(database, selection="protocol", protocol="example", include_uncertified=True)) == 1


def test_all_protocols_limits_each_protocol_by_top_nav() -> None:
    """All-protocol batches apply their limit independently, not globally."""
    token = "0x0000000000000000000000000000000000000010"
    first = VaultSpec(42161, "0x0000000000000000000000000000000000000001")
    second = VaultSpec(42161, "0x0000000000000000000000000000000000000002")
    third = VaultSpec(42161, "0x0000000000000000000000000000000000000003")
    database = VaultDatabase(
        rows={
            first: {"NAV": Decimal("1"), "Protocol": "Alpha", "_denomination_token": {"address": token}, "_deposit_manager": {"can_deposit": True}},
            second: {"NAV": Decimal("2"), "Protocol": "Alpha", "_denomination_token": {"address": token}, "_deposit_manager": {"can_deposit": True}},
            third: {"NAV": Decimal("3"), "Protocol": "Beta", "_denomination_token": {"address": token}, "_deposit_manager": {"can_deposit": True}},
        },
    )
    candidates = select_candidates(database, selection="all_protocols", max_per_protocol=1)
    assert [candidate.spec for candidate in candidates] == [second, third]


def test_probe_status_is_atomic_and_never_requires_transaction_hashes(tmp_path) -> None:
    """Persistent status keeps bounded history without stale attempt fields."""
    path = tmp_path / "vault-deposit-status.json"
    key = "8453-0x0000000000000000000000000000000000000001"
    address = key.split("-", 1)[1]
    update_status(path, key, {"chain_id": 8453, "address": address, "outcome": "success", "fork_block_number": 123, "execution_mode": "guarded", "minted_share_amount_raw": "10"})
    update_status(path, key, {"chain_id": 8453, "address": address, "outcome": "reverted", "revert_reason": "paused"})
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert data["vaults"][key]["outcome"] == "reverted"
    assert "execution_mode" not in data["vaults"][key]
    assert "minted_share_amount_raw" not in data["vaults"][key]
    assert data["vaults"][key]["attempt_count"] == len(data["vaults"][key]["history"]) + 1
    assert data["vaults"][key]["history"][0]["outcome"] == "success"
    assert "transaction_hash" not in data["vaults"][key]
    update_status(path, key, {"chain_id": 8453, "address": address, "outcome": "success", "fork_block_number": 124})
    data = json.loads(path.read_text())
    assert data["vaults"][key]["outcome"] == "success"
    assert "revert_reason" not in data["vaults"][key]


def test_successful_probe_status_requires_fork_block(tmp_path) -> None:
    """A success without reproducible fork evidence fails closed."""
    with pytest.raises(ValueError, match="fork_block_number"):
        update_status(tmp_path / "status.json", "8453-0x1", {"outcome": "success", "fork_block_number": None})


def test_invalid_denomination_filter_is_rejected_before_database_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed token filter cannot silently broaden selection."""
    monkeypatch.setenv("SIMULATE", "true")
    monkeypatch.setenv("DENOMINATION_TOKEN", "not-an-address")
    with pytest.raises(ValueError, match="DENOMINATION_TOKEN is not a valid address"):
        run_from_environment()


def test_probe_logs_detailed_and_summary_tables(caplog: pytest.LogCaptureFixture) -> None:
    """Terminal output shows every vault result and aggregate outcome counts."""
    caplog.set_level(logging.INFO, logger="eth_defi.erc_4626.deposit_probe")
    log_probe_tables(
        [
            VaultDepositProbeOutput("Example", "0x0000000000000000000000000000000000000002", "Failed vault", "USDC (0x0000000000000000000000000000000000000010)", "reverted", "reverted", "0", "Vault is paused", "execution reverted: paused"),
            VaultDepositProbeOutput("Example", "0x0000000000000000000000000000000000000001", "Successful vault", "USDC (0x0000000000000000000000000000000000000010)", "success", "Ok (generic ERC-4626)", "0", None, None),
        ]
    )
    output = caplog.text
    assert "Protocol" in output
    assert "Name" in output
    assert "Denomination token" in output
    assert "maxDeposit guidance" in output
    assert "Failure reason" in output
    assert "Revert reason" in output
    assert "Ok (generic ERC-4626)" in output
    assert "Vault deposit probe summary" in output
    assert "Ok" in output
    assert output.index("Successful vault") < output.index("Failed vault")
    assert "success" in output
    assert "reverted" in output


def test_broken_rescan_clears_retained_deposit_manager_capability() -> None:
    """A preserved descriptive row must not retain stale adapter certification."""
    spec = VaultSpec(8453, "0x0000000000000000000000000000000000000001")
    database = VaultDatabase(
        rows={
            spec: {
                "Name": "Healthy vault",
                "Denomination": "USDC",
                "NAV": Decimal("100"),
                "_deposit_manager": {"can_deposit": True, "can_redeem": True},
            },
        },
    )
    database._merge_rows({spec: {"Name": "<broken: TimeoutError>", "Denomination": "", "_deposit_manager": None}})
    assert database.rows[spec]["Name"] == "Healthy vault"
    assert database.rows[spec]["_deposit_manager"] is None


def test_gains_redemption_ticket_survives_json_round_trip() -> None:
    """The epoch information required for a later redeem is persistent."""
    manager = GainsDepositManager.__new__(GainsDepositManager)
    ticket = GainsRedemptionTicket(
        vault_address="0x0000000000000000000000000000000000000001",
        owner="0x0000000000000000000000000000000000000002",
        to="0x0000000000000000000000000000000000000003",
        raw_shares=10**30,
        tx_hash=HexBytes("0x" + "11" * 32),
        current_epoch=123,
        unlock_epoch=124,
    )
    restored = manager.reconstruct_redemption_ticket(json.loads(json.dumps(manager.serialize_redemption_ticket(ticket))))
    assert restored == ticket


def test_gains_finish_redemption_uses_erc4626_receiver_then_owner() -> None:
    """Gains claims must preserve the ERC-4626 redeem argument order."""
    redeem = Mock(return_value=object())
    manager = GainsDepositManager.__new__(GainsDepositManager)
    manager.vault = SimpleNamespace(vault_contract=SimpleNamespace(functions=SimpleNamespace(redeem=redeem)))
    ticket = GainsRedemptionTicket(
        vault_address="0x0000000000000000000000000000000000000001",
        owner="0x0000000000000000000000000000000000000002",
        to="0x0000000000000000000000000000000000000003",
        raw_shares=10,
        tx_hash=HexBytes("0x" + "11" * 32),
        current_epoch=123,
        unlock_epoch=124,
    )

    manager.finish_redemption(ticket)

    redeem.assert_called_once_with(ticket.raw_shares, ticket.to, ticket.owner)
