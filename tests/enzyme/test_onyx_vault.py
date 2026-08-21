"""Test Enzyme Onyx Base vault discovery and historical accounting."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_abi import encode
from web3 import Web3

from eth_defi.enzyme import onyx_vault
from eth_defi.enzyme.blue_vault import EnzymeBlueVault
from eth_defi.enzyme.onyx_discovery import ENZYME_BASE_SHARES_FACTORY, EnzymeVaultFactoryCandidate, decode_enzyme_shares_deployed_event, fetch_enzyme_shares_factories_for_chain
from eth_defi.enzyme.onyx_historical import EnzymeVaultHistoricalReader
from eth_defi.enzyme.onyx_vault import ONYX_VALUE_ASSET_COMPARABILITY_TOKENS, EnzymeVault
from eth_defi.erc_4626.classification import create_probe_calls, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name, is_activity_filter_exempt
from eth_defi.erc_4626.discovery_base import _prepare_probe_leads, create_enzyme_factory_detection, create_enzyme_potential_vault_match  # noqa: PLC2701
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk

BASE_CHAIN_ID = 8453
TEST_SHARES_ADDRESS = "0x000000000000000000000000000000000000bEEF"
EXPECTED_MANAGEMENT_FEE = 0.01
EXPECTED_PERFORMANCE_FEE = 0.2
EXPECTED_ENTRANCE_FEE = 0.0025
EXPECTED_EXIT_FEE = 0.005


def test_enzyme_base_shares_factory_is_configured() -> None:
    """Enable the official Onyx SharesFactory only on Base."""

    assert [address.lower() for address in fetch_enzyme_shares_factories_for_chain(BASE_CHAIN_ID)] == [ENZYME_BASE_SHARES_FACTORY.lower()]
    assert fetch_enzyme_shares_factories_for_chain(1) == []


def test_enzyme_factory_event_decodes_canonical_shares_address() -> None:
    """Decode the Shares address from a factory proxy-deployment event."""

    data = encode(["address"], [TEST_SHARES_ADDRESS])
    shares = decode_enzyme_shares_deployed_event({"address": ENZYME_BASE_SHARES_FACTORY, "data": data.hex()})

    assert shares == TEST_SHARES_ADDRESS


def test_enzyme_adapter_routes_through_vault_base() -> None:
    """Create the non-ERC-4626 adapter through the shared classifier factory."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=BASE_CHAIN_ID))
    vault = create_vault_instance(web3, TEST_SHARES_ADDRESS, features={ERC4626Feature.enzyme_onyx_like})

    assert isinstance(vault, EnzymeVault)
    assert vault.vault_address == vault.address
    assert get_vault_protocol_name({ERC4626Feature.enzyme_onyx_like}) == "Enzyme"
    assert get_vault_protocol_name({ERC4626Feature.enzyme_blue_like}) == "Enzyme"
    assert ERC4626Feature("enzyme_like") is ERC4626Feature.enzyme_onyx_like
    assert isinstance(create_vault_instance(web3, TEST_SHARES_ADDRESS, features={ERC4626Feature.enzyme_blue_like}), EnzymeBlueVault)


def test_enzyme_risk_is_low() -> None:
    """Classify Enzyme's technical protocol risk as low."""

    assert get_vault_risk("Enzyme") is VaultTechnicalRisk.low


def test_enzyme_factory_leads_bypass_erc4626_activity_threshold() -> None:
    """Keep valid factory-discovered Shares vaults eligible for price scanning."""

    detection = SimpleNamespace(features={ERC4626Feature.enzyme_onyx_like})
    assert is_activity_filter_exempt(detection) is True


def test_enzyme_factory_lead_skips_generic_erc4626_probes() -> None:
    """Trust the reviewed factory event instead of repeating broad probes."""

    candidate = EnzymeVaultFactoryCandidate(
        chain=BASE_CHAIN_ID,
        address=TEST_SHARES_ADDRESS,
        factory_address=ENZYME_BASE_SHARES_FACTORY,
        created_block=35_306_000,
        created_at=datetime.datetime(2026, 8, 20),  # noqa: DTZ001
        transaction_hash="0xdead",
        log_index=0,
    )
    lead = create_enzyme_potential_vault_match(candidate)
    addresses, _leads_by_address, factory_lead_count = _prepare_probe_leads({TEST_SHARES_ADDRESS: lead})

    assert addresses == []
    assert factory_lead_count == 1
    assert create_enzyme_factory_detection(candidate).features == {ERC4626Feature.enzyme_onyx_like}


def test_enzyme_accessors_are_not_added_to_every_base_probe() -> None:
    """Keep Onyx recognition tied to its trusted factory event."""

    function_names = {call.func_name for call in create_probe_calls([TEST_SHARES_ADDRESS], chain_id=BASE_CHAIN_ID)}

    assert "sharePrice" not in function_names
    assert "getValueAsset" not in function_names


def test_enzyme_current_reader_derives_share_price_and_total_value() -> None:
    """Read the live scanner metrics from the Shares price and supply calls."""

    vault = EnzymeVault.__new__(EnzymeVault)
    vault.shares_contract = SimpleNamespace(
        functions=SimpleNamespace(
            sharePrice=lambda: SimpleNamespace(call=lambda **_kwargs: (int(Decimal("1.25") * 10**18), 123)),
            totalSupply=lambda: SimpleNamespace(call=lambda **_kwargs: int(Decimal("40") * 10**18)),
        )
    )

    assert vault.fetch_share_price(123) == Decimal("1.25")
    assert vault.fetch_total_supply(123) == Decimal("40")
    assert vault.fetch_total_assets(123) == Decimal("50")
    assert vault.fetch_nav(123) == Decimal("50")
    assert isinstance(vault.get_historical_reader(stateful=False), EnzymeVaultHistoricalReader)


@pytest.mark.parametrize(
    ("value_asset", "expected_symbol"),
    [
        ("USD", "USDC"),
        ("BTC", "cbBTC"),
        ("EUR", "EURC"),
    ],
)
def test_enzyme_value_assets_use_canonical_metric_denominations(monkeypatch, value_asset: str, expected_symbol: str) -> None:
    """Normalise every discovered Onyx value asset without using handler assets."""

    expected_address = ONYX_VALUE_ASSET_COMPARABILITY_TOKENS[value_asset]
    token = SimpleNamespace(address=expected_address, symbol=expected_symbol)
    vault = EnzymeVault.__new__(EnzymeVault)
    vault.web3 = SimpleNamespace()
    vault.spec = SimpleNamespace(chain_id=BASE_CHAIN_ID, vault_address=TEST_SHARES_ADDRESS)
    vault.default_block_identifier = None
    vault.token_cache = {}
    vault.shares_contract = SimpleNamespace(
        functions=SimpleNamespace(
            getValueAsset=lambda: SimpleNamespace(call=lambda **_kwargs: value_asset.encode("ascii").ljust(32, b"\x00")),
        )
    )
    monkeypatch.setattr(onyx_vault, "fetch_erc20_details", lambda *_args, **_kwargs: token)

    assert vault.fetch_denomination_token_address() == expected_address
    assert vault.fetch_denomination_token() is token
    assert vault.fetch_info()["denomination_assumption"] == "Onyx USD, BTC and EUR values are exported as canonical Base USDC, cbBTC and EURC metrics respectively"


def test_enzyme_unknown_value_asset_fails_before_historical_reader() -> None:
    """Reject a new named value asset instead of leaving its denomination null."""

    vault = EnzymeVault.__new__(EnzymeVault)
    vault.spec = SimpleNamespace(chain_id=BASE_CHAIN_ID, vault_address=TEST_SHARES_ADDRESS)
    vault.default_block_identifier = None
    vault.shares_contract = SimpleNamespace(
        functions=SimpleNamespace(
            getValueAsset=lambda: SimpleNamespace(call=lambda **_kwargs: b"GOLD".ljust(32, b"\x00")),
        )
    )

    with pytest.raises(onyx_vault.EnzymeVaultUnsupportedError, match="unsupported value asset 'GOLD'"):
        vault.fetch_denomination_token_address()


def test_enzyme_current_reader_reads_fee_trackers(monkeypatch) -> None:
    """Read every current Enzyme fee class and reuse the FeeHandler."""

    management_tracker_address = "0x0000000000000000000000000000000000000001"
    performance_tracker_address = "0x0000000000000000000000000000000000000002"
    fee_handler_call_count = 0
    vault = EnzymeVault.__new__(EnzymeVault)
    vault.web3 = SimpleNamespace()
    vault.default_block_identifier = None

    def fetch_fee_handler_address() -> SimpleNamespace:
        """Return an observable current FeeHandler address call."""

        def call(**_kwargs) -> str:
            """Count FeeHandler lookups."""

            nonlocal fee_handler_call_count
            fee_handler_call_count += 1
            return "0x0000000000000000000000000000000000000003"

        return SimpleNamespace(call=call)

    vault.shares_contract = SimpleNamespace(
        functions=SimpleNamespace(
            getFeeHandler=fetch_fee_handler_address,
        )
    )
    fee_handler = SimpleNamespace(
        functions=SimpleNamespace(
            getManagementFeeTracker=lambda: SimpleNamespace(call=lambda **_kwargs: management_tracker_address),
            getPerformanceFeeTracker=lambda: SimpleNamespace(call=lambda **_kwargs: performance_tracker_address),
            getEntranceFeeBps=lambda: SimpleNamespace(call=lambda **_kwargs: 25),
            getExitFeeBps=lambda: SimpleNamespace(call=lambda **_kwargs: 50),
        )
    )
    management_tracker = SimpleNamespace(functions=SimpleNamespace(getRate=lambda: SimpleNamespace(call=lambda **_kwargs: 100)))
    performance_tracker = SimpleNamespace(functions=SimpleNamespace(getRate=lambda: SimpleNamespace(call=lambda **_kwargs: 2_000)))

    def fake_get_deployed_contract(_web3, abi_name: str, address: str):
        """Return the fake fee component matching an ABI and address."""

        if abi_name.endswith("FeeHandler.json"):
            return fee_handler
        if address == management_tracker_address:
            return management_tracker
        return performance_tracker

    monkeypatch.setattr(onyx_vault, "get_deployed_contract", fake_get_deployed_contract)

    assert vault.get_management_fee(123) == EXPECTED_MANAGEMENT_FEE
    assert vault.get_performance_fee(123) == EXPECTED_PERFORMANCE_FEE
    assert vault.get_deposit_fee(123) == EXPECTED_ENTRANCE_FEE
    assert vault.get_withdraw_fee(123) == EXPECTED_EXIT_FEE

    before_fee_data_call_count = fee_handler_call_count
    monkeypatch.setattr(vault, "get_fee_mode", lambda: VaultFeeMode.internalised_skimming)
    fee_data = vault.get_fee_data()

    assert fee_data.management == EXPECTED_MANAGEMENT_FEE
    assert fee_data.protocol is None
    assert fee_data.performance == EXPECTED_PERFORMANCE_FEE
    assert fee_data.deposit == EXPECTED_ENTRANCE_FEE
    assert fee_data.withdraw == EXPECTED_EXIT_FEE
    assert fee_handler_call_count == before_fee_data_call_count + 1


def test_enzyme_historical_reader_derives_value_asset_total_value() -> None:
    """Convert 18-decimal share price and supply to calculated total value."""

    state_updates = []
    reader = EnzymeVaultHistoricalReader.__new__(EnzymeVaultHistoricalReader)
    reader.vault = SimpleNamespace(address=TEST_SHARES_ADDRESS)
    reader.reader_state = SimpleNamespace(
        on_called=lambda result, total_assets, share_price: state_updates.append((result, total_assets, share_price)),
    )
    share_price_call = EncodedCall(func_name="sharePrice", address=TEST_SHARES_ADDRESS, data=b"", extra_data={"function": "share_price"})
    total_supply_call = EncodedCall(func_name="totalSupply", address=TEST_SHARES_ADDRESS, data=b"", extra_data={"function": "total_supply"})
    read = reader.process_result(
        block_number=123,
        timestamp=datetime.datetime(2026, 8, 20),  # noqa: DTZ001
        call_results=[
            EncodedCallResult(call=share_price_call, success=True, result=encode(["uint256", "uint256"], [int(Decimal("1.25") * 10**18), 123]), block_identifier=123),
            EncodedCallResult(call=total_supply_call, success=True, result=int(Decimal("40") * 10**18).to_bytes(32, "big"), block_identifier=123),
        ],
    )

    assert read.share_price == Decimal("1.25")
    assert read.total_supply == Decimal("40")
    assert read.total_assets == Decimal("50")
    assert len(state_updates) == 1
    assert state_updates[0][1:] == (Decimal("50"), Decimal("1.25"))


def test_enzyme_historical_reader_records_progress_without_share_price() -> None:
    """Advance reader state when an early Shares contract has no share price."""

    progress_updates = []

    def record_progress(result: EncodedCallResult) -> None:
        """Record the supply call accepted by the reader state."""

        progress_updates.append(result)

    reader = EnzymeVaultHistoricalReader.__new__(EnzymeVaultHistoricalReader)
    reader.vault = SimpleNamespace(address=TEST_SHARES_ADDRESS)
    reader.reader_state = SimpleNamespace(on_unpriced_call=record_progress)
    share_price_call = EncodedCall(func_name="sharePrice", address=TEST_SHARES_ADDRESS, data=b"", extra_data={"function": "share_price"})
    total_supply_call = EncodedCall(func_name="totalSupply", address=TEST_SHARES_ADDRESS, data=b"", extra_data={"function": "total_supply"})

    read = reader.process_result(
        block_number=123,
        timestamp=datetime.datetime(2026, 8, 20),  # noqa: DTZ001
        call_results=[
            EncodedCallResult(call=share_price_call, success=False, result=b"", block_identifier=123),
            EncodedCallResult(call=total_supply_call, success=True, result=(0).to_bytes(32, "big"), block_identifier=123),
        ],
    )

    assert read.share_price is None
    assert read.total_assets is None
    assert len(progress_updates) == 1


def test_onyx_current_deposit_permission_is_unknown_without_handler_index() -> None:
    """Do not infer investor permission from a non-enumerable Shares contract."""

    vault = EnzymeVault.__new__(EnzymeVault)
    vault.spec = SimpleNamespace(vault_address=TEST_SHARES_ADDRESS)

    assert fetch_deposit_permission(vault) is VaultDepositPermission.unknown


def test_onyx_link_opens_address_specific_enzyme_page() -> None:
    """Link a Base Onyx Shares address directly to its vault detail page."""

    vault = EnzymeVault.__new__(EnzymeVault)
    vault.spec = VaultSpec(8453, TEST_SHARES_ADDRESS)

    assert vault.get_link() == f"https://app.enzyme.finance/vault/{Web3.to_checksum_address(TEST_SHARES_ADDRESS)}?network=base"
