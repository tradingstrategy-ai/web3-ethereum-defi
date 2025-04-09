"""
GMX Events Module

This module provides functionality for monitoring GMX protocol events through the EventEmitter contract.
All GMX events are emitted through this contract with specific event names, making it easy to
monitor protocol activity.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Set, Union

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import LogTopicError
from web3.types import FilterParams, LogReceipt

from cherry_core import ingest
from cherry_etl import config as cc, run_pipeline

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import GMX_EVENT_EMITTER_ADDRESS, GMX_EVENT_EMITTER_ABI


# Set up logger
logger = logging.getLogger(__name__)


class GMXEventListener:
    """Base event listener interface for GMX events."""

    def __init__(self, event_name: str, callback: Callable[[Dict[str, Any]], None]):
        """
        Initialize a GMX event listener.

        Args:
            event_name: Name of the GMX event to listen for
            callback: Function to call when the event is detected
        """
        self.event_name = event_name
        self.callback = callback
        self.filter = None

    async def process_events(self, logs: List[LogReceipt]) -> None:
        """Process event logs and call the callback for each matching event."""
        raise NotImplementedError("Subclasses must implement this method")


class GMXEvents:
    """
    Event monitoring functionality for GMX protocol.

    This class allows monitoring GMX events that are emitted through the EventEmitter contract.
    Events can be filtered by name and processed through callback functions.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize events module.

        Args:
            config: GMX configuration object
        """
        self.config = config
        self.web3 = config.web3
        self.chain = config.get_chain()

        # Get event emitter contract
        self.event_emitter_address = GMX_EVENT_EMITTER_ADDRESS[self.chain]
        self.event_emitter = self.web3.eth.contract(address=Web3.to_checksum_address(self.event_emitter_address), abi=GMX_EVENT_EMITTER_ABI[self.chain])

        # Track running state
        self.running = False
        self.listener_thread = None
        self.event_listeners: Dict[str, GMXEventListener] = {}
        self.processed_events: Set[str] = set()
        self.poll_interval = 2  # seconds

    def subscribe_to_event(self, event_name: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        Subscribe to a specific GMX event.

        Args:
            event_name: Name of the event to subscribe to (e.g., "PositionIncrease")
            callback: Function to call when event is detected
        """
        listener = DirectGMXEventListener(event_name, callback, self.event_emitter)
        self.event_listeners[event_name] = listener

        logger.info(f"Subscribed to GMX event: {event_name}")

    def subscribe_to_events(self, event_names: List[str], callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        Subscribe to multiple GMX events with the same callback.

        Args:
            event_names: List of event names to subscribe to
            callback: Function to call when any of these events is detected
        """
        for event_name in event_names:
            self.subscribe_to_event(event_name, callback)

    async def _fetch_logs(self, from_block: int, to_block: int) -> List[LogReceipt]:
        """Fetch logs from the blockchain."""
        filter_params = FilterParams(address=self.event_emitter_address, fromBlock=from_block, toBlock=to_block)

        try:
            return self.web3.eth.get_logs(filter_params)
        except Exception as e:
            logger.error(f"Error fetching logs: {e}")
            return []

    def get_event_id(self, log: LogReceipt) -> str:
        """Generate unique ID for an event log."""
        return f"{log['blockHash'].hex()}-{log['transactionHash'].hex()}-{log['logIndex']}"

    async def start_listening(self, poll_interval: int = 2) -> None:
        """
        Start listening for subscribed events.

        Args:
            poll_interval: Seconds between checks for new events
        """
        if self.running:
            logger.warning("Event listener is already running")
            return

        self.running = True
        self.poll_interval = poll_interval

        # Start listening thread
        self.listener_thread = asyncio.create_task(self._listener_loop())
        logger.info(f"Started GMX event listener with poll interval {poll_interval}s")

    async def _listener_loop(self) -> None:
        """Main event listening loop."""
        last_block = self.web3.eth.block_number

        while self.running:
            current_block = self.web3.eth.block_number

            if current_block > last_block:
                logger.debug(f"Checking for events in blocks {last_block+1} to {current_block}")

                # Fetch logs for the new blocks
                logs = await self._fetch_logs(last_block + 1, current_block)

                # Process logs through listeners
                for log in logs:
                    event_id = self.get_event_id(log)

                    # Skip if already processed
                    if event_id in self.processed_events:
                        continue

                    # Mark as processed
                    self.processed_events.add(event_id)

                    # Process through each listener
                    for listener in self.event_listeners.values():
                        try:
                            await listener.process_log(log)
                        except Exception as e:
                            logger.error(f"Error processing log with {listener.event_name}: {e}")

                last_block = current_block

            await asyncio.sleep(self.poll_interval)

    async def stop_listening(self) -> None:
        """Stop listening for events."""
        if not self.running:
            logger.warning("Event listener is not running")
            return

        self.running = False

        if self.listener_thread:
            self.listener_thread.cancel()
            try:
                await self.listener_thread
            except asyncio.CancelledError:
                pass
            self.listener_thread = None

        logger.info("Stopped GMX event listener")

    def clear_processed_events(self) -> None:
        """Clear the list of processed events."""
        self.processed_events.clear()


class DirectGMXEventListener(GMXEventListener):
    """Event listener implementation that directly monitors GMX events."""

    def __init__(self, event_name: str, callback: Callable[[Dict[str, Any]], None], event_emitter: Contract):
        """
        Initialize the direct GMX event listener.

        Args:
            event_name: Name of the GMX event to monitor
            callback: Function to call when event is detected
            event_emitter: EventEmitter contract
        """
        super().__init__(event_name, callback)
        self.event_emitter = event_emitter

    async def process_log(self, log: LogReceipt) -> None:
        """
        Process a single log entry.

        Args:
            log: Log receipt from the blockchain
        """
        try:
            # Try to decode the event
            event_dict = self._decode_event_log(log)

            # Check if this is the event we're looking for
            if event_dict and self._is_target_event(event_dict):
                logger.info(f"Found {self.event_name} event")

                # Format the event data for the callback
                formatted_event = self._format_event_data(event_dict)

                # Call the callback
                self.callback(formatted_event)
        except Exception as e:
            logger.error(f"Error processing log: {e}")

    def _decode_event_log(self, log: LogReceipt) -> Optional[Dict[str, Any]]:
        """Decode a log entry into an event dictionary."""
        try:
            # Try each event type
            for event_type in ["EventLog", "EventLog1", "EventLog2"]:
                try:
                    return self.event_emitter.events[event_type]().process_receipt({"logs": [log]})[0]
                except (LogTopicError, IndexError):
                    continue

            return None
        except Exception as e:
            logger.debug(f"Could not decode log as EventEmitter event: {e}")
            return None

    def _is_target_event(self, event_dict: Dict[str, Any]) -> bool:
        """Check if the decoded event matches our target event name."""
        try:
            return event_dict["args"]["eventName"] == self.event_name
        except (KeyError, TypeError):
            return False

    def _format_event_data(self, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Format the event data for the callback."""
        result = {"event": self.event_name, "transaction_hash": event_dict.get("transactionHash", "").hex(), "block_number": event_dict.get("blockNumber", 0), "log_index": event_dict.get("logIndex", 0), "timestamp": None, "sender": event_dict["args"].get("msgSender", ""), "data": {}}  # Will be populated by event monitor

        # Extract event data
        try:
            event_data = event_dict["args"].get("eventData", {})
            if "addressItems" in event_data:
                for item in event_data["addressItems"]:
                    result["data"][item.get("key", "")] = item.get("value", "")

            if "uintItems" in event_data:
                for item in event_data["uintItems"]:
                    result["data"][item.get("key", "")] = item.get("value", 0)

            if "intItems" in event_data:
                for item in event_data["intItems"]:
                    result["data"][item.get("key", "")] = item.get("value", 0)

            if "boolItems" in event_data:
                for item in event_data["boolItems"]:
                    result["data"][item.get("key", "")] = item.get("value", False)

            if "bytes32Items" in event_data:
                for item in event_data["bytes32Items"]:
                    result["data"][item.get("key", "")] = item.get("value", "").hex()

            if "stringItems" in event_data:
                for item in event_data["stringItems"]:
                    result["data"][item.get("key", "")] = item.get("value", "")
        except Exception as e:
            logger.error(f"Error formatting event data: {e}")

        return result


class GMXEventPipeline:
    """
    Pipeline for processing GMX events using Cherry ETL.

    This provides an alternative way to process events using the Cherry ETL pipeline,
    which can be useful for batch processing or historical analysis.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize the GMX event pipeline.

        Args:
            config: GMX configuration object
        """
        self.config = config
        self.chain = config.get_chain()
        self.event_emitter_address = GMX_EVENT_EMITTER_ADDRESS[self.chain]

    async def create_event_pipeline(
        self,
        writer: cc.Writer,
        event_names: List[str] = None,
        from_block: int = 0,
        to_block: Optional[int] = None,
    ) -> cc.Pipeline:
        """
        Create a pipeline for processing GMX events.

        Args:
            writer: Cherry ETL writer configuration
            event_names: List of event names to filter for (optional)
            from_block: Starting block number
            to_block: Ending block number (optional)

        Returns:
            Configured pipeline object
        """
        # Create provider config
        provider = ingest.ProviderConfig(
            kind=ingest.ProviderKind.HYPERSYNC if self.chain == "arbitrum" else ingest.ProviderKind.SQD,
            url=f"https://{self.chain.lower()}.hypersync.xyz" if self.chain == "arbitrum" else f"https://portal.sqd.dev/datasets/{self.chain.lower()}-mainnet",
        )

        # Create query
        query = ingest.Query(
            kind=ingest.QueryKind.EVM,
            params=ingest.evm.Query(
                from_block=from_block,
                to_block=to_block,
                include_all_blocks=True,
                logs=[
                    ingest.evm.LogRequest(
                        address=[self.event_emitter_address],
                        include_blocks=True,
                        include_transactions=True,
                    )
                ],
                fields=ingest.evm.Fields(
                    block=ingest.evm.BlockFields(number=True, timestamp=True),
                    transaction=ingest.evm.TransactionFields(
                        hash=True,
                        from_=True,
                        to=True,
                    ),
                    log=ingest.evm.LogFields(
                        block_number=True,
                        transaction_hash=True,
                        log_index=True,
                        address=True,
                        data=True,
                        topic0=True,
                        topic1=True,
                        topic2=True,
                        topic3=True,
                    ),
                ),
            ),
        )

        # Create pipeline
        pipeline = cc.Pipeline(
            provider=provider,
            query=query,
            writer=writer,
            steps=[
                # Custom step to process GMX events
                cc.Step(
                    kind=cc.StepKind.CUSTOM,
                    config=cc.CustomStepConfig(
                        runner=self._process_gmx_events,
                        context={"event_names": event_names},
                    ),
                ),
                # Hex encode binary fields
                cc.Step(
                    kind=cc.StepKind.HEX_ENCODE,
                    config=cc.HexEncodeConfig(),
                ),
            ],
        )

        return pipeline

    def _process_gmx_events(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process GMX events from raw logs.

        Args:
            data: Dictionary containing logs and related data
            context: Context including event names to filter for

        Returns:
            Dictionary with processed GMX events
        """
        import polars as pl

        logs = data["logs"]
        blocks = data["blocks"]
        transactions = data["transactions"]

        # Join logs with blocks and transactions
        logs_with_metadata = logs.join(
            blocks.select(
                pl.col("number").alias("block_number"),
                pl.col("timestamp").alias("block_timestamp"),
            ),
            on="block_number",
        ).join(
            transactions.select(
                pl.col("hash").alias("transaction_hash"),
                pl.col("from").alias("tx_from"),
                pl.col("to").alias("tx_to"),
            ),
            on="transaction_hash",
        )

        # TODO: Decode event data
        # This would involve processing the EventLog/EventLog1/EventLog2 data
        # and extracting the eventName and eventData fields

        return {"gmx_events": logs_with_metadata}


# Example usage:
async def example_monitor_position_events(web3: Web3):
    """Example of monitoring GMX position events."""
    config = GMXConfig(web3, chain="arbitrum")

    # Create events monitor
    events = GMXEvents(config)

    # Define callback function
    def handle_position_event(event):
        print(f"Position event detected: {event['event']}")
        print(f"Transaction: {event['transaction_hash']}")
        print(f"Data: {json.dumps(event['data'], indent=2)}")

    # Subscribe to position events
    events.subscribe_to_events(
        [
            "EventLog2",
            # "PositionDecrease",
            # "LiquidatePosition"
        ],
        handle_position_event,
    )

    # Start listening
    await events.start_listening(poll_interval=5)

    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        # Stop listening when interrupted
        await events.stop_listening()


async def main():
    from web3 import Web3
    import asyncio
    from eth_defi.gmx.config import GMXConfig
    import os
    from dotenv import load_dotenv

    load_dotenv()
    rpc_url = os.environ["AVALANCHE"]
    # Initialize Web3 with your provider
    web3 = Web3(Web3.HTTPProvider(rpc_url))

    # Create GMX configuration
    config = GMXConfig(web3, chain="arbitrum")

    # Initialize GMX events monitor
    events = GMXEvents(config)

    # Define event handler
    def handle_position_event(event):
        print(f"New position event: {event['event']}")
        print(f"Transaction: {event['transaction_hash']}")
        print(f"Size: {event['data'].get('sizeDelta', 0)}")

    # Subscribe to events
    events.subscribe_to_event("EventLog2", handle_position_event)
    # events.subscribe_to_event("PositionDecrease", handle_position_event)

    # Start listening
    await events.start_listening()

    try:
        # Run until interrupted
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await events.stop_listening()


if __name__ == "__main__":
    asyncio.run(main())
