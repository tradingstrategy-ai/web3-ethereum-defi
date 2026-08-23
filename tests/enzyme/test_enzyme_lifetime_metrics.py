"""Exercise Enzyme Blue and Onyx vaults through lifetime-metric export."""

import datetime
import os
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
import pytest
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.enzyme.offchain_metadata import ONYX_PUBLIC_DESCRIPTION_UNAVAILABLE
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

#: Enzyme Blue Base Camp VaultProxy, deployed before the shared Base fork block.
ENZYME_BLUE_VAULT = "0x75a17c22235b2dd584e3ea8c142422d97b826816"
ENZYME_BLUE_FIRST_SEEN_BLOCK = 23_221_202
ENZYME_BLUE_FIRST_SEEN_AT = datetime.datetime(2024, 12, 3, 13, 15, 51)  # noqa: DTZ001 - scanner uses naive UTC.

#: Enzyme Onyx USD-valued Shares vault, deployed before the shared Base fork block.
ENZYME_ONYX_VAULT = "0xdd922b0a90c3273c76fd68f3265293d50923a904"
ENZYME_ONYX_FIRST_SEEN_BLOCK = 36_213_502
ENZYME_ONYX_FIRST_SEEN_AT = datetime.datetime(2025, 9, 30, 7, 12, 31)  # noqa: DTZ001 - scanner uses naive UTC.

#: Enzyme Onyx EUR-valued Shares vault. It verifies that a non-USD value asset
#: reaches the generic historical reader state with an ERC-20 denomination.
ENZYME_ONYX_EUR_VAULT = "0x48e7c4b85ed4e8b7b0921101c21a806e49dd5006"
ENZYME_ONYX_EUR_FIRST_SEEN_BLOCK = 44_432_466
ENZYME_ONYX_EUR_FIRST_SEEN_AT = datetime.datetime(2026, 4, 8, 13, 17, 59)  # noqa: DTZ001 - scanner uses naive UTC.


@dataclass(slots=True, frozen=True)
class EnzymeMetricProfile:
    """Describe one real Enzyme vault's fixed-block metric expectations.

    :param address: Canonical Blue VaultProxy or Onyx Shares address.
    :param features: Factory-confirmed Enzyme architecture flag.
    :param first_seen_block: Factory deployment block used in scanner metadata.
    :param first_seen_at: Naive UTC factory deployment timestamp.
    :param expected_denomination: Expected exported denominator symbol.
    :param expected_share_price: Exact fixed-block vault share price.
    :param expected_total_assets: Exact fixed-block vault TVL.
    :param expected_total_supply: Exact fixed-block share supply.
    """

    address: str
    features: set[ERC4626Feature]
    first_seen_block: int
    first_seen_at: datetime.datetime
    expected_denomination: str
    expected_share_price: Decimal
    expected_total_assets: Decimal
    expected_total_supply: Decimal


pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run Enzyme lifetime-metric integration tests"),
    # Reuse the canonical Base fork with other read-only characterisation tests.
    pytest.mark.xdist_group("fork:base:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared, read-only Base midnight fork.

    :param anvil_fork_pool: Session-scoped fixed-block Anvil fork registry.
    :return: Web3 connection pinned to :data:`BASE_MIDNIGHT_BLOCK`.
    """

    assert JSON_RPC_BASE is not None
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, BASE_MIDNIGHT_BLOCK)


@pytest.mark.parametrize(
    "profile",
    [
        EnzymeMetricProfile(
            address=ENZYME_BLUE_VAULT,
            features={ERC4626Feature.enzyme_blue_like},
            first_seen_block=ENZYME_BLUE_FIRST_SEEN_BLOCK,
            first_seen_at=ENZYME_BLUE_FIRST_SEEN_AT,
            expected_denomination="WETH",
            expected_share_price=Decimal("1"),
            expected_total_assets=Decimal("0.01"),
            expected_total_supply=Decimal("0.01"),
        ),
        EnzymeMetricProfile(
            address=ENZYME_ONYX_VAULT,
            features={ERC4626Feature.enzyme_onyx_like},
            first_seen_block=ENZYME_ONYX_FIRST_SEEN_BLOCK,
            first_seen_at=ENZYME_ONYX_FIRST_SEEN_AT,
            expected_denomination="USDC",
            expected_share_price=Decimal("0.962906956707870729"),
            expected_total_assets=Decimal("12.85933917909920077489685820"),
            expected_total_supply=Decimal("13.35470586178400751"),
        ),
        EnzymeMetricProfile(
            address=ENZYME_ONYX_EUR_VAULT,
            features={ERC4626Feature.enzyme_onyx_like},
            first_seen_block=ENZYME_ONYX_EUR_FIRST_SEEN_BLOCK,
            first_seen_at=ENZYME_ONYX_EUR_FIRST_SEEN_AT,
            expected_denomination="EURC",
            expected_share_price=Decimal("1"),
            expected_total_assets=Decimal("100"),
            expected_total_supply=Decimal("100"),
        ),
    ],
)
def test_enzyme_vault_reaches_lifetime_metrics(
    web3: Web3,
    profile: EnzymeMetricProfile,
) -> None:
    """Export one real Enzyme Blue and Onyx vault through the full metric path.

    The test reads the production adapter and historical reader at the shared
    fixed Base fork, creates a scanner metadata row, and calculates lifetime
    metrics from representative price observations. This catches interface
    regressions between protocol-specific readers and the generic exporter.

    :param web3: Shared Base fork connection.
    :param profile: Fixed-block expectations for the selected Enzyme vault.
    :return: None. Assertions verify the complete export path.
    """

    vault = create_vault_instance(
        web3,
        profile.address,
        features=profile.features,
        token_cache={},
        default_block_identifier=BASE_MIDNIGHT_BLOCK,
        require_denomination_token=True,
    )
    assert vault is not None
    assert vault.denomination_token is not None
    assert vault.denomination_token.symbol == profile.expected_denomination
    if ERC4626Feature.enzyme_onyx_like in profile.features:
        assert vault.short_description is None
        assert vault.description == ONYX_PUBLIC_DESCRIPTION_UNAVAILABLE
    else:
        assert vault.short_description
        assert vault.description

    reader = vault.get_historical_reader(stateful=True)
    call_results = [call.call_as_result(web3, block_identifier=BASE_MIDNIGHT_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = native_datetime_utc_fromtimestamp(web3.eth.get_block(BASE_MIDNIGHT_BLOCK)["timestamp"])
    for result in call_results:
        result.timestamp = timestamp
    historical_read = reader.process_result(BASE_MIDNIGHT_BLOCK, timestamp, call_results)

    assert historical_read.errors is None
    assert historical_read.share_price == profile.expected_share_price
    assert historical_read.total_assets == profile.expected_total_assets
    assert historical_read.total_supply == profile.expected_total_supply

    detection = ERC4262VaultDetection(
        chain=8453,
        address=profile.address,
        first_seen_at_block=profile.first_seen_block,
        first_seen_at=profile.first_seen_at,
        features=profile.features,
        updated_at=profile.first_seen_at,
        deposit_count=0,
        redeem_count=0,
    )
    scan_record = create_vault_scan_record(web3, detection, block_identifier=BASE_MIDNIGHT_BLOCK, token_cache={})
    assert scan_record["Denomination"] == profile.expected_denomination
    assert scan_record["NAV"] == profile.expected_total_assets
    assert scan_record["Shares"] == profile.expected_total_supply

    spec = VaultSpec(8453, profile.address)
    timestamps = pd.date_range("2026-07-22", periods=3, freq="D")
    prices = pd.DataFrame(
        {
            "chain": [8453] * 3,
            "address": [profile.address] * 3,
            "id": [spec.as_string_id()] * 3,
            "block_number": [BASE_MIDNIGHT_BLOCK] * 3,
            "share_price": [float(profile.expected_share_price)] * 3,
            "total_assets": [float(profile.expected_total_assets)] * 3,
            "total_supply": [float(profile.expected_total_supply)] * 3,
            "event_count": [0] * 3,
            "vault_poll_frequency": ["large_tvl"] * 3,
        },
        index=timestamps,
    )
    metrics = calculate_lifetime_metrics(calculate_hourly_returns_for_all_vaults(prices), VaultDatabase(rows={spec: scan_record}))
    exported = export_lifetime_row(metrics.iloc[0])

    assert len(metrics) == 1
    assert exported["protocol"] == "Enzyme"
    assert exported["denomination"] == profile.expected_denomination
    assert exported["current_nav"] == pytest.approx(float(profile.expected_total_assets))
