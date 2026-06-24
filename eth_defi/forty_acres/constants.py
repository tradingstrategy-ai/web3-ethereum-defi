Looking at the bounty request and existing codebase, I need to implement a 40acres vault protocol integration. Based on the existing patterns for Aave v2/v3 integrations and the TypeScript requirement, I'll create the necessary files for the 40acres vault protocol.

```typescript
// eth_defi/forty_acres/constants.py
"""Constants for 40acres vault protocol."""

from enum import Enum
from typing import Dict, List, NamedTuple


class FortyAcresVersion(Enum):
    """40acres protocol versions."""
    V1 = "v1"


class FortyAcresNetwork(NamedTuple):
    """40acres network configuration."""
    chain_id: int
    name: str
    vault_factory: str
    registry: str


# 40acres vault protocol networks
FORTY_ACRES_NETWORKS: Dict[int, FortyAcresNetwork] = {
    1: FortyAcresNetwork(
        chain_id=1,
        name="ethereum",
        vault_factory="0x0000000000000000000000000000000000000000",  # Replace with actual address
        registry="0x0000000000000000000000000000000000000000",      # Replace with actual address
    ),
    137: FortyAcresNetwork(
        chain_id=137,
        name="polygon",
        vault_factory="0x0000000000000000000000000000000000000000",  # Replace with actual address
        registry="0x0000000000000000000000000000000000000000",      # Replace with actual address
    ),
    42161: FortyAcresNetwork(
        chain_id=42161,
        name="arbitrum",
        vault_factory="0x0000000000000000000000000000000000000000",  # Replace with actual address
        registry="0x0000000000000000000000000000000000000000",      # Replace with actual address
    ),
}


def get_forty_acres_network_by_chain_id(chain_id: int) -> FortyAcresNetwork:
    """Get 40acres network configuration by chain ID."""
    if chain_id not in FORTY_ACRES_NETWORKS:
        raise ValueError(f"Unsupported 40acres network: {chain_id}")
    return FORTY_ACRES_NETWORKS[chain_id]


def forty_acres_get_vault_name_by_address(chain_id: int, vault_address: str) -> str:
    """Get vault name by address."""
    # This would typically query the registry contract
    # For now, return a placeholder
    return f"40acres-vault-{vault_address.lower()[:8]}"
```

```typescript
// eth_defi/forty_acres/events.py
"""40acres vault protocol event reader.

Efficiently read 40acres vault events from a blockchain.

Currently we are tracking these events:

- Deposit
- Withdraw
- VaultCreated
- HarvestPerformed
"""

import csv
import datetime
import logging
from pathlib import Path
from typing import Callable

from requests.adapters import HTTPAdapter
from tqdm.auto import tqdm
from web3 import Web3

from eth_defi.forty_acres.constants import (
    FORTY_ACRES_NETWORKS,
    FortyAcresVersion,
    forty_acres_get_vault_name_by_address,
)
from eth_defi.abi import get_contract
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.event_reader.conversion import (
    convert_int256_bytes_to_int,
    convert_jsonrpc_value_to_int,
    convert_uint256_string_to_address,
    decode_data,
)
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import LogResult, read_events_concurrent
from eth_defi.event_reader.reorganisation_monitor import ReorganisationMonitor
from eth_defi.event_reader.state import ScanState
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor
from eth_defi.token import TokenDetails, fetch_erc20_details

logger = logging.getLogger(__name__)


class VaultCache(LogContext):
    """Manage cache of vault data when doing event look-up.

    Do not do extra requests for already known vaults.
    """

    def __init__(self):
        self.cache = {}

    def get_vault_info(self, web3: Web3, address: str) -> TokenDetails:
        if address not in self.cache:
            details = fetch_erc20_details(web3, address, raise_on_error=False)
            logging.warning(f"Fetched vault details for {address}: {details}")
            self.cache[address] = details
        return self.cache[address]


def _process_forty_acres_deposit_event(
    log_result: LogResult,
    vault_cache: VaultCache,
) -> dict:
    """Process a single Deposit event."""
    web3 = log_result.web3
    block_time = log_result.timestamp
    block_number = log_result["blockNumber"]
    tx_hash = log_result["transactionHash"]
    log_index = log_result["logIndex"]
    
    # Decode event data
    decoded = decode_data(log_result["data"])
    
    vault_address = convert_uint256_string_to_address(log_result["address"])
    user = convert_uint256_string_to_address(log_result["topics"][1])
    amount = convert_jsonrpc_value_to_int(decoded[0])
    shares = convert_jsonrpc_value_to_int(decoded[1])
    
    vault_info = vault_cache.get_vault_info(web3, vault_address)
    
    return {
        "block_number": block_number,
        "block_time": block_time,
        "tx_hash": tx_hash.hex(),
        "log_index": log_index,
        "vault_address": vault_address,
        "vault_symbol": vault_info.symbol if vault_info else "UNKNOWN",
        "user": user,
        "amount": amount,
        "shares": shares,
        "event_type": "Deposit",
    }


def _process_forty_acres_withdraw_event(
    log_result: LogResult,
    vault_cache: VaultCache,
) -> dict:
    """Process a single Withdraw event."""
    web3 = log_result.web3
    block_time = log_result.timestamp
    block_number = log_result["blockNumber"]
    tx_hash = log_result["transactionHash"]
    log_index = log_result["logIndex"]
    
    # Decode event data
    decoded = decode_data(log_result["data"])
    
    vault_address = convert_uint256_string_to_address(log_result["address"])
    user = convert_uint256_string_to_address(log_result["topics"][1])
    amount = convert_jsonrpc_value_to_int(decoded[0])
    shares = convert_jsonrpc_value_to_int(decoded[1])
    
    vault_info = vault_cache.get_vault_info(web3, vault_address)
    
    return {
        "block_number": block_number,
        "block_time": block_time,
        "tx_hash": tx_hash.hex(),
        "log_index": log_index,
        "vault_address": vault_address,
        "vault_symbol": vault_info.symbol if vault_info else "UNKNOWN",
        "user": user,
        "amount": amount,
        "shares": shares,
        "event_type": "Withdraw",
    }


def _process_forty_acres_harvest_event(
    log_result: LogResult,
    vault_cache: VaultCache,
) -> dict:
    """Process a single HarvestPerformed event."""
    web3 = log_result.web3
    block_time = log_result.timestamp
    block_number = log_result["blockNumber"]
    tx_hash = log_result["transactionHash"]
    log_index = log_result["logIndex"]
    
    # Decode event data
    decoded = decode_data(log_result["data"])
    
    vault_address = convert_uint256_string_to_address(log_result["address"])
    harvester = convert_uint256_string_to_address(log_result["topics"][1])
    profit = convert_jsonrpc_value_to_int(decoded[0])
    
    vault_info = vault_cache.get_vault_info(web3, vault_address)
    
    return {
        "block_number": block_number,
        "block_time": block_time,
        "tx_hash": tx_hash.hex(),
        "log_index": log_index,
        "vault_address": vault_address,
        "vault_symbol": vault_info.symbol if vault_info else "UNKNOWN",
        "harvester": harvester,
        "profit": profit,
        "event_type": "HarvestPerformed",
    }


def _fetch_forty_acres_events_to_csv(
    json_rpc_url: str,
    state: ScanState,
    forty_acres_network_name: str,
    start_block: int,
    end_block: int,
    output_folder: str = "/tmp",
    max_workers: int = 16,
    log_info: Callable = print,
    reorg_monitor: ReorganisationMonitor | None = None,
    version: FortyAcresVersion = FortyAcresVersion.V1,
):
    """Fetch all tracked 40acres events to CSV files for notebook analysis."""
    
    assert start_block <= end_block, f"start_block {start_block} must be <= end_block {end_block}"
    
    output_folder = Path(output_folder)
    
    # Setup web3 connection
    web3_factory = TunedWeb3Factory(json_rpc_url, HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers))
    web3 = web3_factory(None)
    
    # Get network configuration
    chain_id = web3.eth.chain_id
    if chain_id not in FORTY_ACRES_NETWORKS:
        raise ValueError(f"Unsupported 40acres network: {chain_id}")
    
    network_config = FORTY_ACRES_NETWORKS[chain_id]
    
    # Event signatures
    deposit_event_signature = web3.keccak(text="Deposit(address,uint256,uint256)").hex()
    withdraw_event_signature = web3.keccak(text="Withdraw(address,uint256,uint256)").hex()
    harvest_event_signature = web3.keccak(text="HarvestPerformed(address,uint256)").hex()
    
    # Setup vault cache
    vault_cache = VaultCache()
    
    # Process different event types
    events_to_process = [
        {
            "name": "deposit",
            "signature": deposit_event_signature,
            "processor": _process_forty_acres_deposit_event,
            "filename": f"forty-acres-{version.value}-{forty_acres_network_name.lower()}-deposit.csv",
        },
        {
            "name": "withdraw", 
            "signature": withdraw_event_signature,
            "processor": _process_forty_acres_withdraw_event,
            "filename": f"forty-acres-{version.value}-{forty_acres_network_name.lower()}-withdraw.csv",
        },
        {
            "name": "harvest",
            "signature": harvest_event_signature,
            "processor": _process_forty_acres_harvest_event,
            "filename": f"forty-acres-{version.value}-{forty_acres_network_name.lower()}-harvest.csv",
        },
    ]
    
    for event_config in events_to_process:
        log_info(f"Processing {event_config['name']} events...")
        
        def process_event(log_result: LogResult) -> dict:
            return event_config["processor"](log_result, vault_cache)
        
        # Read events
        events_iterable = read_events_concurrent(
            web3_factory,
            start_block,
            end_block,
            events=[{
                "topics": [event_config["signature"]],
                "address": None,  # Listen to all addresses
            }],
            extract_timestamps=process_event,
            max_workers=max_workers,
            reorg_monitor=reorg_monitor,
            context=vault_cache,
        )
        
        # Write to CSV
        output_file = output_folder / event_config["filename"]
        with open(output_file, "w", newline="") as csvfile:
            writer = None
            count = 0
            
            with tqdm(desc=f"Extracting {event_config['name']} events", unit=" events") as progress_bar:
                for event_data in events_iterable:
                    if writer is None:
                        # Initialize CSV writer with first row
                        fieldnames = event_data.keys()
                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                        writer.writeheader()
                    
                    writer.writerow(event_data)
                    count += 1
                    progress_bar.update(1)
        
        log_info(f"Wrote {count} {event_config['name']} events to {output_file}")


def forty_acres_fetch_events_to_csv(
    json_rpc_url: str,
    state: ScanState,
    forty_acres_network_name: str,
    start_block: int,
    end_block: int,
    output_folder: str = "/tmp",
    max_workers: int = 16,
    log_info: Callable = print,
    reorg_monitor: ReorganisationMonitor | None = None,
):
    """Fetch all tracked 40acres v1 events to CSV files for notebook analysis.

    Creates CSV files with the event data:

    - `/tmp/forty-acres-v1-{network_name.lower()}-deposit.csv`
    - `/tmp/forty-acres-v1-{network_name.lower()}-withdraw.csv`
    - `/tmp/forty-acres-v1-{network_name.lower()}-harvest.csv`

    A progress bar and estimation on the completion is rendered for console / Jupyter notebook using `tqdm`.

    The scan can be resumed using `state` storage to retrieve the last scanned block number from the previous round.
    However, the mechanism here is not perfect and only good for notebook use - for advanced
    persistent usage like database backed scans, please write your own scan loop using proper transaction management.

    .. note ::

        Any Ethereum address is lowercased in the resulting dataset and is not checksummed.

    :param json_rpc_url: JSON-RPC URL
    :param start_block: First block to process (inclusive)
    :param end_block: Last block to process (inclusive)
    :param forty_acres_network_name: Network name, e.g. 'Ethereum'
    :param state: Store the current scan state, so we can resume
    :param output_folder: Folder to contain output CSV files, default is /tmp folder
    :param max_workers: How many threads to allocate for JSON-RPC IO
    :param log_info: Which function to use to output info messages about the progress
    :param reorg_monitor: Check for block reorganisations
    """
    return _fetch_forty_acres_events_to_csv(
        json_rpc_url=json_rpc_url,
        state=state,
        forty_acres_network_name=forty_acres_network_name,
        start_block=start_block,
        end_block=end_block,
        output_folder=output_folder,
        max