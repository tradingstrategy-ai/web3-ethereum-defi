"""Unit tests for GMX historical liquidity-provider observations."""

import asyncio

import pytest
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.gmx import historical_oracle
from eth_defi.gmx.events import GMXEventData
from eth_defi.gmx.historical_oracle import GMX_DEPOSIT_ACTION_TYPE, GMX_EVENT_LOG1_DATA_TYPES, decode_historical_share_price_event_data, encode_address_as_event_topic, extract_historical_share_price_observation, fetch_historical_share_price_observations_hypersync

TOKEN = to_checksum_address("0x1111111111111111111111111111111111111111")
PROVIDER = to_checksum_address("0x2222222222222222222222222222222222222222")
SOURCE_TIMESTAMP = 1_700_000_000


def test_historical_fetch_reuses_supplied_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep successive native Hypersync streams on one caller-owned loop."""

    event_loops: list[asyncio.AbstractEventLoop] = []

    async def fetch_observations(**_kwargs: object) -> list:
        await asyncio.sleep(0)
        event_loops.append(asyncio.get_running_loop())
        return []

    monkeypatch.setattr(historical_oracle, "_fetch_historical_share_price_observations_hypersync_async", fetch_observations)
    arguments = {
        "hypersync_client": object(),
        "web3": Web3(),
        "chain_id": 42161,
        "event_emitter_address": TOKEN,
        "start_block": 1,
        "end_block": 2,
    }
    with asyncio.Runner() as event_loop_runner:
        fetch_historical_share_price_observations_hypersync(**arguments, event_loop_runner=event_loop_runner)
        fetch_historical_share_price_observations_hypersync(**arguments, event_loop_runner=event_loop_runner)

    assert event_loops[0] is event_loops[1]


def test_historical_fetch_closes_native_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release native stream buffers deterministically after end-of-stream."""

    class Receiver:
        """Minimal native-stream substitute."""

        closed = False

        async def recv(self) -> None:  # noqa: PLR6301
            """Return end-of-stream immediately."""

            await asyncio.sleep(0)

        def close(self) -> None:
            """Record deterministic resource release."""

            self.closed = True

    receiver = Receiver()

    async def open_stream(*_args: object) -> Receiver:
        """Return the controlled stream substitute."""

        await asyncio.sleep(0)
        return receiver

    monkeypatch.setattr(historical_oracle, "open_hypersync_stream", open_stream)
    observations = fetch_historical_share_price_observations_hypersync(
        hypersync_client=object(),
        web3=Web3(),
        chain_id=42161,
        event_emitter_address=TOKEN,
        start_block=1,
        end_block=2,
    )

    assert observations == []
    assert receiver.closed


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
        ([("actionType", GMX_DEPOSIT_ACTION_TYPE)], []),
        empty_items,
        empty_items,
    )
    web3 = Web3()
    data = web3.codec.encode(GMX_EVENT_LOG1_DATA_TYPES, (PROVIDER, "MarketPoolValueUpdated", event_data))

    decoded = decode_historical_share_price_event_data(web3, data, encode_address_as_event_topic(TOKEN))

    assert decoded.address_items == {"market": TOKEN}
    assert decoded.uint_items == {"marketTokensSupply": 100 * 10**18}
    assert decoded.int_items == {"poolValue": 250 * 10**30}
    assert decoded.bytes32_items == {"actionType": GMX_DEPOSIT_ACTION_TYPE}


@pytest.mark.parametrize(
    ("event", "expected_product"),
    [
        (GMXEventData(event_name="MarketPoolValueUpdated", address_items={"market": TOKEN}, uint_items={"marketTokensSupply": 100 * 10**18}, int_items={"poolValue": 250 * 10**30}, bytes32_items={"actionType": GMX_DEPOSIT_ACTION_TYPE}), TOKEN),
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


def test_ignore_gm_withdrawal_valuation_context() -> None:
    """Do not mix GMX's withdrawal-specific PnL context into the GM curve."""

    event = GMXEventData(
        event_name="MarketPoolValueUpdated",
        address_items={"market": TOKEN},
        uint_items={"marketTokensSupply": 100 * 10**18},
        int_items={"poolValue": 250 * 10**30},
        bytes32_items={"actionType": b"\x00" * 32},
    )

    assert (
        extract_historical_share_price_observation(
            event,
            chain_id=42161,
            block_number=123,
            block_timestamp=SOURCE_TIMESTAMP,
            transaction_hash="0xabc",
            log_index=4,
        )
        is None
    )
