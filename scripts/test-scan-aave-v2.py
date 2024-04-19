"""A sample script to manually scan Aave v2 events.

.. code-block:: shell

    python scripts/test-scan-aave-v2.py

"""

import logging
import os

from web3 import HTTPProvider, Web3

from eth_defi.aave_v2.constants import get_aave_v2_network_by_chain_id
from eth_defi.aave_v2.events import aave_v2_fetch_events_to_csv
from eth_defi.event_reader.json_state import JSONFileScanState
from eth_defi.event_reader.reorganisation_monitor import create_reorganisation_monitor

logging.getLogger().setLevel(logging.INFO)

json_rpc_url = os.environ["JSON_RPC_URL"]
web3 = Web3(HTTPProvider(json_rpc_url))

aave_network = get_aave_v2_network_by_chain_id(web3.eth.chain_id)

print(f"Detected network {aave_network.name } chain {web3.eth.chain_id} start block {aave_network.pool_created_at_block}")

# Stores the last block number of event data we store
state = JSONFileScanState(f"/tmp/aave-v2-{aave_network.name.lower()}-scan.json")

reorg_monitor = create_reorganisation_monitor(web3, check_depth=5)
reorg_monitor.load_initial_block_headers(block_count=10_000)

# scan the last 10,000 blocks
end_block = reorg_monitor.get_last_block_live()
start_block = end_block - 10_000
max_workers = 4

aave_v2_fetch_events_to_csv(
    json_rpc_url,
    state,
    aave_network.name,
    start_block=start_block,
    end_block=end_block,
    max_workers=max_workers,
    reorg_monitor=reorg_monitor,
)
