"""GMX custom evnet reader."""

import pytest

from eth_defi.gmx.onchain.event import query_gmx_events_async, EventLogType, query_gmx_events
from eth_defi.hypersync.server import get_hypersync_server

from hypersync import HypersyncClient, ClientConfig

from eth_defi.hypersync.timestamp import get_block_timestamps_using_hypersync, get_hypersync_block_height


@pytest.fixture()
def hypersync_client() -> HypersyncClient:
    hypersync_url = get_hypersync_server(42161)  # Arbitrum One
    client = HypersyncClient(ClientConfig(url=hypersync_url))
    return client


def test_gmx_event_reader(hypersync_client: HypersyncClient):
    """Extract some events from GMX v2 deployment."""

    # https://arbiscan.io/address/0xc8ee91a536674287db53897056e12d9819156d3822fb
    # https://www.codeslaw.app/contracts/arbitrum/0xc8ee91a54287db53897056e12d9819156d3822fb
    # https://www.codeslaw.app/contracts/arbitrum/0xc8ee91a54287db53897056e12d9819156d3822fb?tab=abi

    # deploy_block = 107_737_756
    # end_block = deploy_block + 10_000

    events = query_gmx_events(
        client=hypersync_client,
        start_block=380_891_095 - 10_000,
        end_block=380_891_095,
        gmx_event_name="PositionIncrease",
        log_type=EventLogType.EventLog1,
    )

    assert len(events) == 3667
