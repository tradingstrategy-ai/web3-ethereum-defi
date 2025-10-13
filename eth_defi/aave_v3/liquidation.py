"""Liquidation event fetch for Aave

- Download Aave liquidations data using HyperSync
"""
import asyncio
import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import AsyncIterable

from web3 import Web3

from eth_defi import hypersync
from eth_typing import HexAddress

from hexbytes import HexBytes

from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.token import fetch_erc20_details, TokenDetails

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AaveLiquidationEvent:
    """Aave liquidation event with resolved token details and human token amounts."""
    block_number: int
    block_hash: HexBytes
    timestamp: datetime.datetime
    transaction_hash: HexAddress
    log_index: int
    collateral_asset: TokenDetails
    debt_asset: TokenDetails
    user: str
    debt_to_cover: Decimal
    liquidated_collateral_amount: Decimal
    liquidator: HexAddress
    receive_a_token: bool


class AaveLiquidationEventFetcher:

    def __init__(
        self,
        client: hypersync.Client,
        web3: Web3,
        hypersync_read_timeout: float = 90,
    ):
        self.client = client
        self.web3 = web3
        self.hypersync_read_timeout = hypersync_read_timeout

    def resolve_token(self, address: HexAddress) -> TokenDetails:
        """Resolve token address to checksum address.

        -  Cached
        """
        return fetch_erc20_details(
            self.web3,
            address,
        )

    def build_query(
        self,
        start_block: int,
        end_block: int,
    ):
        """Create liquidation event query.

        The event in the question:

        .. code-block:: solidity

            // Event Signature
            // 0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286

            LiquidationCall(
                address collateralAsset,
                address debtAsset,
                address user,
                uint256 debtToCover,
                uint256 liquidatedCollateralAmount,
                address liquidator,
                bool receiveAToken
            )

        See also:

        - https://www.quicknode.com/sample-app-library/ethereum-aave-liquidation-tracker
        """

        return hypersync.Query(
            from_block=start_block,
            to_block=end_block,
            logs=[
                hypersync.LogSelection(
                    topics=[
                        ["0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"]
                    ]
                )
            ],
            field_selection=hypersync.FieldSelection(
                block=["number", "timestamp"],
                log=["block_number", "block_hash", "transaction_hash", "log_index", "data", "topic0", "topic1", "topic2", "topic3"]
            ),
        )

    def decode_event(self, log: hypersync.Log, block_timestamps: dict) -> AaveLiquidationEvent:
        """Decode liquidation event from the log data."""
        assert log.topics is not None
        assert len(log.topics) == 4

        # Decode the indexed parameters (topics)
        collateral_asset = self.web3.to_checksum_address("0x" + log.topic1[-40:])
        debt_asset = self.web3.to_checksum_address("0x" + log.topic2[-40:])
        user = self.web3.to_checksum_address("0x" + log.topic3[-40:])

        colleratal_asset_details = self.resolve_token(collateral_asset)
        debt_asset_details = self.resolve_token(debt_asset)

        # Decode the data parameters (non-indexed)
        decoded = self.web3.codec.decode(
            ["uint256", "uint256", "address", "bool"],
            bytes.fromhex(log.data[2:])
        )

        yield AaveLiquidationEvent(
            block_number=log.block_number,
            block_hash=log.block_hash,
            timestamp=block_timestamps[log.block_number],
            transaction_hash=log.transaction_hash,
            log_index=log.log_index,
            collateral_asset=colleratal_asset_details,
            debt_asset=debt_asset_details,
            user=user,
            debt_to_cover=decoded[0],
            liquidated_collateral_amount=decoded[1],
            liquidator=self.web3.to_checksum_address(decoded[2]),
            receive_a_token=decoded[3],
        )

    async def scan_liquidations(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> AsyncIterable[AaveLiquidationEvent]:
        """Identify smart contracts emitting 4626 like events.

        - Scan all event matches using HyperSync

        - See stream() example here: https://github.com/enviodev/hypersync-client-python/blob/main/examples/all-erc20-transfers.py
        """
        assert end_block > start_block

        chain = self.web3.eth.chain_id

        logger.info("Building HyperSync query")
        query = self.build_query(start_block, end_block)

        logger.info(f"Starting HyperSync stream {start_block:,} to {end_block:,}, chain {chain}, query is {query}")
        # start the stream
        receiver = await self.client.stream(query, hypersync.StreamConfig())

        if display_progress:
            chain_name = get_chain_name(self.web3.eth.chain_id)
            progress_bar = tqdm(
                total=end_block - start_block,
                desc=f"Reading Aave liquiationh data on {chain_name}",
            )
        else:
            progress_bar = None

        last_block = start_block
        timestamp = None

        logger.info(f"Streaming HyperSync")

        last_synced = None

        matches = 0
        seen = set()

        while True:
            try:
                res = await asyncio.wait_for(receiver.recv(), timeout=self.recv_timeout)
            except asyncio.TimeoutError as e:
                raise RuntimeError(f"Hypersync stream() read timeout after {self.recv_timeout} seconds - currently this is unrecoverable TODO") from e

            # exit if the stream finished
            if res is None:
                break

            current_block = res.next_block

            if res.data.logs:
                block_lookup = {b.number: b for b in res.data.blocks}
                log: hypersync.Log
                for log in res.data.logs:
                    event = self.decode_event(log, block_lookup)
                    yield event

            last_synced = res.archive_height

            if progress_bar is not None:
                progress_bar.update(current_block - last_block)
                last_block = current_block


                # Add extra data to the progress bar
                if timestamp is not None:
                    progress_bar.set_postfix(
                        {
                            "At": timestamp,
                            "Liquidations": f"{matches:,}",
                        }
                    )

        logger.info(f"HyperSync sees {last_synced} as the last block")

        if progress_bar is not None:
            progress_bar.close()

