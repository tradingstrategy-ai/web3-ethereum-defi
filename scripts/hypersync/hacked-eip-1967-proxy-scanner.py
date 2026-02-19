"""Scan all supported HyperSync chains for potential breached EIP-1967 proxies.

- See the Twitter thread for details https://x.com/moo9000/status/1958473690997236025
- Resumable scanner that keeps scanning breaches proxies across all EVM chains
-

Some notes:

- Initialized() event may be used by some account abstracted wallets so we get a lot of events for some chains (Abstract
"""

import csv
import logging
import asyncio
import os
import pickle
import tempfile
from dataclasses import dataclass, field
import datetime
from pathlib import Path

import tabulate
from web3 import Web3

from eth_typing import HexAddress
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.hypersync.server import HYPERSYNC_SERVES
from eth_defi.utils import setup_console_logging

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from hypersync import BlockField, LogField
from hypersync import HypersyncClient


logger = logging.getLogger(__name__)

# Find different kind of Initialized() events
# https://github.com/search?q=%22event+Initialized%22+language%253ASolidity++proxy&type
# https://docs.openzeppelin.com/contracts/5.x/api/proxy#Initializable-Initialized-uint64-
INITIALIZED_SIGNATURES = [
    "Initialized(uint64)",
    "Initialized(uint8)",
]


@dataclass(slots=True)
class SpottedEvent:
    """One Initialized() event"""

    block_number: int
    block_timestamp: datetime.datetime
    tx_hash: str

    # TODO: Add from address


@dataclass(slots=True)
class Lead:
    """A lead is a potential EIP-1967 proxy contract breach."""

    chain_id: int
    chain_name: str

    #: The address of the lead contract
    address: HexAddress

    #: The block number where this lead was found
    signature: str
    events: list[SpottedEvent] = field(default_factory=list)

    def __repr__(self):
        return f"<Lead {self.address} at {self.events[0].block_timestamp}"

    def is_likely_breach(self, threshold: datetime.timedelta) -> bool:
        """We are a likely breach if the two earliest Initialised() event are quite close to each other."""

        events = self.events

        if len(events) < 2:
            return False

        seen_tx_hashes = set()
        filtered_events = []
        # Check for Initialized() twice in the same tx
        for event in events:
            if event.tx_hash in seen_tx_hashes:
                continue
            seen_tx_hashes.add(event.tx_hash)
            filtered_events.append(event)

        double_init_window = events[1].block_timestamp - events[0].block_timestamp
        return double_init_window <= threshold


@dataclass(slots=True)
class ChainState:
    """Store scanned last block and leads for a specific chain."""

    #: Which chain this state is for
    chain_id: int

    #: Last block we managed to scan
    next_scanned_block: int = 1

    #: Contract address -> Lead data mapping
    leads: dict[HexAddress, Lead] = field(default_factory=dict)

    def get_potential_breaches(self, threshold: datetime.timedelta, with_chain: bool) -> list[Lead]:
        """Get all leads that are likely breaches based on the threshold.

        :return:
            Human readable table
        """
        breaches = [lead for lead in self.leads.values() if lead.is_likely_breach(threshold)]

        data = []
        for breach in breaches:
            if with_chain:
                entry = {"Chain": breach.chain_name, "Chain id": breach.chain_id}
            else:
                entry = {}

            entry.update(
                {
                    "Address": breach.address,
                    "1st timestamp": breach.events[0].block_timestamp,
                    "2nd timestamp": breach.events[1].block_timestamp,
                    "1st tx": breach.events[0].tx_hash,
                }
            )
            data.append(entry)
        return data


@dataclass(slots=True)
class EVMWorldScanner:
    """Store the state for all chains."""

    chain_states: dict[int, ChainState] = field(default_factory=dict)


def create_update_query(chain_state: ChainState):
    """Create a query that updates the last scanned chain state."""

    sigs = [Web3.keccak(text=s).hex() for s in INITIALIZED_SIGNATURES]

    log_selections = [hypersync.LogSelection(topics=[[sig]]) for sig in sigs]

    # The query to run
    query = hypersync.Query(
        # start from block 0 and go to the end of the chain (we don't specify a toBlock).
        from_block=chain_state.next_scanned_block,
        # The logs we want. We will also automatically get transactions and blocks relating to these logs (the query implicitly joins them).
        logs=log_selections,
        # Select the fields we are interested in, notice topics are selected as topic0,1,2,3
        field_selection=hypersync.FieldSelection(
            block=[
                BlockField.NUMBER,
                BlockField.TIMESTAMP,
            ],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.ADDRESS,
                LogField.TRANSACTION_HASH,
                LogField.TOPIC0,
            ],
        ),
    )
    return query


async def refresh_chain(
    client: HypersyncClient,
    chain_state: ChainState,
    chain_name: str,
    double_init_threshold: datetime.timedelta,
    timeout=30.0,
) -> ChainState:
    """Run lead detection for on chain.

    - We discard results after the scan, except for those addresses where we got multiple events
    """

    start_block = chain_state.next_scanned_block
    end_block = await client.get_height()

    chain_id = await client.get_chain_id()

    progress_bar = tqdm(
        total=end_block - start_block,
        desc=f"Scanning potential EIP-1967 breaches on {chain_name} ({chain_id}), blocks {start_block:,} - {end_block:,}",
    )

    last_block = start_block
    timestamp = None

    query = create_update_query(chain_state)
    receiver = await client.stream(query, hypersync.StreamConfig())
    lead_count = 0
    interesting_lead_count = 0

    leads: dict[HexAddress, Lead] = {}

    while True:
        try:
            res = await asyncio.wait_for(receiver.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("HyperSync receiver timed out")
            break  # or handle as appropriate

        # exit if the stream finished
        if res is None:
            break

        current_block = res.next_block

        if res.data.logs:
            # HyperSync response has two parts:
            # Block: block timestamps
            # Logs: Logs in the query batch
            block_lookup = {b.number: b for b in res.data.blocks}
            log: hypersync.Log

            for log in res.data.logs:
                lead = leads.get(log.address)
                if lead is None:
                    lead = Lead(
                        signature=log.topics[0],
                        chain_id=chain_id,
                        chain_name=chain_name,
                        address=log.address,
                    )
                    leads[log.address] = lead
                    lead_count += 1

                block = block_lookup[log.block_number]
                timestamp = native_datetime_utc_fromtimestamp(int(block.timestamp, 16))

                evt = SpottedEvent(
                    block_number=log.block_number,
                    block_timestamp=timestamp,
                    tx_hash=log.transaction_hash,
                )
                lead.events.append(evt)

                if len(lead.events) >= 2:
                    if lead.is_likely_breach(double_init_threshold):
                        interesting_lead_count += 1

        if progress_bar is not None:
            progress_bar.update(current_block - last_block)
            last_block = current_block

            # Add extra data to the progress bar
            if timestamp is not None:
                progress_bar.set_postfix(
                    {
                        "At": timestamp,
                        "Contracts detected": f"{lead_count:,}",
                        "Double inits": f"{interesting_lead_count:,}",
                    }
                )

        last_synced = res.archive_height

    # Only save contracts that have seen at least two Initialized() events
    interesting_leads = {k: v for k, v in leads.items() if len(v.events) >= 2}

    logger.info("Total %d double init leads in this iteration", len(interesting_leads))

    chain_state.leads.update(interesting_leads)

    # Where will we start next time
    chain_state.next_scanned_block = last_synced

    logger.info(f"HyperSync saw {last_synced:,} as the last block for {chain_name}")

    if progress_bar is not None:
        progress_bar.close()

    return chain_state


def scan_all_chains(
    world_state: EVMWorldScanner,
    state_file: Path,
    double_init_threshold: datetime.timedelta,
    verbose=False,
):
    """Scan all supported HyperSync chains and update the state file"""
    supported_chains = HYPERSYNC_SERVES

    for chain_id, hypersync_url in supported_chains.items():
        chain_name = f"Chain {chain_id}"
        client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))

        chain_state = world_state.chain_states.get(chain_id, ChainState(chain_id=chain_id))

        # HyperSync client is written in Rust, and wants us to force to use async functions.
        # Encapsulate the madness here, we are not JavaScript.
        async def _hypersync_asyncio_wrapper() -> ChainState:
            return await refresh_chain(
                client,
                chain_state,
                chain_name,
                double_init_threshold,
            )

        world_state.chain_states[chain_id] = asyncio.run(_hypersync_asyncio_wrapper())

        breaches = chain_state.get_potential_breaches(double_init_threshold, with_chain=False)
        if breaches:
            if verbose:
                logger.info(f"Found {len(breaches)} potential breaches on {chain_name} chain")
                output = tabulate.tabulate(breaches, headers="keys", tablefmt="fancy_grid")
                logger.info("\n%s", output)
            else:
                logger.info("Total %d potential breaches detected", len(breaches))
        else:
            logger.info(f"No potential breaches found on {chain_name} chain.")

        # Because this is long running op, sync the state file between chains.
        # Do atomic replacement so CTRL+C does not mess the file.
        # Allows us to continue scanning where our computer shut down.
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=state_file.parent,
            suffix=".pickle",
            delete=False,
        ) as tmp:
            temp_fname = tmp.name
            with open(temp_fname, "wb") as f:
                pickle.dump(world_state, f)
            os.replace(temp_fname, state_file)


def main():
    """Entry point"""
    setup_console_logging(default_log_level="info")

    state_file = Path.home() / ".tradingstrategy" / "breached-eip-1967-proxy-scanner.pickle"
    state_file.parent.parent.mkdir(parents=True, exist_ok=True)

    csv_file = Path.home() / ".tradingstrategy" / "breached-eip-1967-proxy-scanner.csv"

    logger.info("Using state file %s", state_file.resolve())

    if state_file.exists():
        world_state = pickle.load(state_file.open("rb"))
    else:
        world_state = EVMWorldScanner()

    double_init_threshold = datetime.timedelta = datetime.timedelta(hours=1)

    scan_all_chains(world_state, state_file, double_init_threshold)

    # Dump CSV of all chains
    data = []
    for chain in world_state.chain_states.values():
        leads = chain.get_potential_breaches(double_init_threshold, with_chain=True)
        data += leads
    fieldnames = data[0].keys()
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(data)

    print(f"All ok, seeing total {len(data)} potential double inits across all chains currently")


if __name__ == "__main__":
    main()
