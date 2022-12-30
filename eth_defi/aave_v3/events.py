"""Aave v3 event reader.

Efficiently read Aave v3 from a blockchain.

Currently we are tracking these events:

- ReserveDataUpdated
"""
import csv
import datetime
import logging
from pathlib import Path

from requests.adapters import HTTPAdapter
from tqdm.auto import tqdm
from web3 import Web3

from eth_defi.aave_v3.constants import (
    AAVE_V3_NETWORKS,
    aave_v3_get_token_name_by_deposit_address,
)
from eth_defi.abi import get_contract
from eth_defi.event_reader.conversion import (
    convert_int256_bytes_to_int,
    convert_uint256_string_to_address,
    decode_data,
)
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import LogResult, read_events_concurrent
from eth_defi.event_reader.state import ScanState
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor
from eth_defi.token import TokenDetails, fetch_erc20_details

# from eth_defi.token import TokenDetails, fetch_erc20_details

logger = logging.getLogger(__name__)


class TokenCache(LogContext):
    """Manage cache of token data when doing ReserveDataUpdated look-up.

    Do not do extra requests for already known tokens.
    """

    def __init__(self):
        self.cache = {}

    def get_token_info(self, web3: Web3, address: str) -> TokenDetails:
        if address not in self.cache:
            details = fetch_erc20_details(web3, address, raise_on_error=False)
            logging.warn(f"Fetched ERC20 details for {address}: {details}")
            self.cache[address] = details
        return self.cache[address]


def get_event_mapping(web3: Web3) -> dict:
    """Returns tracked event types and mapping.

    Currently we are tracking these events:
        - ReserveDataUpdated(address indexed reserve, uint256 liquidityRate, uint256 stableBorrowRate, uint256 variableBorrowRate, uint256 liquidityIndex, uint256 variableBorrowIndex)
    """
    ReserveLogic = get_contract(web3, "aave_v3/ReserveLogic.json")

    return {
        "ReserveDataUpdated": {
            "contract_event": ReserveLogic.events.ReserveDataUpdated,
            "field_names": [
                "block_number",
                "timestamp",
                "tx_hash",
                "log_index",
                "token",
                "contract_address",
                "reserve",
                "liquidity_rate",
                "stable_borrow_rate",
                "variable_borrow_rate",
                "liquidity_index",
                "variable_borrow_index",
            ],
            "decode_function": decode_reserve_data_updated,
        },
    }


def _decode_base(log: LogResult) -> dict:
    block_time = datetime.datetime.utcfromtimestamp(log["timestamp"])

    return {
        "block_number": int(log["blockNumber"], 16),
        "timestamp": block_time.isoformat(),
        "tx_hash": log["transactionHash"],
        "log_index": int(log["logIndex"], 16),
    }


def decode_reserve_data_updated(aave_network_name: str, log: LogResult) -> dict:
    """Process a ReserveDataUpdated event. The event signature is:

    .. code-block::

        # topic0: signature 0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a
        event ReserveDataUpdated(
            address indexed reserve,    # topic1
            uint256 liquidityRate,      # data0
            uint256 stableBorrowRate,   # data1
            uint256 variableBorrowRate, # data2
            uint256 liquidityIndex,     # data3
            uint256 variableBorrowIndex # data4
        );
    """
    # Ensure the event comes from the correct smart contract
    if log["address"].lower() != AAVE_V3_NETWORKS[aave_network_name.lower()].pool_address.lower():
        return None

    # Do additional lookup for the token data
    # web3 = log["event"].web3
    # token_cache: TokenCache = log["context"]
    result = _decode_base(log)

    # Any indexed Solidity event parameter will be in topics data.
    # The first topics (0) is always the event signature.
    if len(log["topics"]) < 2:
        logging.warn(f'IGNORING EVENT: block={log["blockNumber"]} tx={log["transactionHash"]} topics={log["topics"]} data={log["data"]}')
        return None
    event_signature, reserve = log["topics"]
    deposit_address = convert_uint256_string_to_address(reserve)

    # Any non-indexed Solidity event parameter will be in the data section.
    # Chop data blob to byte32 entries
    liquidity_rate_raw, stable_borrow_rate_raw, variable_borrow_rate_raw, liquidity_index_raw, variable_borrow_index_raw = decode_data(log["data"])

    liquidity_rate = convert_int256_bytes_to_int(liquidity_rate_raw)
    stable_borrow_rate = convert_int256_bytes_to_int(stable_borrow_rate_raw)
    variable_borrow_rate = convert_int256_bytes_to_int(variable_borrow_rate_raw)
    liquidity_index = convert_int256_bytes_to_int(liquidity_index_raw)
    variable_borrow_index = convert_int256_bytes_to_int(variable_borrow_index_raw)

    result.update(
        {
            "reserve": deposit_address,
            "liquidity_rate": liquidity_rate,
            "stable_borrow_rate": stable_borrow_rate,
            "variable_borrow_rate": variable_borrow_rate,
            "liquidity_index": liquidity_index,
            "variable_borrow_index": variable_borrow_index,
            "contract_address": log["address"],
        }
    )

    # Detect token name from reserve address (None if not found)
    result["token"] = aave_v3_get_token_name_by_deposit_address(deposit_address)

    logger.debug(f'EVENT: block={log["blockNumber"]} tx={log["transactionHash"]} token={result["token"]} reserve={deposit_address} liquidity_rate={liquidity_rate} stable_borrow_rate={stable_borrow_rate} variable_borrow_rate={variable_borrow_rate} liquidity_index={liquidity_index} variable_borrow_index={variable_borrow_rate}')

    return result


def aave_v3_fetch_events_to_csv(
    json_rpc_url: str,
    state: ScanState,
    aave_network_name: str,
    start_block: int,
    end_block: int,
    output_folder: str = "/tmp",
    max_workers: int = 16,
    log_info=print,
):
    """Fetch all tracked Aave v3 events to CSV files for notebook analysis.

    Creates couple of CSV files with the event data:

    - `/tmp/aave-v3-{aave_network_name.lower()}-reservedataupdated.csv`

    A progress bar and estimation on the completion is rendered for console / Jupyter notebook using `tqdm`.

    The scan be resumed using `state` storage to retrieve the last scanned block number from the previous round.
    However, the mechanism here is no perfect and only good for notebook use - for advanced
    persistent usage like database backed scans, please write your own scan loop using proper transaction management.

    .. note ::

        Any Ethereum address is lowercased in the resulting dataset and is not checksummed.

    :param json_rpc_url: JSON-RPC URL
    :param start_block: First block to process (inclusive), default is block xxx (when Aave v3 xxx was created on mainnet)
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
    token_cache = TokenCache()
    http_adapter = HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
    web3_factory = TunedWeb3Factory(json_rpc_url, http_adapter)
    web3 = web3_factory(token_cache)
    executor = create_thread_pool_executor(web3_factory, token_cache, max_workers=max_workers)
    event_mapping = get_event_mapping(web3)
    contract_events = [event_data["contract_event"] for event_data in event_mapping.values()]

    # Start scanning
    restored, restored_start_block = state.restore_state(start_block)
    original_block_range = end_block - start_block

    if restored:
        log_info(f"Restored previous scan state, data until block {restored_start_block:,}, we are skipping {restored_start_block - start_block:,} blocks out of {original_block_range:,} total")
    else:
        log_info(
            f"No previous scan done, starting fresh from block {start_block:,}, total {original_block_range:,} blocks",
        )

    # Prepare local buffers and files.
    # Buffers is a context dictionary that is passed around
    # by the event scanner.
    buffers = {}

    for event_name, mapping in event_mapping.items():
        file_path = f"{output_folder}/aave-v3-{aave_network_name.lower()}-{event_name.lower()}.csv"
        exists_already = Path(file_path).exists()
        file_handler = open(file_path, "a")
        csv_writer = csv.DictWriter(file_handler, fieldnames=mapping["field_names"])
        if not exists_already:
            csv_writer.writeheader()

        # For each event, we have its own
        # counters and handlers in the context dictionary
        buffers[event_name] = {
            "buffer": [],
            "total": 0,
            "file_handler": file_handler,
            "csv_writer": csv_writer,
        }

    log_info(f"Scanning block range {restored_start_block:,} - {end_block:,}")
    with tqdm(total=end_block - restored_start_block) as progress_bar:
        #  1. update the progress bar
        #  2. save any events in the buffer in to a file in one go
        def update_progress(
            current_block,
            start_block,
            end_block,
            chunk_size: int,
            total_events: int,
            last_timestamp: int,
            context: TokenCache,
        ):
            nonlocal buffers

            if last_timestamp:
                # Display progress with the date information
                d = datetime.datetime.utcfromtimestamp(last_timestamp)
                formatted_time = d.strftime("%Y-%m-%d")
                progress_bar.set_description(f"Block: {current_block:,}, events: {total_events:,}, time:{formatted_time}")
            else:
                progress_bar.set_description(f"Block: {current_block:,}, events: {total_events:,}")

            progress_bar.update(chunk_size)

            # Update event specific contexes
            for buffer_data in buffers.values():
                buffer = buffer_data["buffer"]

                # log_info(f'Writing buffer to file {len(buffer)} events')
                # write events to csv
                for entry in buffer:
                    buffer_data["csv_writer"].writerow(entry)
                    buffer_data["total"] += 1

                # then reset buffer
                buffer_data["buffer"] = []

            # Sync the state of updated events
            state.save_state(current_block)

        # Read specified events in block range
        for log_result in read_events_concurrent(
            executor,
            restored_start_block,
            end_block,
            events=contract_events,
            notify=update_progress,
            chunk_size=100,
            context=token_cache,
        ):
            try:
                # write to correct buffer
                event_name = log_result["event"].event_name
                buffer = buffers[event_name]["buffer"]
                decode_function = event_mapping[event_name]["decode_function"]
                decoded_result = decode_function(aave_network_name, log_result)
                # Note: decoded_result is None if the event is e.g. from Aave v2 contract
                if decoded_result:
                    logger.debug(f"Adding event to buffer: {event_name}")
                    buffer.append(decoded_result)
            except Exception as e:
                raise RuntimeError(f"Could not decode {log_result}") from e

    # Write remaining events, close files and print stats
    for event_name, buffer in buffers.items():
        if len(buffer["buffer"]) > 0:
            for entry in buffer["buffer"]:
                buffer["csv_writer"].writerow(entry)
                buffer["total"] += 1
            buffer["buffer"] = []
        buffer["file_handler"].close()
    state.save_state(end_block)
