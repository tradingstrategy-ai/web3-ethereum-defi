"""Sygnum FILQ tokenised-fund regression tests."""

# ruff: noqa: ARG001, FBT001, FBT002, PLR6301

import datetime
from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626 import discovery_base as discovery_base_module
from eth_defi.erc_4626.classification import VaultFeatureProbe, create_vault_instance, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import LeadScanReport, VaultDiscoveryBase
from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, SYGNUM_ETHEREUM_CHAIN_ID, SYGNUM_HARDCODED_LEADS
from eth_defi.tokenised_fund.sygnum.historical import SygnumVaultHistoricalReader
from eth_defi.tokenised_fund.sygnum.vault import SYGNUM_NAV_UNAVAILABLE_REASON, SYGNUM_RESTRICTED_FLOW_REASON, SygnumVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import get_curator_name, identify_curator, is_protocol_curator
from eth_defi.vault.historical import VaultHistoricalReadMulticaller


class DummySygnumDiscovery(VaultDiscoveryBase):
    """Discovery backend without event-derived leads."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=SYGNUM_ETHEREUM_CHAIN_ID))
    web3factory = object()

    def fetch_leads(self, _start_block: int, _end_block: int, _display_progress: bool = True) -> LeadScanReport:
        """Return no standard ERC-4626 leads.

        :param _start_block: Ignored start block.
        :param _end_block: Ignored end block.
        :param _display_progress: Ignored progress switch.
        :return: Empty report.
        """
        return LeadScanReport()


class DummyToken:
    """Convert two-decimal FILQ supply values."""

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert a raw FILQ amount.

        :param raw_amount: Two-decimal raw amount.
        :return: Human-readable supply.
        """
        return Decimal(raw_amount) / Decimal(100)


class DummyVault:
    """Minimal historical-reader adapter."""

    address = FILQ_A_ETHEREUM_ADDRESS
    vault_address = FILQ_A_ETHEREUM_ADDRESS
    spec = VaultSpec(SYGNUM_ETHEREUM_CHAIN_ID, FILQ_A_ETHEREUM_ADDRESS)
    first_seen_at_block = FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
    share_token = DummyToken()
    denomination_token = None
    nav_unavailable_reason = SYGNUM_NAV_UNAVAILABLE_REASON


def test_sygnum_hardcoded_classification_is_chain_aware() -> None:
    """Classify FILQ only on reviewed Ethereum deployment."""

    broken_probe = SimpleNamespace(success=True, result=b"")
    assert identify_vault_features(FILQ_A_ETHEREUM_ADDRESS, {"EVM IS BROKEN SHIT": broken_probe}, "sygnum", chain_id=SYGNUM_ETHEREUM_CHAIN_ID) == {ERC4626Feature.sygnum_like}
    assert ERC4626Feature.sygnum_like not in identify_vault_features(FILQ_A_ETHEREUM_ADDRESS, {"EVM IS BROKEN SHIT": broken_probe}, "wrong chain", chain_id=31337)
    assert get_vault_protocol_name({ERC4626Feature.sygnum_like}) == "Sygnum"


def test_sygnum_vault_blocks_public_flows_and_unpriced_nav() -> None:
    """Keep permissioned FILQ flows and unavailable NAV explicit."""

    vault = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), FILQ_A_ETHEREUM_ADDRESS, features={ERC4626Feature.sygnum_like})
    assert isinstance(vault, SygnumVault)
    assert vault.fetch_deposit_closed_reason() == SYGNUM_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == SYGNUM_RESTRICTED_FLOW_REASON
    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(NotImplementedError, match="Sygnum-approved"):
        vault.get_deposit_manager()
    with pytest.raises(RuntimeError, match="no public historical NAV"):
        vault.fetch_share_price()
    reader = vault.get_historical_reader(stateful=True)
    assert isinstance(reader.reader_state, VaultReaderState)


def test_sygnum_historical_reader_keeps_supply_without_price() -> None:
    """Avoid synthetic FILQ NAV or TVL in historical rows."""

    reader = SygnumVaultHistoricalReader.__new__(SygnumVaultHistoricalReader)
    reader.vault = DummyVault()
    reader.reader_state = VaultReaderState(reader.vault)
    call = EncodedCall(func_name="totalSupply", address=FILQ_A_ETHEREUM_ADDRESS, data=b"", extra_data={"function": "totalSupply", "vault": FILQ_A_ETHEREUM_ADDRESS})
    timestamp = datetime.datetime(2026, 4, 27, 2, 19, 35, tzinfo=datetime.UTC).replace(tzinfo=None)
    result = EncodedCallResult(call=call, success=True, result=(44_826_428).to_bytes(32, "big"), block_identifier=FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, timestamp=timestamp)
    row = reader.process_result(FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, timestamp, [result])
    assert row.total_supply == Decimal("448264.28")
    assert row.share_price is None
    assert row.total_assets is None
    assert row.errors == [SYGNUM_NAV_UNAVAILABLE_REASON]
    assert reader.reader_state.last_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
    assert reader.reader_state.last_call_at == timestamp
    assert reader.reader_state.last_tvl == Decimal(0)
    assert reader.reader_state.last_share_price == Decimal(0)


def test_stateful_scanner_rejects_reader_without_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject malformed protocol readers before threaded token preparation."""

    vault = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), FILQ_A_ETHEREUM_ADDRESS, features={ERC4626Feature.sygnum_like})
    multicaller = object.__new__(VaultHistoricalReadMulticaller)
    prepared_reader = multicaller._prepare_reader(vault, stateful=True)
    assert isinstance(prepared_reader.reader_state, VaultReaderState)
    assert multicaller._prepare_denomination_token(prepared_reader) is None

    stateless_reader = vault.get_historical_reader(stateful=False)
    monkeypatch.setattr(vault, "get_historical_reader", lambda stateful: stateless_reader)
    with pytest.raises(TypeError, match="did not initialise a BatchCallState reader_state"):
        multicaller._prepare_reader(vault, stateful=True)


def test_sygnum_hardcoded_lead_is_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add FILQ-A without relying on ERC-4626 events."""

    def fake_probe_vaults(chain: int, web3factory: object, addresses: list[str], **kwargs: object) -> Iterator[VaultFeatureProbe]:
        """Yield the expected hardcoded Sygnum feature.

        :param chain: Expected Ethereum chain id.
        :param web3factory: Expected discovery factory.
        :param addresses: Expected FILQ-only address list.
        :param kwargs: Probe options.
        :return: One Sygnum feature probe.
        """
        assert chain == SYGNUM_ETHEREUM_CHAIN_ID
        assert web3factory is DummySygnumDiscovery.web3factory
        assert addresses == [FILQ_A_ETHEREUM_ADDRESS]
        yield VaultFeatureProbe(address=FILQ_A_ETHEREUM_ADDRESS, features={ERC4626Feature.sygnum_like})

    monkeypatch.setattr(discovery_base_module, "probe_vaults", fake_probe_vaults)
    report = DummySygnumDiscovery(max_workers=1).scan_vaults(0, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, display_progress=False, hardcoded_lead_sources=(("Sygnum", SYGNUM_HARDCODED_LEADS),))
    assert report.new_leads == 1
    assert report.detections[FILQ_A_ETHEREUM_ADDRESS].features == {ERC4626Feature.sygnum_like}


def test_sygnum_is_protocol_managed_curator() -> None:
    """Assign FILQ to Sygnum's documented Desygnate operating role."""

    assert identify_curator(SYGNUM_ETHEREUM_CHAIN_ID, "FILQ-A", "Fidelity USD Digital Liquidity Fund-Acc", FILQ_A_ETHEREUM_ADDRESS, protocol_slug="sygnum") == "sygnum"
    assert is_protocol_curator("sygnum")
    assert get_curator_name("sygnum") == "Sygnum"
