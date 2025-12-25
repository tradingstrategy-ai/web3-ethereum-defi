"""Aave v2 event reader.

Efficiently read Aave v2 from a blockchain.

Currently we are tracking these events:

- ReserveDataUpdated
"""

import logging
from typing import Callable

from eth_defi.aave_v3.constants import AaveVersion
from eth_defi.aave_v3.events import _fetch_aave_events_to_csv
from eth_defi.event_reader.reorganisation_monitor import ReorganisationMonitor
from eth_defi.event_reader.state import ScanState

logger = logging.getLogger(__name__)


def aave_v2_fetch_events_to_csv(
    json_rpc_url: str,
    state: ScanState,
    aave_network_name: str,
    start_block: int,
    end_block: int,
    output_folder: str = "/tmp",
    max_workers: int = 16,
    log_info: Callable = print,
    reorg_monitor: ReorganisationMonitor | None = None,
):
    """Fetch all tracked Aave v2 events to CSV files for notebook analysis.

    Creates a CSV file with the event data:

    - `/tmp/aave-v2-{aave_network_name.lower()}-reservedataupdated.csv`

    A progress bar and estimation on the completion is rendered for console / Jupyter notebook using `tqdm`.

    The scan be resumed using `state` storage to retrieve the last scanned block number from the previous round.
    However, the mechanism here is no perfect and only good for notebook use - for advanced
    persistent usage like database backed scans, please write your own scan loop using proper transaction management.

    .. note ::

        Any Ethereum address is lowercased in the resulting dataset and is not checksummed.

    :param json_rpc_url: JSON-RPC URL
    :param start_block: First block to process (inclusive), default is block xxx (when Aave v2 xxx was created on mainnet)
    :param end_block: Last block to process (inclusive), default is block xxx (1000 block after default start block)
    :param aave_network_name: Network name, e.g. 'Polygon'
    :param state: Store the current scan state, so we can resume
    :param output_folder: Folder to contain output CSV files, default is /tmp folder
    :param max_workers:
        How many threads to allocate for JSON-RPC IO.
        You can increase your EVM node output a bit by making a lot of parallel requests,
        until you exhaust your nodes IO capacity. Experiement with different values
        and see how your node performs.
    :param log_info: Which function to use to output info messages about the progress
    """

    return _fetch_aave_events_to_csv(
        json_rpc_url=json_rpc_url,
        state=state,
        aave_network_name=aave_network_name,
        start_block=start_block,
        end_block=end_block,
        output_folder=output_folder,
        max_workers=max_workers,
        log_info=log_info,
        reorg_monitor=reorg_monitor,
        aave_version=AaveVersion.V2,
    )
