"""Unit tests for GMX historical liquidity-provider observations."""

import pytest
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.gmx.events import GMXEventData
from eth_defi.gmx.historical_oracle import GMX_EVENT_LOG1_DATA_TYPES, decode_historical_share_price_event_data, encode_address_as_event_topic, extract_historical_share_price_observation

TOKEN = to_checksum_address("0x1111111111111111111111111111111111111111")
PROVIDER = to_checksum_address("0x2222222222222222222222222222222222222222")
SOURCE_TIMESTAMP = 1_700_000_000


def test_encode_product_address_as_event_topic() -> None:
    """GMX product address filtering targets EventLog1's indexed topic."""

    assert encode_address_as_event_topic(TOKEN) == f"0x{'0' * 24}{TOKEN[2:].lower()}"


def test_decode_focused_historical_event_data() -> None:
    """Decode only the generic GMX item families needed by the equity curve."""

    empty_items = ([], [])
    event_data = (
        ([("market", TOKEN)], []),
        ([("marketTokensSupply", 100 * 10**18)], []),
        ([("poolValue", 250 * 10**30)], []),
        empty_items,
        empty_items,
        empty_items,
        empty_items,
    )
    web3 = Web3()
    data = web3.codec.encode(GMX_EVENT_LOG1_DATA_TYPES, (PROVIDER, "MarketPoolValueInfo", event_data))

    decoded = decode_historical_share_price_event_data(web3, data, encode_address_as_event_topic(TOKEN))

    assert decoded.address_items == {"market": TOKEN}
    assert decoded.uint_items == {"marketTokensSupply": 100 * 10**18}
    assert decoded.int_items == {"poolValue": 250 * 10**30}


@pytest.mark.parametrize(
    ("event", "expected_product"),
    [
        (GMXEventData(event_name="MarketPoolValueInfo", address_items={"market": TOKEN}, uint_items={"marketTokensSupply": 100 * 10**18}, int_items={"poolValue": 250 * 10**30}), TOKEN),
        (GMXEventData(event_name="GlvValueUpdated", address_items={"glv": TOKEN}, uint_items={"supply": 100 * 10**18, "value": 250 * 10**30}), TOKEN),
    ],
)
def test_extract_flow_neutral_share_price(event: GMXEventData, expected_product: str) -> None:
    """Derive the same USD share price from canonical GM and GLV value events."""

    observation = extract_historical_share_price_observation(
        event,
        chain_id=42161,
        block_number=123,
        block_timestamp=SOURCE_TIMESTAMP,
        transaction_hash="0xabc",
        log_index=4,
    )

    assert observation is not None
    assert observation.product_address == expected_product
    assert observation.total_assets == pytest.approx(250)
    assert observation.total_supply == pytest.approx(100)
    assert observation.share_price == pytest.approx(2.5)
