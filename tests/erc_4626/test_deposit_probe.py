"""Unit tests for guarded vault-deposit probe selection and local state."""

import json
import logging
from decimal import Decimal

import pytest
from hexbytes import HexBytes

import eth_defi.erc_4626.deposit_redeem as erc_4626_deposit_redeem
from eth_defi.erc_4626.deposit_probe import DEFAULT_STATUS_PATH, VaultDepositProbeCandidate, VaultDepositProbeOutput, fetch_max_deposit_guidance, log_probe_tables, require_simulation, run_from_environment, select_candidates, update_status
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES, ERC4626Vault
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager, GainsRedemptionTicket
from eth_defi.erc_4626.vault_protocol.kiln.vault import KilnVault
from eth_defi.erc_4626.vault_protocol.summer.vault import SummerVault
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositManagerCapability
from eth_defi.vault.vaultdb import VaultDatabase


def test_vault_deposit_manager_capability_suppresses_partial_public_support() -> None:
    """The initial JSON schema advertises only symmetric manager support."""
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
    assert VaultDepositManagerCapability(False, True, None, "asynchronous").as_initial_public_schema() is None
    with pytest.raises(ValueError, match="deposit_flow"):
        VaultDepositManagerCapability(True, True, None, "asynchronous")


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
