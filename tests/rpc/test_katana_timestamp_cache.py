"""Live Katana regression check for repairing a sparse timestamp cache.

Run with the configured provider, without printing credentials::

    source .local-test.env && poetry run pytest tests/rpc/test_katana_timestamp_cache.py -q
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess_auto_backend
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.provider.rpcdb import RPCRequestStats


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("JSON_RPC_KATANA"), reason="Set JSON_RPC_KATANA to run the live sparse-cache check")
def test_live_katana_repairs_sampled_timestamp_gaps(tmp_path: Path) -> None:
    """Fetch exact historical headers below a populated cache's newest block.

    The real provider check uses an isolated cache and the operator's existing
    RPC configuration. A nearby header cannot replace the requested header;
    repeating the same sampling grid must make no further block requests.

    :param tmp_path: Isolated timestamp cache, separate from production state.
    :return: None; checks exact header clocks, persistent repair and warm-cache reuse.
    """
    chain_id = 747474
    factory = MultiProviderWeb3Factory(os.environ["JSON_RPC_KATANA"])
    web3 = factory()
    head = web3.eth.block_number
    start = head - 100
    cached_epochs = {block: web3.eth.get_block(block)["timestamp"] for block in (start + 1, head)}
    cache = BlockTimestampDatabase.create(chain_id, tmp_path)
    try:
        cache.import_chain_data(chain_id, pd.Series(cached_epochs))
    finally:
        cache.close()
    stats = RPCRequestStats()
    expected_header_calls = 3
    for _ in range(2):
        timestamps = fetch_block_timestamps_multiprocess_auto_backend(chain_id=chain_id, web3factory=factory, start_block=start, end_block=start + 20, step=10, cache_path=tmp_path, max_workers=2, display_progress=False, rpc_request_stats=stats)
        try:
            stored = timestamps.timestamp_db.query(start, start + 20)
            for block in range(start, start + 21, 10):
                assert block in stored.index
                assert timestamps[block] == native_datetime_utc_fromtimestamp(web3.eth.get_block(block)["timestamp"])
        finally:
            timestamps.close()
        calls, errors = stats.export()
        assert sum(count for (_, method), count in calls.items() if method == "eth_getBlockByNumber") == expected_header_calls
        assert not errors
