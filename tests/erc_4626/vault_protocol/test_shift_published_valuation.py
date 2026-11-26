"""Replay the ltLLP stale-quote episode through the historical price reader."""

from decimal import Decimal

import pytest
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault
from eth_defi.event_reader.multicall_batcher import EncodedCallResult
from eth_defi.token import TokenDetails
from eth_defi.vault.base import VaultSpec

LAST_FRESH_BLOCK = 527443920
INCIDENT_SAMPLE_BLOCK = 527445008
PUBLISHED = 1795611611
QUOTE_FRESHNESS = 4200
SUPPLY = 244389801066482356659602
LTLLP = "0x956bdd9c18b786b082fd50c52722d254f0cb6964"


@pytest.fixture
def published_vault(monkeypatch: pytest.MonkeyPatch) -> ShiftVault:
    """Bind the verified feed and token interface without a network call."""
    web3 = Web3()
    vault = ShiftVault(web3, VaultSpec(42161, LTLLP), default_block_identifier=INCIDENT_SAMPLE_BLOCK)
    feed = get_deployed_contract(web3, "shift/ShiftTvlFeed.json", "0x4062c539D09A76D79e366F8C2a2e2F987EAD1FE0")
    monkeypatch.setattr(ShiftVault, "tvl_feed", feed)
    monkeypatch.setattr(ShiftVault, "tvl_feed_decimals", 6)
    monkeypatch.setattr(ShiftVault, "share_token", TokenDetails(vault.shift_contract, decimals=18))
    return vault


@pytest.mark.parametrize(
    ("block", "sampled", "fields", "expected_price", "expected_nav"),
    [
        pytest.param(LAST_FRESH_BLOCK, 1795615811, (262645912431, PUBLISHED, SUPPLY), "1.074700", "262645.912431", id="before-expiry"),
        pytest.param(INCIDENT_SAMPLE_BLOCK, 1795616098, (262645912431, PUBLISHED, SUPPLY), "1.074700", "262645.912431", id="incident"),
        pytest.param(527469821, 1795622412, (262736192149, 1795622412, SUPPLY), "1.075070", "262736.192149", id="recovery"),
        pytest.param(INCIDENT_SAMPLE_BLOCK, 1795616098, (0, PUBLISHED, SUPPLY), "0", "0", id="genuine-zero"),
    ],
)
def test_shift_published_valuation(
    published_vault: ShiftVault,
    monkeypatch: pytest.MonkeyPatch,
    block: int,
    sampled: int,
    *,
    fields: tuple[int, int, int],
    expected_price: str,
    expected_nav: str,
) -> None:
    """Use the published quote throughout the ltLLP incident and recovery."""
    monkeypatch.setattr(ShiftVault, "get_management_fee", lambda _self, _block: 0.0)
    monkeypatch.setattr(ShiftVault, "get_performance_fee", lambda _self, _block: 0.1)
    reader = published_vault.get_historical_reader(stateful=False)
    calls = list(reader.construct_multicalls())
    timestamp = native_datetime_utc_fromtimestamp(sampled)
    payload = published_vault.web3.codec.encode(["uint256", "uint256", "uint256"], fields)
    # A post-publication mint must not change the denominator of the quote.
    live_supply = SUPPLY + 10**18 if block == LAST_FRESH_BLOCK else SUPPLY
    rows = [EncodedCallResult(calls[0], True, payload, block, timestamp), EncodedCallResult(calls[1], True, live_supply.to_bytes(32), block, timestamp)]
    result = reader.process_result(block, timestamp, rows)
    assert result.errors is None
    assert result.share_price == Decimal(expected_price)
    assert result.total_assets == Decimal(expected_nav)
    assert result.total_supply == Decimal(live_supply) / Decimal(10**18)
    assert result.nav_timestamp == native_datetime_utc_fromtimestamp(fields[1])
    assert result.deposits_open is None
    assert result.redemption_open is None


def test_shift_scan_record_keeps_quote_age_and_availability(published_vault: ShiftVault, monkeypatch: pytest.MonkeyPatch) -> None:
    """Export the incident's original quote separately from vault availability."""

    def get_block(block_identifier: int) -> dict[str, int]:
        assert block_identifier == INCIDENT_SAMPLE_BLOCK
        return {"number": block_identifier, "timestamp": 1795616098}

    def call(function: ContractFunction, *, block_identifier: int | str = "latest") -> int | bool | tuple[int, int, int]:
        match function.fn_name:
            case "freshness":
                assert block_identifier == INCIDENT_SAMPLE_BLOCK
                return QUOTE_FRESHNESS
            case "getLastTvl":
                assert block_identifier == INCIDENT_SAMPLE_BLOCK
                return 262645912431, PUBLISHED, SUPPLY
            case "paused" | "whitelistEnabled":
                return False
        raise AssertionError(function.fn_name)

    monkeypatch.setattr(published_vault.web3.eth, "get_block", get_block)
    monkeypatch.setattr(ContractFunction, "call", call)
    record = published_vault.fetch_scan_record_extra_data()
    assert record["_shift_nav_freshness_seconds"] == QUOTE_FRESHNESS
    assert record["_nav_timestamp"] == native_datetime_utc_fromtimestamp(PUBLISHED)
    assert record["_deposit_closed_reason"] is None
    assert record["_redemption_closed_reason"] is None
