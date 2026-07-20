"""Test Ondo tokenised-fund classification and issuer NAV adapters."""

# Test helpers mirror discovery callback signatures.
# ruff: noqa: ARG001, FBT001, FBT002, PLR2004, PLR6301

import datetime
import os
from decimal import Decimal
from types import SimpleNamespace

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626 import discovery_base as discovery_base_module
from eth_defi.erc_4626.classification import VaultFeatureProbe, create_vault_instance, create_vault_instance_autodetect, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import LeadScanReport, VaultDiscoveryBase
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.ondo.constants import ETHEREUM_CHAIN_ID, ONDO_HARDCODED_LEADS, ONDO_OUSG_ETHEREUM, ONDO_PRODUCTS, ONDO_USDY_ETHEREUM
from eth_defi.tokenised_fund.ondo.historical import OndoVaultHistoricalReader
from eth_defi.tokenised_fund.ondo.vault import ONDO_RESTRICTED_FLOW_REASON, OndoVault
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
ONDO_TEST_BLOCK = 25_450_000
USDY_EXPECTED_SHARE_PRICE = Decimal("1.1381987")
OUSG_EXPECTED_SHARE_PRICE = Decimal("115.76487")


class DummyOndoDiscovery(VaultDiscoveryBase):
    """Minimal discovery backend for hardcoded Ondo Ethereum leads."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=ETHEREUM_CHAIN_ID))
    web3factory = object()

    def fetch_leads(self, _start_block: int, _end_block: int, _display_progress: bool = True) -> LeadScanReport:
        """Return no event-derived leads."""

        return LeadScanReport()


def test_ondo_hardcoded_classification_and_curator_are_chain_aware() -> None:
    """Classify only the reviewed Ethereum USDY and OUSG token addresses."""

    broken_probe = SimpleNamespace(success=True, result=b"")
    for product in ONDO_PRODUCTS.values():
        assert identify_vault_features(product.token, {"EVM IS BROKEN SHIT": broken_probe}, "Ondo", chain_id=1) == {ERC4626Feature.ondo_like}
        assert ERC4626Feature.ondo_like not in identify_vault_features(product.token, {"EVM IS BROKEN SHIT": broken_probe}, "other", chain_id=31337)
        assert identify_curator(product.chain_id, product.symbol, product.product_name, product.token) == "ondo"


def test_ondo_hardcoded_leads_are_added_to_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add reviewed issuer share tokens without ERC-4626 flow events."""

    def fake_probe_vaults(chain: int, web3factory: object, addresses: list[str], *, block_identifier: int, max_workers: int, progress_bar_desc: str | None):
        """Return explicit Ondo classifications for registered leads."""

        assert chain == ETHEREUM_CHAIN_ID
        assert web3factory is DummyOndoDiscovery.web3factory
        assert set(addresses) == {product.token for product in ONDO_PRODUCTS.values()}
        for address in addresses:
            yield VaultFeatureProbe(address=address, features={ERC4626Feature.ondo_like})

    monkeypatch.setattr(discovery_base_module, "probe_vaults", fake_probe_vaults)
    report = DummyOndoDiscovery(max_workers=1).scan_vaults(0, max(product.first_seen_at_block for product in ONDO_PRODUCTS.values()), display_progress=False, hardcoded_lead_sources=(("Ondo", ONDO_HARDCODED_LEADS),))
    assert report.new_leads == 2
    assert set(report.leads) == {product.token for product in ONDO_PRODUCTS.values()}


def test_ondo_vault_blocks_generic_transactions() -> None:
    """Expose only read-only support for permissioned issuer flows."""

    vault = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), ONDO_USDY_ETHEREUM.token, features={ERC4626Feature.ondo_like})
    assert isinstance(vault, OndoVault)
    assert vault.get_protocol_name() == "Ondo"
    assert get_vault_protocol_name({ERC4626Feature.ondo_like}) == "Ondo"
    assert vault.fetch_deposit_closed_reason() == ONDO_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == ONDO_RESTRICTED_FLOW_REASON
    assert vault.get_deposit_manager_capability() is None
    assert isinstance(vault.get_historical_reader(stateful=False), OndoVaultHistoricalReader)
    assert vault.get_flags() == {VaultFlag.tokenised_fund}
    assert vault.short_description == ONDO_USDY_ETHEREUM.short_description
    assert vault.description == ONDO_USDY_ETHEREUM.description
    assert vault.short_description != vault.description
    metadata = vault.fetch_scan_record_extra_data()
    assert metadata["Denomination"] == "USD"
    assert metadata["_synthetic_usd_denomination"] is True
    assert metadata["_denomination_token"]["address"] is None


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at a deterministic archive block."""

    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run Ondo integration tests")
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=ONDO_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a fork-backed Web3 connection."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


@flaky.flaky
@pytest.mark.parametrize(("product", "expected_price"), ((ONDO_USDY_ETHEREUM, USDY_EXPECTED_SHARE_PRICE), (ONDO_OUSG_ETHEREUM, OUSG_EXPECTED_SHARE_PRICE)))
def test_ondo_autodetect_reads_issuer_nav(web3: Web3, product, expected_price: Decimal) -> None:
    """Read each reviewed token's canonical issuer NAV and historical row."""

    vault = create_vault_instance_autodetect(web3, product.token)
    assert isinstance(vault, OndoVault)
    assert vault.features == {ERC4626Feature.ondo_like}
    assert vault.share_token.decimals == 18
    assert vault.fetch_share_price(ONDO_TEST_BLOCK) == expected_price
    assert vault.fetch_total_supply(ONDO_TEST_BLOCK) > 0
    assert vault.fetch_total_assets(ONDO_TEST_BLOCK) == vault.fetch_total_supply(ONDO_TEST_BLOCK) * expected_price
    reader = vault.get_historical_reader(stateful=False)
    results = [call.call_as_result(web3, block_identifier=ONDO_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(ONDO_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(ONDO_TEST_BLOCK, timestamp, results)
    assert read.share_price == expected_price
    assert read.total_assets == vault.fetch_total_assets(ONDO_TEST_BLOCK)
    assert read.errors is None
