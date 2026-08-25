"""Test Enzyme Blue direct discovery and historical accounting."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_abi import encode
from web3 import Web3

from eth_defi.enzyme import blue_vault
from eth_defi.enzyme.blue_discovery import ENZYME_BLUE_DEPLOYMENTS, EnzymeBlueVaultFactoryCandidate, decode_enzyme_blue_vault_deployed_event, fetch_enzyme_blue_dispatchers_for_chain
from eth_defi.enzyme.blue_historical import EnzymeBlueVaultHistoricalReader
from eth_defi.enzyme.blue_vault import ALLOWED_DEPOSIT_RECIPIENTS_POLICY_IDENTIFIER, ENZYME_BLUE_LEGACY_POLICY_MANAGERS, FEE_BPS_DENOMINATOR, MANAGEMENT_FEE_RATE_SCALE, SECONDS_PER_YEAR, EnzymeBlueVault
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import _prepare_probe_leads, create_enzyme_blue_factory_detection, create_enzyme_blue_potential_vault_match  # noqa: PLC2701
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission

CHAIN_ID = 1
VAULT = "0x000000000000000000000000000000000000bEEF"
ACCESSOR = "0x000000000000000000000000000000000000cAFE"
FUND_DEPLOYER = "0x000000000000000000000000000000000000dEaD"
EXPECTED_TOTAL_MANAGEMENT_FEE = 0.015
EXPECTED_PROTOCOL_FEE = 0.005
EXPECTED_PERFORMANCE_FEE = 0.2
EXPECTED_ENTRANCE_FEE = 0.0025
EXPECTED_EXIT_FEE = 0.005


def test_blue_dispatcher_is_configured_for_all_supported_chains() -> None:
    """Use reviewed persistent Dispatcher addresses for Blue discovery."""

    assert set(ENZYME_BLUE_DEPLOYMENTS) == {1, 137, 8453, 42161}
    assert fetch_enzyme_blue_dispatchers_for_chain(CHAIN_ID) == [ENZYME_BLUE_DEPLOYMENTS[CHAIN_ID].dispatcher]


def test_blue_deployment_event_decodes_both_vault_contracts() -> None:
    """Retain the canonical VaultProxy and its ComptrollerProxy accessor."""

    data = encode(["address", "address", "string"], [VAULT, ACCESSOR, "Example fund"])
    vault, accessor, name = decode_enzyme_blue_vault_deployed_event({"address": ENZYME_BLUE_DEPLOYMENTS[CHAIN_ID].dispatcher, "data": data.hex()})

    assert (vault.lower(), accessor.lower(), name) == (VAULT.lower(), ACCESSOR.lower(), "Example fund")


def test_blue_factory_lead_bypasses_generic_erc4626_probe() -> None:
    """Blue is a non-ERC-4626 protocol confirmed by its Dispatcher event."""

    candidate = EnzymeBlueVaultFactoryCandidate(
        chain=CHAIN_ID,
        address=VAULT,
        dispatcher_address=ENZYME_BLUE_DEPLOYMENTS[CHAIN_ID].dispatcher,
        fund_deployer=FUND_DEPLOYER,
        vault_accessor=ACCESSOR,
        fund_name="Example fund",
        created_block=11_632_494,
        created_at=datetime.datetime(2026, 8, 20),  # noqa: DTZ001
        transaction_hash="0xdead",
        log_index=0,
    )
    lead = create_enzyme_blue_potential_vault_match(candidate)
    addresses, _leads, count = _prepare_probe_leads({VAULT: lead})

    assert addresses == []
    assert count == 1
    assert create_enzyme_blue_factory_detection(candidate).features == {ERC4626Feature.enzyme_blue_like}


@pytest.mark.parametrize(
    ("chain_id", "network"),
    [(1, "ethereum"), (137, "polygon"), (8453, "base"), (42161, "arbitrum")],
)
def test_blue_link_opens_address_specific_enzyme_page(chain_id: int, network: str) -> None:
    """Link every supported Blue deployment directly to its vault detail page."""

    vault = EnzymeBlueVault.__new__(EnzymeBlueVault)
    vault.spec = VaultSpec(chain_id, VAULT)

    assert vault.get_link() == f"https://app.enzyme.finance/vault/{Web3.to_checksum_address(VAULT)}?network={network}"


def test_blue_historical_reader_derives_price_and_tvl() -> None:
    """Calculate denomination-token TVL and share price from GAV/supply."""

    updates = []
    reader = EnzymeBlueVaultHistoricalReader.__new__(EnzymeBlueVaultHistoricalReader)
    reader.vault = SimpleNamespace(address=VAULT, denomination_token=SimpleNamespace(decimals=6), share_token=SimpleNamespace(decimals=18))
    reader.reader_state = SimpleNamespace(on_called=lambda result, total_assets, share_price: updates.append((result, total_assets, share_price)))
    gav_call = EncodedCall(func_name="calcGav", address=ACCESSOR, data=b"", extra_data={"function": "gav"})
    supply_call = EncodedCall(func_name="totalSupply", address=VAULT, data=b"", extra_data={"function": "total_supply"})

    result = reader.process_result(
        123,
        datetime.datetime(2026, 8, 20),  # noqa: DTZ001
        [
            EncodedCallResult(call=gav_call, success=True, result=(50_000_000).to_bytes(32, "big"), block_identifier=123),
            EncodedCallResult(call=supply_call, success=True, result=(40 * 10**18).to_bytes(32, "big"), block_identifier=123),
        ],
    )

    assert result.total_assets == Decimal("50")
    assert result.total_supply == Decimal("40")
    assert result.share_price == Decimal("1.25")
    assert len(updates) == 1


def test_blue_historical_reader_rejects_zero_gav_with_outstanding_shares() -> None:
    """Do not publish an unavailable historic Blue valuation as a full loss."""

    unpriced_updates = []
    reader = EnzymeBlueVaultHistoricalReader.__new__(EnzymeBlueVaultHistoricalReader)
    reader.vault = SimpleNamespace(address=VAULT, denomination_token=SimpleNamespace(decimals=6), share_token=SimpleNamespace(decimals=18))
    reader.reader_state = SimpleNamespace(on_called=pytest.fail, on_unpriced_call=unpriced_updates.append)
    gav_call = EncodedCall(func_name="calcGav", address=ACCESSOR, data=b"", extra_data={"function": "gav"})
    supply_call = EncodedCall(func_name="totalSupply", address=VAULT, data=b"", extra_data={"function": "total_supply"})

    result = reader.process_result(
        123,
        datetime.datetime(2026, 8, 20),  # noqa: DTZ001
        [
            EncodedCallResult(call=gav_call, success=True, result=(0).to_bytes(32, "big"), block_identifier=123),
            EncodedCallResult(call=supply_call, success=True, result=(40 * 10**18).to_bytes(32, "big"), block_identifier=123),
        ],
    )

    assert result.share_price is None
    assert result.total_assets is None
    assert result.total_supply == Decimal("40")
    assert result.errors == ["Enzyme Blue GAV is zero while share supply is positive; price is unavailable"]
    assert len(unpriced_updates) == 1
    assert unpriced_updates[0].call is gav_call


@pytest.mark.parametrize(
    ("policy_identifier", "expected_permission"),
    [
        (ALLOWED_DEPOSIT_RECIPIENTS_POLICY_IDENTIFIER, VaultDepositPermission.whitelisted),
        ("BUY_SHARES_CALLER_WHITELIST", VaultDepositPermission.whitelisted),
        ("DEPOSITOR_WHITELIST", VaultDepositPermission.whitelisted),
        ("INVESTOR_WHITELIST", VaultDepositPermission.whitelisted),
        ("ALLOWED_ADAPTERS", VaultDepositPermission.permissionless),
    ],
)
def test_blue_current_deposit_permission(
    monkeypatch: pytest.MonkeyPatch,
    policy_identifier: str,
    expected_permission: VaultDepositPermission,
) -> None:
    """Classify only Blue's reviewed recipient policy as permissioned."""

    policy_address = "0x0000000000000000000000000000000000000001"
    policy_manager_address = "0x0000000000000000000000000000000000000002"
    vault = EnzymeBlueVault.__new__(EnzymeBlueVault)
    vault.web3 = SimpleNamespace()
    vault.default_block_identifier = None
    vault.comptroller_contract = SimpleNamespace(
        address=ACCESSOR,
        functions=SimpleNamespace(
            getPolicyManager=lambda: SimpleNamespace(call=lambda **_kwargs: policy_manager_address),
        ),
    )
    policy_manager = SimpleNamespace(
        functions=SimpleNamespace(
            getEnabledPoliciesForFund=lambda _fund: SimpleNamespace(call=lambda **_kwargs: [policy_address]),
        )
    )
    policy = SimpleNamespace(
        functions=SimpleNamespace(
            identifier=lambda: SimpleNamespace(call=lambda **_kwargs: policy_identifier),
        )
    )

    def fake_get_deployed_contract(_web3, abi_name: str, _address: str):
        """Resolve policy-manager and policy test doubles."""

        return policy_manager if abi_name.endswith("PolicyManager.json") else policy

    monkeypatch.setattr(blue_vault, "get_deployed_contract", fake_get_deployed_contract)

    assert vault.is_whitelisted_deposit() is (expected_permission is VaultDepositPermission.whitelisted)
    assert fetch_deposit_permission(vault) is expected_permission
    assert vault.get_whitelist_notes() == (f"Enzyme {policy_identifier} policy restricts investor addresses; the policy does not establish KYC." if expected_permission is VaultDepositPermission.whitelisted else None)


def test_blue_legacy_policy_manager_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve deprecated Phoenix policy state through its FundDeployer."""

    fund_deployer = next(address for chain_id, address in ENZYME_BLUE_LEGACY_POLICY_MANAGERS if chain_id == CHAIN_ID)
    policy_manager_address = ENZYME_BLUE_LEGACY_POLICY_MANAGERS[CHAIN_ID, fund_deployer]
    policy_address = "0x0000000000000000000000000000000000000001"
    vault = EnzymeBlueVault.__new__(EnzymeBlueVault)
    vault.web3 = SimpleNamespace()
    vault.spec = VaultSpec(CHAIN_ID, VAULT)
    vault.default_block_identifier = None

    def raise_legacy_comptroller(**_kwargs):
        """Reproduce the missing legacy Comptroller method."""

        message = "legacy Comptroller"
        raise ValueError(message)

    vault.comptroller_contract = SimpleNamespace(
        address=ACCESSOR,
        functions=SimpleNamespace(
            getPolicyManager=lambda: SimpleNamespace(call=raise_legacy_comptroller),
        ),
    )
    dispatcher = SimpleNamespace(
        functions=SimpleNamespace(
            getFundDeployerForVaultProxy=lambda _vault: SimpleNamespace(call=lambda **_kwargs: fund_deployer),
        )
    )
    policy_manager = SimpleNamespace(
        functions=SimpleNamespace(
            getEnabledPoliciesForFund=lambda _fund: SimpleNamespace(call=lambda **_kwargs: [policy_address]),
        )
    )
    policy = SimpleNamespace(
        functions=SimpleNamespace(
            identifier=lambda: SimpleNamespace(call=lambda **_kwargs: "INVESTOR_WHITELIST"),
        )
    )

    def fake_get_deployed_contract(_web3, abi_name: str, address: str):
        """Resolve deprecated-release test doubles by ABI and address."""

        if abi_name.endswith("Dispatcher.json"):
            return dispatcher
        if abi_name.endswith("PolicyManager.json"):
            assert address == policy_manager_address
            return policy_manager
        return policy

    monkeypatch.setattr(blue_vault, "get_deployed_contract", fake_get_deployed_contract)

    assert vault.is_whitelisted_deposit() is True


def test_blue_current_fees_include_protocol_fee_in_user_facing_management_rate(monkeypatch) -> None:
    """Show the combined Blue fee paid by an investor and its breakdown."""

    vault = EnzymeBlueVault.__new__(EnzymeBlueVault)
    vault.default_block_identifier = None
    vault.comptroller_contract = SimpleNamespace(address=ACCESSOR)
    fees = ["management", "performance", "entrance", "exit"]
    manager_annual_rate = Decimal("0.01")
    management_per_second_rate = int(MANAGEMENT_FEE_RATE_SCALE * (1 / (1 - manager_annual_rate)) ** (1 / SECONDS_PER_YEAR))

    monkeypatch.setattr(vault, "_fetch_enabled_fee_contracts", lambda _block: fees)
    monkeypatch.setattr(vault, "_fetch_protocol_fee", lambda _block: EXPECTED_PROTOCOL_FEE)
    monkeypatch.setattr(
        vault,
        "_try_fee_call",
        lambda fee, function_name, *_args, **_kwargs: {
            ("management", "getFeeInfoForFund"): (management_per_second_rate, 0),
            ("performance", "getFeeInfoForFund"): (int(FEE_BPS_DENOMINATOR * Decimal(str(EXPECTED_PERFORMANCE_FEE))), 0),
            ("entrance", "getRateForFund"): int(FEE_BPS_DENOMINATOR * Decimal(str(EXPECTED_ENTRANCE_FEE))),
            ("exit", "getInKindRateForFund"): int(FEE_BPS_DENOMINATOR * Decimal(str(EXPECTED_EXIT_FEE))),
        }.get((fee, function_name)),
    )

    fee_data = vault.get_fee_data()

    assert fee_data.management == pytest.approx(EXPECTED_TOTAL_MANAGEMENT_FEE)
    assert fee_data.protocol == EXPECTED_PROTOCOL_FEE
    assert fee_data.performance == EXPECTED_PERFORMANCE_FEE
    assert fee_data.deposit == EXPECTED_ENTRANCE_FEE
    assert fee_data.withdraw == EXPECTED_EXIT_FEE
