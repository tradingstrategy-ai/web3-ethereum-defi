"""Test Asseto non-ERC-4626 vault classification and blocked flows."""

#: Test helper subclasses mirror the shared discovery interface signature.
# ruff: noqa: FBT001, FBT002, PLR6301

import datetime
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626 import discovery_base as discovery_base_module
from eth_defi.erc_4626.classification import VaultFeatureProbe, create_vault_instance, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import LeadScanReport, VaultDiscoveryBase
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.tokenised_fund.asseto import vault as asseto_vault_module
from eth_defi.tokenised_fund.asseto.constants import ASSETO_AOABT_HASHKEY, ASSETO_HARDCODED_LEADS, HASHKEY_CHAIN_ID
from eth_defi.tokenised_fund.asseto.historical import AssetoVaultHistoricalReader
from eth_defi.tokenised_fund.asseto.vault import ASSETO_BLOCKED_FLOW_REASON, AssetoRoleInfo, AssetoVault, convert_asseto_basis_points_to_percent, create_asseto_short_description
from eth_defi.tokenised_fund.vault import TokenisedFundDepositManager
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.risk import VaultTechnicalRisk

SYNTHETIC_TIMESTAMP = datetime.datetime(2026, 7, 16, 12, 0, tzinfo=datetime.UTC).replace(tzinfo=None)


class DummyAssetoDiscovery(VaultDiscoveryBase):
    """Minimal discovery backend for testing hardcoded Asseto leads."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=HASHKEY_CHAIN_ID))
    web3factory = object()

    def fetch_leads(
        self,
        _start_block: int,
        _end_block: int,
        _display_progress: bool = True,
    ) -> LeadScanReport:
        """Return no event-based leads."""

        return LeadScanReport()


class DummyAssetoToken:
    """Convert synthetic Asseto raw token amounts to decimals."""

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert a six-decimal raw token amount."""

        return Decimal(raw_amount) / Decimal(10**6)


class DummyAssetoVault:
    """Minimal Asseto vault required by its historical reader unit tests."""

    address = "0x0000000000000000000000000000000000000001"
    share_token = DummyAssetoToken()

    def convert_denomination_to_usd(self, value: Decimal, _timestamp: datetime.datetime) -> Decimal:
        """Pass through already-USD synthetic NAV values."""

        return value

    def get_performance_fee(self, _block_number: int) -> float:
        """Return the fixed strategy performance fee."""

        return 0.2

    def get_management_fee(self, _block_number: int) -> float:
        """Return the fixed strategy management fee."""

        return 0.01


class DummyOffchainAssetoVault(DummyAssetoVault):
    """Synthetic registry product with an Asseto display-price history."""

    def uses_onchain_pricer(self) -> bool:
        """Disable the HashKey-specific pricer call."""

        return False

    def fetch_offchain_share_price(self, _timestamp: datetime.datetime) -> Decimal:
        """Return a cached daily NAV/share point."""

        return Decimal("1.25")


class DummyAssetoReaderState:
    """Record state updates issued by the Asseto historical reader."""

    def __init__(self):
        """Initialise the recorded call list."""

        self.calls: list[dict[str, Decimal | int]] = []

    def on_called(
        self,
        result: EncodedCallResult,
        total_assets: Decimal | None = None,
        share_price: Decimal | None = None,
    ) -> None:
        """Record the NAV call and calculated Asseto TVL."""

        assert total_assets is not None
        assert share_price is not None
        self.calls.append(
            {
                "block_number": int(result.block_identifier),
                "total_assets": total_assets,
                "share_price": share_price,
            }
        )


def make_asseto_call_result(
    function_name: str,
    value: int,
    *,
    success: bool = True,
    block_number: int = 123,
) -> EncodedCallResult:
    """Create a synthetic Asseto historical multicall result."""

    call = EncodedCall(
        func_name=function_name,
        address=DummyAssetoVault.address,
        data=b"",
        extra_data={"function": function_name, "vault": DummyAssetoVault.address},
    )
    return EncodedCallResult(
        call=call,
        success=success,
        result=value.to_bytes(32, "big") if success else b"",
        block_identifier=block_number,
    )


def make_asseto_historical_reader(*, stateful: bool) -> tuple[AssetoVaultHistoricalReader, DummyAssetoReaderState | None]:
    """Create an Asseto historical reader without Web3 dependencies."""

    reader = AssetoVaultHistoricalReader.__new__(AssetoVaultHistoricalReader)
    reader.vault = DummyAssetoVault()
    state = DummyAssetoReaderState() if stateful else None
    reader.reader_state = state
    return reader, state


def test_asseto_hardcoded_lead_is_added_to_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add AoABT without relying on ERC-4626 deposit or withdrawal events."""

    def fake_probe_vaults(
        chain: int,
        web3factory: object,
        addresses: list[str],
        *,
        block_identifier: int,
        max_workers: int,
        progress_bar_desc: str | None,
    ):
        """Return Asseto classification for the only registered AoABT token."""

        assert chain == HASHKEY_CHAIN_ID
        assert web3factory is DummyAssetoDiscovery.web3factory
        assert addresses == [ASSETO_AOABT_HASHKEY.token]
        assert block_identifier == ASSETO_AOABT_HASHKEY.first_seen_at_block
        assert max_workers == 1
        assert progress_bar_desc is None
        yield VaultFeatureProbe(address=ASSETO_AOABT_HASHKEY.token, features={ERC4626Feature.asseto_like})

    monkeypatch.setattr(discovery_base_module, "probe_vaults", fake_probe_vaults)
    monkeypatch.setattr(discovery_base_module, "ODA_FACT_HARDCODED_LEADS", ())
    monkeypatch.setattr(discovery_base_module, "MIDAS_HARDCODED_LEADS", ())

    report = DummyAssetoDiscovery(max_workers=1).scan_vaults(
        start_block=0,
        end_block=ASSETO_AOABT_HASHKEY.first_seen_at_block,
        display_progress=False,
    )

    assert ASSETO_HARDCODED_LEADS == (
        (
            HASHKEY_CHAIN_ID,
            ASSETO_AOABT_HASHKEY.token,
            ASSETO_AOABT_HASHKEY.first_seen_at_block,
            ASSETO_AOABT_HASHKEY.first_seen_at,
        ),
    )
    assert report.new_leads == 1
    assert report.detections[ASSETO_AOABT_HASHKEY.token].features == {ERC4626Feature.asseto_like}


def test_asseto_hardcoded_classification_is_chain_aware() -> None:
    """Only classify the AoABT address on its registered HashKey Chain."""

    broken_probe = SimpleNamespace(success=True, result=b"")

    features = identify_vault_features(
        ASSETO_AOABT_HASHKEY.token,
        calls={"EVM IS BROKEN SHIT": broken_probe},
        debug_text="asseto HashKey",
        chain_id=HASHKEY_CHAIN_ID,
    )
    assert features == {ERC4626Feature.asseto_like}

    unsupported_chain_features = identify_vault_features(
        ASSETO_AOABT_HASHKEY.token,
        calls={"EVM IS BROKEN SHIT": broken_probe},
        debug_text="asseto unsupported chain",
        chain_id=31337,
    )
    assert ERC4626Feature.asseto_like not in unsupported_chain_features


def test_asseto_vault_is_read_only() -> None:
    """Create the direct VaultBase adapter and keep deposits blocked."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=HASHKEY_CHAIN_ID))
    vault = create_vault_instance(
        web3,
        ASSETO_AOABT_HASHKEY.token,
        features={ERC4626Feature.asseto_like},
    )

    assert isinstance(vault, AssetoVault)
    assert vault.get_protocol_name() == "Asseto"
    assert vault.get_flags() == {VaultFlag.tokenised_fund}
    assert get_vault_protocol_name({ERC4626Feature.asseto_like}) == "Asseto"
    assert vault.fetch_deposit_closed_reason() == ASSETO_BLOCKED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == ASSETO_BLOCKED_FLOW_REASON
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}
    assert isinstance(vault.get_historical_reader(stateful=False), AssetoVaultHistoricalReader)

    assert isinstance(vault.get_deposit_manager(), TokenisedFundDepositManager)


def test_asseto_vault_uses_product_metadata_when_token_metadata_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the display identity available when the ERC-20 metadata call fails."""

    monkeypatch.setattr(AssetoVault, "fetch_share_token", lambda _vault: SimpleNamespace(name=None, symbol=None))
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=HASHKEY_CHAIN_ID))
    vault = create_vault_instance(
        web3,
        ASSETO_AOABT_HASHKEY.token,
        features={ERC4626Feature.asseto_like},
    )

    assert vault.name == "Asseto Orient Arbitrage Token"
    assert vault.symbol == "AoABT"
    assert vault.short_description == "AoABT tokenises the Asseto Orient Arbitrage Strategy and offers daily U.S. dollar yields backed one-to-one by the underlying strategy."


def test_asseto_short_description_uses_product_strategy() -> None:
    """Expose the underlying Asseto strategy instead of token-wrapper boilerplate."""

    introduction = "AMCASH+ is a 1:1 asset-backed token collateralized by the ChinaAMC USD Digital Money Market Fund Class B USD, which invests in short-term deposits and high quality money market instruments. Investors receive daily NAV updates."

    assert create_asseto_short_description(introduction) == "AMCASH+ is a 1:1 asset-backed token collateralized by the ChinaAMC USD Digital Money Market Fund Class B USD, which invests in short-term deposits and high quality money market instruments."
    assert create_asseto_short_description("  Tokenised   fixed-income fund.  More detail follows. ") == "Tokenised fixed-income fund."
    assert create_asseto_short_description("CFSRS is backed by Stable Return SP. Its objective is stable returns. The fund primarily invests in international fixed-income securities. Further marketing text.") == "CFSRS is backed by Stable Return SP. The fund primarily invests in international fixed-income securities."
    assert create_asseto_short_description(None) is None


def test_asseto_vault_resolves_curator_from_priority_partner_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefer an Asseto investment manager over an investment advisor."""

    roles = (
        AssetoRoleInfo(role="Investment Advisor", organisation_name="CMS Asset Management (HK)", logo_url="https://example.com/advisor.svg"),
        AssetoRoleInfo(role="Investment Manager", organisation_name="DL Holdings", logo_url="https://example.com/manager.svg"),
        AssetoRoleInfo(role="Legal", organisation_name="Ogier", logo_url="https://example.com/legal.svg"),
    )

    def fake_fetch_roles(product_name: str) -> Iterator[AssetoRoleInfo]:
        """Return public roles for the Asseto product key."""

        assert product_name == "AoABT"
        yield from roles

    monkeypatch.setattr(asseto_vault_module, "fetch_asseto_product_roles", fake_fetch_roles)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=HASHKEY_CHAIN_ID))
    vault = create_vault_instance(
        web3,
        ASSETO_AOABT_HASHKEY.token,
        features={ERC4626Feature.asseto_like},
    )

    assert tuple(vault.fetch_roles()) == roles
    assert vault.fetch_curator_name() == "DL Holdings"
    assert vault.manager_name == "DL Holdings"


def test_asseto_historical_reader_calculates_tvl_and_updates_state() -> None:
    """Calculate historical TVL from six-decimal supply and 18-decimal NAV."""

    reader, state = make_asseto_historical_reader(stateful=True)

    read = reader.process_result(
        123,
        SYNTHETIC_TIMESTAMP,
        [
            make_asseto_call_result("totalSupply", 2_500_000),
            make_asseto_call_result("getLatestPrice", 1_012_345_678_900_000_000),
        ],
    )

    assert read.timestamp == SYNTHETIC_TIMESTAMP
    assert read.total_supply == Decimal("2.5")
    assert read.share_price == Decimal("1.0123456789")
    assert read.total_assets == Decimal("2.53086419725")
    assert read.performance_fee == pytest.approx(0.2)
    assert read.management_fee == pytest.approx(0.01)
    assert read.errors is None
    assert not read.deposits_open
    assert not read.redemption_open
    assert state is not None
    assert state.calls == [
        {
            "block_number": 123,
            "total_assets": Decimal("2.53086419725"),
            "share_price": Decimal("1.0123456789"),
        }
    ]


def test_asseto_historical_reader_uses_offchain_nav_without_pricer() -> None:
    """Combine historical ERC-20 supply with a registry product's display NAV."""

    reader, _state = make_asseto_historical_reader(stateful=False)
    reader.vault = DummyOffchainAssetoVault()

    read = reader.process_result(
        123,
        SYNTHETIC_TIMESTAMP,
        [make_asseto_call_result("totalSupply", 2_500_000)],
    )

    assert read.total_supply == Decimal("2.5")
    assert read.share_price == Decimal("1.25")
    assert read.total_assets == Decimal("3.125")
    assert read.errors is None


def test_asseto_vault_converts_hkd_nav_to_usd() -> None:
    """Use the latest currency observation on or before an Asseto NAV point."""

    first_timestamp = int(datetime.datetime(2026, 7, 15, tzinfo=datetime.UTC).timestamp())
    second_timestamp = int(datetime.datetime(2026, 7, 16, tzinfo=datetime.UTC).timestamp())
    product = replace(
        ASSETO_AOABT_HASHKEY,
        denomination_symbol="HKD",
        usd_exchange_rates=((first_timestamp, Decimal("7.80")), (second_timestamp, Decimal("7.81"))),
    )
    vault = AssetoVault.__new__(AssetoVault)
    vault.product = product
    vault._usd_exchange_rate_timestamps = (first_timestamp, second_timestamp)

    converted = vault.convert_denomination_to_usd(Decimal("781"), SYNTHETIC_TIMESTAMP)

    assert converted == Decimal("100")
    assert vault.converts_denomination_to_usd()
    assert vault.uses_synthetic_usd_denomination()


def test_asseto_historical_reader_records_partial_call_failure() -> None:
    """Leave TVL unset without state updates when the supply call fails."""

    reader, state = make_asseto_historical_reader(stateful=True)

    read = reader.process_result(
        123,
        SYNTHETIC_TIMESTAMP,
        [
            make_asseto_call_result("totalSupply", 0, success=False),
            make_asseto_call_result("getLatestPrice", 1_012_345_678_900_000_000),
        ],
    )

    assert read.total_supply is None
    assert read.share_price == Decimal("1.0123456789")
    assert read.total_assets is None
    assert read.errors == ["Asseto totalSupply call failed"]
    assert state is not None
    assert state.calls == []


def test_asseto_vault_maps_manager_and_fund_fees() -> None:
    """Map manager mint/redemption fees to shared entry/exit fields."""

    def make_view_call(value: int) -> SimpleNamespace:
        """Create one fake Web3 contract view call."""

        return SimpleNamespace(call=lambda **_kwargs: value)

    manager = SimpleNamespace(
        functions=SimpleNamespace(
            mintFee=lambda: make_view_call(25),
            redemptionFee=lambda: make_view_call(50),
            BPS_DENOMINATOR=lambda: make_view_call(10_000),
        )
    )
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            chain_id=HASHKEY_CHAIN_ID,
            contract=lambda **_kwargs: manager,
        )
    )
    vault = create_vault_instance(
        web3,
        ASSETO_AOABT_HASHKEY.token,
        features={ERC4626Feature.asseto_like},
    )

    assert vault.get_risk() == VaultTechnicalRisk.low
    assert vault.get_management_fee(123) == pytest.approx(0.01)
    assert vault.get_performance_fee(123) == pytest.approx(0.20)
    assert vault.get_deposit_fee(123) == pytest.approx(0.0025)
    assert vault.get_withdraw_fee(123) == pytest.approx(0.005)
    fee_data = vault.get_fee_data()
    assert fee_data.fee_mode == VaultFeeMode.internalised_skimming
    assert fee_data.deposit == pytest.approx(0.0025)  # Manager mint fee / entry fee.
    assert fee_data.withdraw == pytest.approx(0.005)  # Manager redemption fee / exit fee.
    assert vault.has_custom_fees()
    assert convert_asseto_basis_points_to_percent(100, 10_000) == pytest.approx(0.01)

    with pytest.raises(ValueError, match="BPS_DENOMINATOR"):
        convert_asseto_basis_points_to_percent(100, 0)
