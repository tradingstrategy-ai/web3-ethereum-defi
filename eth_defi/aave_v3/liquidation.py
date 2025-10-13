"""Liquidation event fetch for Aave

- Download Aave liquidations data using HyperSync
"""

import asyncio
import datetime
import logging
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import AsyncIterable

from web3 import Web3
import hypersync

from eth_typing import HexAddress

from hexbytes import HexBytes

from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.event_reader.conversion import convert_uint256_bytes_to_address
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_defi.utils import addr

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AaveLiquidationEvent:
    """Aave liquidation event with resolved token details and human token amounts.

    - Includes Aave v3 compatibles like Spark
    """

    chain_id: int
    chain_name: str
    #: Contract address, which is positing the event, tells us the protocol: Spark, Aave v3, etc. and other Aave v3 compatibles
    contract: HexAddress
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

    def __post_init__(self):
        assert isinstance(self.timestamp, datetime.datetime)

    def as_row(self) -> dict:
        """Convert the event to a dictionary with Pandsa row serializable values."""
        result = {}
        for field in fields(self):
            value = getattr(self, field.name)
            match value:
                case Decimal():
                    value = float(value)
                case TokenDetails():
                    value = value.symbol
            result[field.name] = value
        return result


class AaveLiquidationReader:
    def __init__(
        self,
        client: hypersync.HypersyncClient,
        web3: Web3,
        hypersync_read_timeout: float = 90,
    ):
        assert isinstance(client, hypersync.HypersyncClient), f"Expected HypersyncClient, got {type(client)}"
        assert isinstance(web3, Web3), f"Expected Web3, got {type(web3)}"
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
            # Deal with scam deployments
            raise_on_error=False,
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
            logs=[hypersync.LogSelection(topics=[["0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"]])],
            field_selection=hypersync.FieldSelection(block=["number", "timestamp"], log=["block_number", "block_hash", "transaction_hash", "address", "log_index", "data", "topic0", "topic1", "topic2", "topic3"]),
        )

    def decode_event(
        self,
        chain_id: int,
        chain_name: str,
        log: hypersync.Log,
        block_lookup: dict,
    ) -> AaveLiquidationEvent:
        """Decode liquidation event from the log data.

        - Convert raw addresses and amounts to more manageable format
        """
        assert log.topics is not None
        assert len(log.topics) == 4

        # Decode the indexed parameters (topics)

        topics = log.topics
        assert len(topics) == 4

        collateral_asset = convert_uint256_bytes_to_address(HexBytes(topics[1]))
        debt_asset = convert_uint256_bytes_to_address(HexBytes(topics[2]))
        user = convert_uint256_bytes_to_address(HexBytes(topics[3]))

        colleratal_asset_details = self.resolve_token(collateral_asset)
        debt_asset_details = self.resolve_token(debt_asset)

        # Decode the data parameters (non-indexed)
        decoded = self.web3.codec.decode(["uint256", "uint256", "address", "bool"], bytes.fromhex(log.data[2:]))

        debt_to_cover = decoded[0]
        liquidated_collateral_amount = decoded[1]
        liquidator = addr(decoded[2])

        debt_to_cover_decimal = debt_asset_details.convert_to_decimals(debt_to_cover)
        liquidated_collateral_amount_decimal = colleratal_asset_details.convert_to_decimals(liquidated_collateral_amount)

        block = block_lookup[log.block_number]
        timestamp = native_datetime_utc_fromtimestamp(int(block.timestamp, 16))

        return AaveLiquidationEvent(
            chain_id,
            chain_name,
            contract=log.address,
            block_number=log.block_number,
            block_hash=log.block_hash,
            timestamp=timestamp,
            transaction_hash=log.transaction_hash,
            log_index=log.log_index,
            collateral_asset=colleratal_asset_details,
            debt_asset=debt_asset_details,
            user=user,
            debt_to_cover=debt_to_cover_decimal,
            liquidated_collateral_amount=liquidated_collateral_amount_decimal,
            liquidator=liquidator,
            receive_a_token=decoded[3],
        )

    async def fetch_liquidations_async(
        self,
        start_block: int,
        end_block: int,
        display_progress=True,
    ) -> AsyncIterable[AaveLiquidationEvent]:
        """Identify smart contracts emitting 4626 like events.

        - Scan all event matches using HyperSync

        - See stream() example here: https://github.com/enviodev/hypersync-client-python/blob/main/examples/all-erc20-transfers.py
        """
        assert end_block >= start_block

        hypersync_chain = await self.client.get_chain_id()
        assert hypersync_chain == self.web3.eth.chain_id, f"Hypersync client chain does not match Web3 chain: {hypersync_chain} != {self.web3.eth.chain_id}"

        chain_id = self.web3.eth.chain_id
        chain_name = get_chain_name(chain_id)

        logger.info("Building HyperSync query")
        query = self.build_query(start_block, end_block)

        logger.info(f"Starting HyperSync stream {start_block:,} to {end_block:,}, chain {chain_name}, query is {query}")
        # start the stream
        receiver = await self.client.stream(query, hypersync.StreamConfig())

        if display_progress:
            progress_bar = tqdm(
                total=end_block - start_block,
                desc=f"Reading Aave liquidations data on {chain_name}: {start_block:,} - {end_block:,}",
            )
        else:
            progress_bar = None

        last_block = start_block
        timestamp = None

        logger.info(f"Streaming HyperSync")

        last_synced = None

        matches = 0

        while True:
            try:
                res = await asyncio.wait_for(receiver.recv(), timeout=self.hypersync_read_timeout)
            except asyncio.TimeoutError as e:
                raise RuntimeError(f"Hypersync stream() read timeout after {self.hypersync_read_timeout} seconds - currently this is unrecoverable TODO") from e

            # exit if the stream finished
            if res is None:
                break

            current_block = res.next_block

            block_lookup = {b.number: b for b in res.data.blocks}
            batch_last_block = res.data.blocks[-1] if res.data.blocks else None
            if batch_last_block:
                timestamp = native_datetime_utc_fromtimestamp(int(batch_last_block.timestamp, 16))

            if res.data.logs:
                log: hypersync.Log
                for log in res.data.logs:
                    event = self.decode_event(
                        chain_id,
                        chain_name,
                        log,
                        block_lookup,
                    )
                    yield event
                    matches += 1

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

        logger.info(f"HyperSync sees {last_synced} as the last block on chain {chain_name}")

        if progress_bar is not None:
            progress_bar.close()

    def fetch_liquidations(
        self,
        start_block: int,
        end_block: int,
    ) -> list[AaveLiquidationEvent]:
        async def _inner():
            return [e async for e in self.fetch_liquidations_async(start_block, end_block)]

        return asyncio.run(_inner())
