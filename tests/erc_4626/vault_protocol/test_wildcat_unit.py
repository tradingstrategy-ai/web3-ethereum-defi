"""No-RPC tests for Wildcat discovery and market state formulas."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature, is_activity_filter_exempt
from eth_defi.erc_4626.discovery_base import LeadScanReport, PotentialVaultMatch, _prepare_probe_leads, add_wildcat_market_candidate_lead, create_wildcat_market_detection  # noqa: PLC2701
from eth_defi.erc_4626.vault_protocol.wildcat.constants import WILDCAT_RAY
from eth_defi.erc_4626.vault_protocol.wildcat.discovery import create_wildcat_market_candidate, fetch_wildcat_market_added_event_topic, is_wildcat_registry_log
from eth_defi.erc_4626.vault_protocol.wildcat.historical import WildcatMarketReaderState
from eth_defi.erc_4626.vault_protocol.wildcat.state import WildcatMarketState
from eth_defi.erc_4626.vault_protocol.wildcat.vault import WildcatMarket
from eth_defi.vault.base import VaultSpec

ARCH_CONTROLLER = "0xfEB516d9D946dD487A9346F6fee11f40C6945eE4"
HOOKS_FACTORY = "0xdd7dd3b5076cf89440d05585ff56d246386207be"
V1_CONTROLLER = "0x0000000000000000000000000000000000001234"
MARKET = "0xc9499006a149c553d18171747ed19aa7c6dd19e2"
CREATED_AT = datetime.datetime(2025, 2, 17, 13, 59, 47)


def make_market_added_log(controller: str, block_number: int | str = 21_866_550) -> SimpleNamespace:
    """Create a Hypersync-shaped ``MarketAdded`` log with a non-indexed market."""
    return SimpleNamespace(
        address=ARCH_CONTROLLER,
        topics=[fetch_wildcat_market_added_event_topic(), "0x" + controller.removeprefix("0x").lower().rjust(64, "0")],
        data=Web3.to_hex(Web3().codec.encode(["address"], [Web3.to_checksum_address(MARKET)])),
        block_number=block_number,
        transaction_hash="0xabc",
        log_index=1,
    )


def make_state(**overrides) -> WildcatMarketState:
    """Create a market state with a 1.1 scale factor and 1,000 scaled supply."""
    values = {
        "is_closed": False,
        "max_total_supply": 2_000,
        "accrued_protocol_fees": 0,
        "normalized_unclaimed_withdrawals": 0,
        "scaled_total_supply": 1_000,
        "scaled_pending_withdrawals": 0,
        "pending_withdrawal_expiry": 0,
        "is_delinquent": False,
        "time_delinquent": 0,
        "protocol_fee_bips": 500,
        "annual_interest_bips": 1_000,
        "reserve_ratio_bips": 1_000,
        "scale_factor": WILDCAT_RAY * 11 // 10,
        "last_interest_accrued_timestamp": 0,
    }
    values.update(overrides)
    return WildcatMarketState(**values)


def test_wildcat_market_added_decoding() -> None:
    """A V2 registry event becomes a candidate; V1 events are skipped."""
    log = make_market_added_log(HOOKS_FACTORY, block_number="0x14da836")
    assert is_wildcat_registry_log(1, ARCH_CONTROLLER, log.topics[0])
    assert not is_wildcat_registry_log(1, MARKET, log.topics[0])
    assert not is_wildcat_registry_log(9745, ARCH_CONTROLLER, log.topics[0])

    candidate = create_wildcat_market_candidate(1, log, CREATED_AT)
    assert candidate.address == MARKET
    assert candidate.created_block == 21_866_550
    assert create_wildcat_market_candidate(1, make_market_added_log(V1_CONTROLLER), CREATED_AT) is None

    log.topics = log.topics[:1]
    with pytest.raises(ValueError, match="controller topic"):
        create_wildcat_market_candidate(1, log, CREATED_AT)


def test_wildcat_direct_registry_lead() -> None:
    """A registry lead skips probing, survives older pickles and is exempt from activity filters."""
    candidate = create_wildcat_market_candidate(1, make_market_added_log(HOOKS_FACTORY), CREATED_AT)
    report = LeadScanReport()
    leads: dict = {}
    add_wildcat_market_candidate_lead(report, leads, candidate)
    assert report.new_leads == 1
    lead = leads[MARKET]
    assert lead.is_candidate()
    addresses, _, factory_lead_count = _prepare_probe_leads(leads)
    assert addresses == []
    assert factory_lead_count == 1

    detection = create_wildcat_market_detection(candidate)
    assert detection.features == {ERC4626Feature.wildcat_like}
    assert detection.first_seen_at_block == 21_866_550
    assert is_activity_filter_exempt(detection)

    # An existing generic lead gains the candidate without a new lead count
    generic = PotentialVaultMatch(chain=1, address=MARKET, first_seen_at_block=1, first_seen_at=CREATED_AT, deposit_count=3)
    report = LeadScanReport()
    leads = {MARKET: generic}
    add_wildcat_market_candidate_lead(report, leads, candidate)
    assert report.new_leads == 0
    assert generic.wildcat_market_candidate == candidate
    assert generic.deposit_count == 3

    # Older pickled leads do not have the slot
    old = PotentialVaultMatch(chain=1, address=MARKET, first_seen_at_block=1, first_seen_at=CREATED_AT)
    del old.wildcat_market_candidate
    assert not old.is_candidate()
    assert _prepare_probe_leads({MARKET: old})[0] == [MARKET]


def test_wildcat_lending_metrics_exclude_withdrawn_cash() -> None:
    """Unclaimed withdrawals and protocol fees are not lender liquidity.

    Lender supply is 1,100. Cash of 300 includes 150 of paid but unclaimed
    withdrawals and 20 of protocol fees, so 130 belongs to current lenders and
    970 is lent out. 110 of older unpaid batches reduces only the available
    liquidity.
    """
    state = make_state(normalized_unclaimed_withdrawals=150, accrued_protocol_fees=20, scaled_pending_withdrawals=100)
    assert state.total_supply == 1_100
    assert state.max_deposit == 900
    available, utilisation = state.calculate_lending_metrics(cash=300)
    assert available == 20
    assert utilisation == pytest.approx(970 / 1_100)

    # Pending withdrawals larger than cash do not pin utilisation at 100%
    available, utilisation = make_state(scaled_pending_withdrawals=900).calculate_lending_metrics(cash=500)
    assert available == 0
    assert utilisation == pytest.approx(600 / 1_100)

    assert make_state(is_closed=True).max_deposit == 0
    assert make_state(scaled_total_supply=0).calculate_lending_metrics(cash=10) == (10, None)


def test_wildcat_reads_at_most_daily() -> None:
    """Hourly cadences are slowed to daily; weekly cadences are kept."""
    state = WildcatMarketReaderState(WildcatMarket(Web3(), VaultSpec(1, MARKET)))
    assert state.get_frequency() == ("unverified_tvl", datetime.timedelta(days=1))

    state.first_read_at = datetime.datetime(2025, 1, 1)
    state.last_call_at = datetime.datetime(2026, 1, 1)
    state.last_tvl = Decimal(1_000_000)
    assert state.get_frequency() == ("large_tvl", datetime.timedelta(days=1))
    state.last_tvl = Decimal(10)
    assert state.get_frequency() == ("tiny_tvl", datetime.timedelta(days=7))
