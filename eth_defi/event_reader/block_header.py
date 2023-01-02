"""Block header data.

Structures and helpers to maintain block header data.
"""

import random
from dataclasses import dataclass
from typing import Dict, Optional, TypeAlias

#: 32 bit uint UNIX UTC timestamp of the block
#:
#: TODO: We might need float because high throughput chains like
#: Solana have subsecond timestamps
from eth_typing import HexStr

Timestamp: TypeAlias = int


@dataclass(slots=True, frozen=True)
class BlockHeader:
    """Describe block headers for a single block.

    - This data is used to check chain reorganisations

    - This data is used to store block number -> block timestamp map
      and resolve trades to their UTC time
    """

    #: 32-bit uint of he block number
    block_number: int

    #: Block hash as 0x prefixed string
    #:
    #: Note that this might be converted to binary data later.
    block_hash: str

    #: 32 bit uint UNIX UTC timestamp of the block
    #:
    #: TODO: We might need float because high throughput chains like
    #: Solana have subsecond timestamps
    timestamp: Timestamp

    def __post_init__(self):
        assert type(self.block_number) == int
        assert type(self.block_hash) == str, f"Got {type(self.block_hash)}"
        assert type(self.timestamp) == int
        assert self.block_hash.startswith("0x")

    @staticmethod
    def generate_headers(count: int, start_block: int = 1, start_time: float = 0, blocks_per_second: float = 12) -> Dict[str, list]:
        """Generate random block header data.

        Used for testing.


        :return:
            DataFrame indexed by block number
        """

        # Columnar data
        block_number = []
        block_hash = []
        timestamp = []

        clock = start_time
        for i in range(start_block, start_block + count):
            block_number.append(i)
            block_hash.append(hex(random.randint(2**31, 2**32)))
            timestamp.append(int(clock))
            clock += blocks_per_second

        return {
            "block_number": block_number,
            "block_hash": block_hash,
            "timestamp": timestamp,
        }

    @staticmethod
    def to_pandas(headers: Dict[str, list], partition_size: Optional[int] = None):
        """Convert columnar header data to Pandas.

        .. note ::

            Depends on Pandas, but because we have optional dependency do a lazy import.

        :param headers:
            Raw block data to write.

        :param partition_size:
            Create a key "partition" which each contains partitioning_size blocks.
            E.g. 100_000.

        :return:
        """
        # Optional dependency
        import pandas as pd

        # https://stackoverflow.com/a/64537577/315168
        df = pd.DataFrame.from_dict(headers, orient="columns")
        df.set_index(df["block_number"], inplace=True, drop=False)
        if partition_size:
            assert partition_size > 0
            # First partition starts at 1, not 0
            df["partition"] = df["block_number"].apply(lambda x: max((x // partition_size) * partition_size, 1))
        return df

    @staticmethod
    def from_pandas(df) -> Dict[int, "BlockHeader"]:
        """Decode saved Pandas input."""
        # Optional dependency
        import pandas as pd

        assert isinstance(df, pd.DataFrame)
        map = {}
        for idx, row in df.iterrows():
            record = BlockHeader(block_number=row.block_number, block_hash=row.block_hash, timestamp=row.timestamp)
            map[record.block_number] = record
        return map
